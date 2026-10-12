"""One-off two-pass Yandex repricing requested by owner.

Each pass uses a fresh official Yandex goods-prices report:
- RED (onDisplay < RRP): seller price += ceil((RRP - onDisplay) * 1.5)
- YELLOW (RRP <= onDisplay < ceil(RRP*1.05)): seller price += 3%
Two write passes maximum, then one final verification report. Never lowers prices.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from ozon_agent.business_rules import is_clearance_sku
from ozon_agent.telegram import send_telegram
from .client import YandexMarketClient
from .official_prices import obtain_report, number
from .one_shot_reprice import apply_one_shot
from .price_check import _catalog, classify, load_rrp, resolve_rrp

OUTPUT = Path("artifacts/yandex-two-pass-reprice.json")
COOLDOWN_SEC = 155
MAX_CHANGES_PER_PASS = 200


def plan(rows, rrp_map):
    planned = []
    seen = set()
    for item in rows:
        sku = str(item.get("offerId") or "").strip()
        if not sku or sku in seen or is_clearance_sku(sku):
            continue
        seen.add(sku)
        buyer = number(item.get("onDisplay"))
        seller = number(item.get("basicPrice"))
        rrp, source = resolve_rrp(sku, rrp_map)
        if buyer is None or seller is None or rrp is None:
            continue
        status, minimum = classify(buyer, rrp)
        if status == "RED":
            missing = max(0.0, float(rrp) - float(buyer))
            increase = float(math.ceil(missing * 1.5))
        elif status == "YELLOW":
            increase = float(math.ceil(float(seller) * 0.03))
        else:
            continue
        if increase <= 0:
            continue
        new_price = float(math.ceil(float(seller) + increase))
        planned.append({
            "sku": sku,
            "status": status,
            "buyer_before": float(buyer),
            "seller_before": float(seller),
            "seller_after": new_price,
            "increase": new_price - float(seller),
            "rrp": float(rrp),
            "minimum": float(minimum),
            "rrp_source": source,
        })
    if len(planned) > MAX_CHANGES_PER_PASS:
        raise RuntimeError(f"Refusing unexpectedly large batch: {len(planned)}")
    return planned


def _send(lines):
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


def main():
    if os.getenv("YANDEX_TWO_PASS_APPROVED") != "YES":
        raise RuntimeError("Explicit two-pass approval missing")

    rrp_map = load_rrp()
    client = YandexMarketClient()
    catalog, _, _ = _catalog(client)
    business_ids = sorted({int(x["business_id"]) for x in catalog})
    if len(business_ids) != 1:
        raise RuntimeError("Expected exactly one Yandex business")
    business_id = business_ids[0]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    state = {"business_id": business_id, "policy": {
        "red": "seller += ceil((RRP - onDisplay) * 1.5)",
        "yellow": "seller += ceil(seller * 0.03)",
        "passes": 2,
    }, "rounds": []}

    for round_no in (1, 2):
        rows = obtain_report(client, business_id)
        changes = plan(rows, rrp_map)
        record = {"round": round_no, "planned": changes,
                  "write_started": False, "write_sent": False}
        state["rounds"].append(record)
        OUTPUT.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if changes:
            record["write_started"] = True
            OUTPUT.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            apply_one_shot(client, business_id, changes)
            record["write_sent"] = True
            OUTPUT.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Round {round_no}: sent {len(changes)} changes")
        time.sleep(COOLDOWN_SEC)

    final = obtain_report(client, business_id)
    remaining = []
    for item in final:
        sku = str(item.get("offerId") or "").strip()
        if not sku or is_clearance_sku(sku):
            continue
        buyer = number(item.get("onDisplay"))
        rrp, _ = resolve_rrp(sku, rrp_map)
        if buyer is None or rrp is None:
            continue
        status, minimum = classify(float(buyer), float(rrp))
        if status != "OK":
            remaining.append({
                "sku": sku,
                "status": status,
                "buyer": float(buyer),
                "rrp": float(rrp),
                "minimum": float(minimum),
            })
    remaining.sort(key=lambda x: (0 if x["status"] == "RED" else 1, x["buyer"] - (x["rrp"] if x["status"] == "RED" else x["minimum"])))
    state["final_remaining"] = remaining
    OUTPUT.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    total1 = len(state["rounds"][0]["planned"])
    total2 = len(state["rounds"][1]["planned"])
    red = [x for x in remaining if x["status"] == "RED"]
    yellow = [x for x in remaining if x["status"] == "YELLOW"]
    lines = [
        "Яндекс — разовое повышение в 2 прогона",
        f"1-й прогон: изменено {total1}",
        f"2-й прогон: изменено {total2}",
        f"После повторной проверки: 🔴 {len(red)} / 🟡 {len(yellow)}",
    ]
    for x in remaining:
        target = x["rrp"] if x["status"] == "RED" else x["minimum"]
        lines.append(f"• {x['sku']} [{x['status']}]: покупатель {x['buyer']:,.0f} ₽, цель {target:,.0f} ₽")
    _send(lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Preserve trace of the actual stage, including failures before first write.
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        try:
            state = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        state["error"] = error
        OUTPUT.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        started = [r["round"] for r in state.get("rounds", []) if r.get("write_started")]
        message = (
            "Яндекс — разовое повышение НЕ завершено.\n"
            f"Ошибка: {error}\n"
            + (f"Начата отправка в прогонах {started}; проверьте витрину перед повтором."
               if started else "Отправка новых цен НЕ начиналась.")
        )
        print(message, flush=True)
        try:
            send_telegram(message)
        except Exception as notify_error:
            print(f"Telegram failure alert not delivered: {notify_error}", flush=True)
        raise
