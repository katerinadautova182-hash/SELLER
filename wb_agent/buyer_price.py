"""Canonical Wildberries buyer-price reader.

This module intentionally does NOT use seller/club prices as a proxy for the
buyer storefront. It reads the public card API for the regional price after
WB platform discounts and applies the current public WB Wallet rule fetched
from WB's own payment configuration.

The result is the non-personal buyer price suitable for RRP monitoring. It is
not an account-specific checkout price: personal buyer discounts, promo codes,
loyalty points and account-specific Wallet status can still change checkout.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Iterable

CARD_API = "https://card.wb.ru/cards/v4/detail"
DEFAULT_PAYMENT_URL = (
    "https://static-basket-01.wbbasket.ru/vol1/global-payment/default-payment.json"
)
SETTINGS_URL = "https://static-basket-01.wbbasket.ru/vol0/data/settings-front.json"
DEFAULT_DEST = "-1257786"  # Moscow; override with WB_BUYER_DEST.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36"


class BuyerPriceUnavailable(RuntimeError):
    """Raised when a trustworthy current buyer price cannot be established."""


@dataclass(frozen=True)
class WalletConfig:
    discount_percent: Decimal
    max_price_rub: Decimal


@dataclass(frozen=True)
class BuyerPrice:
    nm_id: int
    supplier_id: int | None
    option_id: int | None
    public_price_rub: int
    wallet_price_rub: int
    product_price_rub: int
    logistics_rub: int
    wallet_discount_percent: str
    wallet_applied: bool
    source: str = "wb_card_v4_plus_public_wallet"

    def to_dict(self) -> dict:
        return asdict(self)


def _read_json(url: str, timeout: int = 25) -> dict:
    """Fetch JSON, falling back to system curl for WB anti-bot HTTP profiles."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Origin": "https://www.wildberries.ru",
            "Referer": "https://www.wildberries.ru/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise BuyerPriceUnavailable("WB returned non-object JSON")
        return payload
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 498):
            raise BuyerPriceUnavailable(f"WB HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise BuyerPriceUnavailable(f"WB request failed: {type(exc).__name__}") from None

    try:
        proc = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                str(timeout),
                "-A",
                USER_AGENT,
                "-H",
                "Accept: application/json",
                "-H",
                "Origin: https://www.wildberries.ru",
                "-H",
                "Referer: https://www.wildberries.ru/",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(proc.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise BuyerPriceUnavailable(
            f"WB public API blocked for urllib and curl: {type(exc).__name__}"
        ) from None
    if not isinstance(payload, dict):
        raise BuyerPriceUnavailable("WB curl fallback returned non-object JSON")
    return payload


def parse_wallet_config(default_payment: dict, settings: dict) -> WalletConfig:
    if default_payment.get("state") != 0:
        raise BuyerPriceUnavailable("WB Wallet config state is not active")

    wallet_row = None
    for row in default_payment.get("data") or []:
        if (
            isinstance(row, dict)
            and row.get("wc_type") == "Незалогиненный кошелёк"
            and row.get("is_active") is True
        ):
            wallet_row = row
            break
    if wallet_row is None:
        raise BuyerPriceUnavailable("Public WB Wallet discount is absent/inactive")

    try:
        discount = Decimal(str(wallet_row["discount_value"]))
        variables = settings["variables"]
        max_price = Decimal(str(variables["wlt1DiscountDisplayMaxPrice"]))
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise BuyerPriceUnavailable("WB Wallet config has unexpected schema") from exc

    if discount < 0 or discount >= 100 or max_price < 0:
        raise BuyerPriceUnavailable("WB Wallet config values are outside safe bounds")
    return WalletConfig(discount_percent=discount, max_price_rub=max_price)


def fetch_wallet_config() -> WalletConfig:
    return parse_wallet_config(_read_json(DEFAULT_PAYMENT_URL), _read_json(SETTINGS_URL))


def calculate_wallet_price(
    product_price_kopecks: int,
    logistics_kopecks: int,
    config: WalletConfig,
) -> tuple[int, int, int, bool]:
    """Return public, Wallet, product and logistics prices in whole RUB.

    WB's public Wallet rule is applied to the product component; logistics is
    added afterwards. WB storefront prices are displayed in whole rubles, so
    Wallet calculation follows the public implementations and floors to RUB.
    """
    if product_price_kopecks <= 0:
        raise BuyerPriceUnavailable("WB card returned non-positive product price")
    logistics_kopecks = max(0, int(logistics_kopecks or 0))

    product_rub = Decimal(product_price_kopecks) / Decimal(100)
    logistics_rub = Decimal(logistics_kopecks) / Decimal(100)
    public_total = product_rub + logistics_rub

    wallet_applied = (
        config.discount_percent > 0
        and (config.max_price_rub == 0 or product_rub <= config.max_price_rub)
    )
    wallet_product = product_rub
    if wallet_applied:
        wallet_product = (
            product_rub
            * (Decimal(100) - config.discount_percent)
            / Decimal(100)
        ).quantize(Decimal("1"), rounding=ROUND_FLOOR)

    public_rub = int(public_total.quantize(Decimal("1"), rounding=ROUND_FLOOR))
    wallet_rub = int(
        (wallet_product + logistics_rub).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    )
    product_whole_rub = int(product_rub.quantize(Decimal("1"), rounding=ROUND_FLOOR))
    logistics_whole_rub = int(
        logistics_rub.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    )
    return public_rub, wallet_rub, product_whole_rub, logistics_whole_rub, wallet_applied


def _stock_qty(size: dict) -> int:
    stocks = size.get("stocks")
    if not isinstance(stocks, list):
        return 0
    total = 0
    for stock in stocks:
        if isinstance(stock, dict):
            qty = stock.get("qty")
            if isinstance(qty, int) and qty > 0:
                total += qty
    return total


def parse_card_payload(payload: dict, config: WalletConfig) -> dict[int, BuyerPrice]:
    products = payload.get("products")
    if not isinstance(products, list):
        data = payload.get("data")
        products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(products, list):
        raise BuyerPriceUnavailable("WB card response has no products array")

    result: dict[int, BuyerPrice] = {}
    for product in products:
        if not isinstance(product, dict):
            continue
        raw_nm = product.get("id", product.get("nmID"))
        try:
            nm_id = int(raw_nm)
        except (TypeError, ValueError):
            continue

        supplier = product.get("supplierId")
        supplier_id = supplier if isinstance(supplier, int) and supplier > 0 else None
        candidates: list[BuyerPrice] = []

        for size in product.get("sizes") or []:
            if not isinstance(size, dict) or _stock_qty(size) <= 0:
                continue
            price_data = size.get("price")
            if not isinstance(price_data, dict):
                continue
            product_price = price_data.get("product")
            if not isinstance(product_price, int) or product_price <= 0:
                continue
            logistics = price_data.get("logistics")
            logistics = logistics if isinstance(logistics, int) and logistics > 0 else 0
            option = size.get("optionId")
            option_id = option if isinstance(option, int) and option > 0 else None

            public_rub, wallet_rub, product_rub, logistics_rub, applied = (
                calculate_wallet_price(product_price, logistics, config)
            )
            candidates.append(
                BuyerPrice(
                    nm_id=nm_id,
                    supplier_id=supplier_id,
                    option_id=option_id,
                    public_price_rub=public_rub,
                    wallet_price_rub=wallet_rub,
                    product_price_rub=product_rub,
                    logistics_rub=logistics_rub,
                    wallet_discount_percent=str(config.discount_percent),
                    wallet_applied=applied,
                )
            )

        if candidates:
            result[nm_id] = min(candidates, key=lambda row: row.wallet_price_rub)
    return result


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def fetch_buyer_prices(
    nm_ids: Iterable[int | str],
    *,
    dest: str | None = None,
    wallet_config: WalletConfig | None = None,
    batch_size: int = 25,
) -> dict[int, BuyerPrice]:
    ids = sorted({int(value) for value in nm_ids if str(value).strip().isdigit()})
    if not ids:
        return {}
    config = wallet_config or fetch_wallet_config()
    destination = (dest or os.getenv("WB_BUYER_DEST") or DEFAULT_DEST).strip()

    result: dict[int, BuyerPrice] = {}
    for batch in _chunks(ids, max(1, min(int(batch_size), 25))):
        params = urllib.parse.urlencode(
            {
                "appType": "1",
                "curr": "rub",
                "lang": "ru",
                "dest": destination,
                "nm": ";".join(map(str, batch)),
            },
            safe=";",
        )
        payload = _read_json(CARD_API + "?" + params)
        result.update(parse_card_payload(payload, config))
    return result
