"""One-off targeted cleanup based on independently observed buyer prices.

Four guarded rounds maximum. At most +20% seller price each round and +75%
from this run's original seller price. No new write unless prior increase
produced a confirmed storefront response. Report all remaining violations.
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
from .price_check import _catalog, load_rrp, resolve_rrp, classify
from .reporting import money

MAX_ROUNDS = 4
STEP_LIMIT = 0.20
TOTAL_LIMIT = 0.75
WAIT_AFTER_WRITE_SEC = 155
REPORT = Path("artifacts/yandex-cleanup.json")


def plan_round(rows, rrp_map, original_seller, previous):
    changes, held, current = [], [], {}
    for row in rows:
        sku = str(row.get("offerId") or "").strip()
        if not sku or is_clearance_sku(sku):
            continue
        buyer, seller = number(row.get("onDisplay")), number(row.get("basicPrice"))
        rrp, _ = resolve_rrp(sku, rrp_map)
        if buyer is None or seller is None or rrp is None:
            continue
        status, floor = classify(buyer, rrp)
        current[sku] = {"buyer": buyer, "seller": seller, "rrp": rrp, "status": status}
        if status == "OK":
            continue
        target = math.ceil(rrp * 1.05)
        original_seller.setdefault(sku, seller)
        if sku in previous:
            old = previous[sku]
            if seller + 0.01 < old["expected_seller"]:
                held.append({"sku": sku, "reason": "SELLER_UPDATE_NOT_YET_APPLIED"})
                continue
            if buyer <= old["buyer"] + 0.01:
                held.append({"sku": sku, "reason": "BUYER_PRICE_DID_NOT_RISE"})
                continue
        # Buyer-to-seller price relation can be highly non-linear; estimate
        # missing seller increment conservatively from *current* values.
        delta = math.ceil((target - buyer) * seller / buyer * 1.08)
        delta = max(delta, 1)
        after = min(seller + delta,
                    math.floor(seller * (1 + STEP_LIMIT)),
                    math.floor(original_seller[sku] * (1 + TOTAL_LIMIT)))
        if after <= seller:
            held.append({"sku": sku, "reason": "CUMULATIVE_RISE_LIMIT"})
            continue
        changes.append({"sku": sku, "status": status, "rrp": rrp,
                        "buyer_before": buyer, "seller_before": seller,
                        "seller_after": after, "minimum_buyer": target,
                        "delta": after - seller})
    return changes, held, current


def final_status(rows, rrp_map):
    result = []
    for row in rows:
        sku = str(row.get("offerId") or "").strip()
        buyer = number(row.get("onDisplay"))
        if not sku or buyer is None or is_clearance_sku(sku):
            continue
        rrp, _ = resolve_rrp(sku, rrp_map)
        if rrp is None:
            continue
        status, _ = classify(buyer, rrp)
        if status != "OK":
            result.append({"sku": sku, "buyer": buyer, "rrp": rrp,
                           "status": status, "minimum": math.ceil(rrp * 1.05)})
    return sorted(result, key=lambda x: (x["status"] != "RED", -x["minimum"] + x["buyer"]))


def main():
    if os.getenv("YANDEX_CLEANUP_ONCE") != "YES":
        raise RuntimeError("Explicit one-time authorization required")
    client = YandexMarketClient()
    rrp_map = load_rrp()
    catalog, _, _ = _catalog(client)
    business_ids = sorted({int(x["business_id"]) for x in catalog})
    if len(business_ids) != 1:
        raise RuntimeError("Expected exactly one business")
    bid = business_ids[0]
    state = {"business_id": bid, "rounds": [], "complete": False,
             "safety": {"max_rounds": MAX_ROUNDS,
                        "max_step_rise": STEP_LIMIT,
                        "max_cumulative_rise": TOTAL_LIMIT}}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    original, previous = {}, {}
    last = obtain_report(client, bid)
    for round_number in range(1, MAX_ROUNDS + 1):
        changes, held, _ = plan_round(last, rrp_map, original, previous)
        record = {"round": round_number, "changes": changes, "held": held,
                  "write_sent": False}
        state["rounds"].append(record)
        REPORT.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        if not changes:
            break
        # No retries on write errors: write may already have been accepted.
        apply_one_shot(client, bid, changes)
        record["write_sent"] = True
        REPORT.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        previous = {x["sku"]: {"buyer": x["buyer_before"],
                               "expected_seller": x["seller_after"]} for x in changes}
        time.sleep(WAIT_AFTER_WRITE_SEC)
        # A failure here must not trigger new writes. Save the attempted
        # changes for reconciliation through a separate read-only check.
        last = obtain_report(client, bid)
    unresolved = final_status(last, rrp_map)
    state["remaining"] = unresolved
    state["complete"] = True
    REPORT.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    count = sum(len(r["changes"]) for r in state["rounds"] if r["write_sent"])
    lines = ["Яндекс Маркет — итог разовой доводки цен",
             f"Раундов: {len(state['rounds'])}, изменений: {count}",
             f"🔴 Осталось ниже РРЦ: {sum(x['status']=='RED' for x in unresolved)}",
             f"🟡 Осталось ниже РРЦ+5%: {sum(x['status']=='YELLOW' for x in unresolved)}"]
    for row in unresolved:
        lines.append(f"• {row['sku']}: витрина {money(row['buyer'])} ₽, "
                     f"минимум {money(row['minimum'])} ₽, "
                     f"не хватает {money(row['minimum']-row['buyer'])} ₽")
    held = [x for round_ in state["rounds"] for x in round_["held"]]
    if held:
        lines.append("Остановлены ограничениями: " + ", ".join(
            f"{x['sku']} ({x['reason']})" for x in held[-30:]))
    chunks, chunk = [], ""
    for line in lines:
        s = (chunk + "\n" + line) if chunk else line
        if len(s) > 3500 and chunk:
            chunks.append(chunk)
            chunk = line
        else:
            chunk = s
    if chunk:
        chunks.append(chunk)
    for chunk in chunks:
        print(chunk)
        send_telegram(chunk)
    return 0 if not unresolved else 2


if __name__ == "__main__":
    raise SystemExit(main())
