"""One-time Yandex price correction with strict safety controls.

Red: raise seller price by missing RUB amount (RRP - onDisplay).
Yellow: same +3% seller-price rule used on Ozon.
Uses official report 'basicPrice' as the seller-price starting point. Never
mutates report 'onDisplay' directly. No second write in verification phase.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

from .client import YandexMarketClient
from .official_prices import obtain_report, number
from .price_check import _catalog, classify, load_rrp, resolve_rrp
from ozon_agent.business_rules import is_clearance_sku
from ozon_agent.telegram import send_telegram

MAX_TOTAL = 40


def candidates(items, rrp_map):
    planned, skipped = [], []
    seen = set()
    for item in items:
        sku = str(item.get("offerId") or "").strip()
        if not sku or sku in seen or is_clearance_sku(sku):
            continue
        seen.add(sku)
        buyer = number(item.get("onDisplay"))
        seller = number(item.get("basicPrice"))
        rrp, source = resolve_rrp(sku, rrp_map)
        if buyer is None or rrp is None:
            continue
        status, minimum = classify(buyer, rrp)
        if status not in ("RED", "YELLOW"):
            continue
        if seller is None:
            skipped.append({"sku": sku, "reason": "NO_SELLER_BASE_PRICE"})
            continue
        if status == "RED":
            delta = math.ceil(rrp - buyer)
        else:
            delta = math.ceil(seller * 0.03)
        if delta <= 0:
            continue
        new_value = math.ceil(seller + delta)
        row = {"sku": sku, "status": status, "buyer_before": buyer,
               "seller_before": seller, "seller_after": new_value,
               "rrp": rrp, "minimum": minimum, "delta": new_value - seller,
               "rrp_source": source}
        planned.append(row)
    if len(planned) > MAX_TOTAL:
        raise RuntimeError("Unexpectedly large one-shot repricing batch")
    return planned, skipped


def apply_one_shot(client, business_id, changes):
    # Use the official business-level price endpoint; no batches bigger than 40.
    offers = [{"offerId": x["sku"], "price": {
        "value": x["seller_after"], "currencyId": "RUR"
    }} for x in changes]
    if not offers:
        return
    client.request("POST", f"/v2/businesses/{business_id}/offer-prices/updates",
                   payload={"offers": offers})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/yandex-one-shot-reprice.json")
    args = parser.parse_args()
    if os.getenv("YANDEX_ONE_SHOT_APPROVED") != "YES":
        raise RuntimeError("Price changes disabled; explicit one-shot approval missing.")
    rrp_map = load_rrp()
    client = YandexMarketClient()
    catalog, _, _ = _catalog(client)
    businesses = sorted(set(int(x["business_id"]) for x in catalog))
    if len(businesses) != 1:
        raise RuntimeError("Expected one Yandex business; aborting without writes")
    business_id = businesses[0]
    before = obtain_report(client, business_id)
    planned, skipped = candidates(before, rrp_map)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    state = {"business_id": business_id, "planned": planned,
             "skipped": skipped, "applied": False}
    output.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    if not planned:
        print("No eligible changes. Skipped:", len(skipped))
        return 0
    # An audit artifact is recorded before attempting writes.
    apply_one_shot(client, business_id, planned)
    state["applied"] = True
    output.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"Sent {len(planned)} price updates; waiting for published prices.")
    # Yandex report-generation throttle: >= 120 seconds between requests.
    time.sleep(150)
    try:
        after = obtain_report(client, business_id)
        new_buyer = {str(x.get("offerId")): number(x.get("onDisplay")) for x in after}
        for row in planned:
            val = new_buyer.get(row["sku"])
            row["buyer_after"] = val
            if val is not None:
                row["result"] = classify(val, row["rrp"])[0]
            else:
                row["result"] = "NOT_YET_AVAILABLE"
    except Exception as exc:
        state["verification_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
    output.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    lines = ["Яндекс Маркет — разовая корректировка цен",
             f"Запрошено изменений: {len(planned)}; пропущено по ограничениям: {len(skipped)}"]
    for row in planned:
        price_after = row.get("buyer_after")
        buyer = "нет данных" if price_after is None else f"{price_after:,.0f}".replace(",", " ") + " ₽"
        lines.append(f"• {row['sku']} [{row['status']}]: продавец "
                     f"{row['seller_before']:,.0f} → {row['seller_after']:,.0f} ₽; "
                     f"витрина {row['buyer_before']:,.0f} → {buyer}; "
                     f"после: {row.get('result','НЕ ПРОВЕРЕНО')}")
    if skipped:
        lines.append("Пропущены: " + ", ".join(x["sku"] for x in skipped))
    if state.get("verification_error"):
        lines.append("Повторная проверка: " + state["verification_error"])
    message = "\n".join(lines)
    print(message)
    # Keep full data in artifact; split Telegram messages below 4096 chars.
    block = ""
    for line in lines:
        if len(block) + len(line) + 1 > 3700 and block:
            send_telegram(block)
            block = ""
        block += line + "\n"
    if block:
        send_telegram(block)
    return 0 if not state.get("verification_error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
