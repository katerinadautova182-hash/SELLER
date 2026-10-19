"""Two-pass Yandex repricing for the temporary Mark Shmidt promotion."""
from __future__ import annotations

import argparse
import json
import os
import time
from math import ceil
from pathlib import Path

from ozon_agent.price_alert import load_rrp_map
from ozon_agent.promo_policy import (
    PROMO_END,
    is_promo_offer,
    moscow_date,
    promo_active,
    regular_rrp,
    target_buyer_price,
)
from ozon_agent.telegram import send_telegram
from .client import YandexMarketClient
from .official_prices import obtain_report, number
from .one_shot_reprice import apply_one_shot
from .price_check import _catalog

COOLDOWN_SEC = 155
TOLERANCE_RUB = 10.0


def _mode_allowed(mode: str) -> bool:
    day = moscow_date()
    if mode == "promo":
        return promo_active(day)
    return day.year == 2026 and day.month == 11 and day.day == 21


def plan(rows: list[dict], rrp_map: dict[str, float], mode: str) -> list[dict]:
    out = []
    seen = set()
    for item in rows:
        sku = str(item.get("offerId") or item.get("OFFER_ID") or "").strip()
        if not sku or sku in seen or not is_promo_offer(sku):
            continue
        seen.add(sku)
        buyer = number(item.get("onDisplay") if "onDisplay" in item else item.get("ON_DISPLAY"))
        seller = number(item.get("basicPrice") if "basicPrice" in item else item.get("BASIC_PRICE"))
        if buyer is None or seller is None:
            continue
        base_rrp = regular_rrp(sku, rrp_map)
        target = target_buyer_price(sku, base_rrp, rrp_map, mode=mode)
        if target is None or abs(float(buyer) - float(target)) <= TOLERANCE_RUB:
            continue
        new_seller = float(max(1, ceil(float(seller) * float(target) / float(buyer))))
        if int(new_seller) == int(float(seller)):
            new_seller = float(int(float(seller)) + (1 if buyer < target else -1))
        out.append({
            "sku": sku,
            "buyer_before": float(buyer),
            "seller_before": float(seller),
            "seller_after": new_seller,
            "target_buyer": float(target),
            "regular_rrp": base_rrp,
            "mode": mode,
        })
    return out


def _send(lines: list[str]) -> None:
    chunk = ""
    for line in lines:
        candidate = (chunk + "\n" + line).strip() if chunk else line
        if len(candidate) > 3600 and chunk:
            send_telegram(chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        send_telegram(chunk)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("promo", "restore"), required=True)
    ap.add_argument("--output", default="artifacts/yandex-promo-reprice.json")
    args = ap.parse_args()

    if os.getenv("MARK_SHMIDT_PROMO_APPROVED") != "YES":
        raise RuntimeError("Promo repricing disabled: approval flag missing")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not _mode_allowed(args.mode):
        state = {"mode": args.mode, "skipped": True,
                 "reason": f"date guard; promo ends {PROMO_END.isoformat()}"}
        output.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(state["reason"])
        return 0

    rrp_map = load_rrp_map()
    if not rrp_map:
        raise RuntimeError("RRP_MAP_B64 is not configured")

    client = YandexMarketClient()
    catalog, _, _ = _catalog(client)
    business_ids = sorted({int(x["business_id"]) for x in catalog})
    if len(business_ids) != 1:
        raise RuntimeError("Expected exactly one Yandex business")
    business_id = business_ids[0]

    state = {"mode": args.mode, "business_id": business_id, "rounds": []}
    for round_no in (1, 2):
        rows = obtain_report(client, business_id)
        changes = plan(rows, rrp_map, args.mode)
        rec = {"round": round_no, "planned": changes, "sent": False}
        state["rounds"].append(rec)
        output.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if changes:
            apply_one_shot(client, business_id, changes)
            rec["sent"] = True
            output.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Yandex promo {args.mode} round {round_no}: sent {len(changes)}")
        time.sleep(COOLDOWN_SEC)

    final_rows = obtain_report(client, business_id)
    final = []
    for item in final_rows:
        sku = str(item.get("offerId") or item.get("OFFER_ID") or "").strip()
        if not sku or not is_promo_offer(sku):
            continue
        buyer = number(item.get("onDisplay") if "onDisplay" in item else item.get("ON_DISPLAY"))
        base_rrp = regular_rrp(sku, rrp_map)
        target = target_buyer_price(sku, base_rrp, rrp_map, mode=args.mode)
        if buyer is None or target is None:
            continue
        final.append({
            "sku": sku,
            "buyer": float(buyer),
            "target": float(target),
            "delta": round(float(buyer) - float(target), 2),
        })
    state["final"] = final
    output.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"Яндекс — {'акционные цены' if args.mode == 'promo' else 'возврат обычных цен'}",
        f"1-й прогон: {len(state['rounds'][0]['planned'])}",
        f"2-й прогон: {len(state['rounds'][1]['planned'])}",
        "Финальная витрина:",
    ]
    for row in sorted(final, key=lambda x: x["sku"]):
        lines.append(
            f"• {row['sku']}: {row['buyer']:,.0f} ₽ / цель {row['target']:,.0f} ₽ "
            f"({row['delta']:+.0f} ₽)"
        )
    _send(lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
