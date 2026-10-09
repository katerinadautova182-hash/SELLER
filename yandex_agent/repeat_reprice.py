"""Bounded, evidence-based follow-up to the initial Yandex price correction.

Never retry the same unchanged storefront observation. Never lower prices.
Stop at cumulative +30% seller-price growth from this run's baseline, max
three write rounds. Always re-fetch official report between rounds.
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path

from .client import YandexMarketClient
from .official_prices import obtain_report, number
from .one_shot_reprice import apply_one_shot, candidates
from .price_check import _catalog, load_rrp, resolve_rrp, classify
from ozon_agent.business_rules import is_clearance_sku
from ozon_agent.telegram import send_telegram

MAX_ROUNDS = 3
MAX_CUMULATIVE_RISE = 0.30
COOLDOWN_SEC = 155


def eligible(planned, baseline, previous_observation):
    approved, held = [], []
    for row in planned:
        sku = row["sku"]
        base = baseline.get(sku, row["seller_before"])
        if row["seller_after"] > base * (1 + MAX_CUMULATIVE_RISE):
            held.append({"sku":sku,"reason":"CUMULATIVE_30_PERCENT_LIMIT"})
        elif sku in previous_observation and row["buyer_before"] <= previous_observation[sku]:
            held.append({"sku":sku,"reason":"NO_CONFIRMED_STORE_PRICE_RESPONSE"})
        else:
            approved.append(row)
    return approved, held


def run():
    if os.getenv("YANDEX_REPEAT_APPROVED") != "YES":
        raise RuntimeError("Explicit authorization missing")
    client = YandexMarketClient()
    rrp_map = load_rrp()
    catalog, _, _ = _catalog(client)
    ids = sorted({int(x["business_id"]) for x in catalog})
    if len(ids) != 1:
        raise RuntimeError("Expected exactly one Yandex business")
    business_id = ids[0]
    path = Path("artifacts/yandex-repeat-control.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"business_id":business_id,"rounds":[],"stops":[]}
    baseline = {}
    previous = {}
    # First report is fresh: do not rely on previous run's potentially stale prices.
    for round_num in range(1, MAX_ROUNDS+1):
        observed = obtain_report(client, business_id)
        planned, skipped = candidates(observed, rrp_map)
        for row in planned:
            baseline.setdefault(row["sku"], row["seller_before"])
        approved, held = eligible(planned, baseline, previous)
        state["stops"].extend(skipped + held)
        record = {"round":round_num,"candidate_count":len(planned),"approved":approved,
                  "held":held,"skipped":skipped,"write_sent":False}
        state["rounds"].append(record)
        path.write_text(json.dumps(state,ensure_ascii=False,indent=2))
        if not approved:
            break
        # Capture storefront readings before mutation; after the next report
        # reject further writes when the storefront failed to react.
        previous = {r["sku"]:r["buyer_before"] for r in approved}
        apply_one_shot(client,business_id,approved)
        record["write_sent"] = True
        path.write_text(json.dumps(state,ensure_ascii=False,indent=2))
        time.sleep(COOLDOWN_SEC)
    # Final verification only: no more writes.
    final = obtain_report(client,business_id)
    final_items = []
    for item in final:
        sku=str(item.get("offerId") or "").strip()
        if not sku or is_clearance_sku(sku): continue
        buyer=number(item.get("onDisplay"))
        rrp,_=resolve_rrp(sku,rrp_map)
        if rrp is None or buyer is None: continue
        status,minimum=classify(buyer,rrp)
        if status != "OK":
            final_items.append({"sku":sku,"buyer":buyer,"minimum":minimum,"status":status})
    state["final_violations"]=final_items
    path.write_text(json.dumps(state,ensure_ascii=False,indent=2))
    lines=["Яндекс — повторная корректировка",
           f"Раундов: {len(state['rounds'])}; осталось нарушений: {len(final_items)}",
           f"Пропусков по ограничениям: {len(state['stops'])}"]
    for x in final_items:
        lines.append(f"• {x['sku']}: {x['status']}, покупатель {x['buyer']:,.0f} ₽, цель {x['minimum']:,.0f} ₽")
    if final_items: lines.append("Часть товаров не достигла цели. Дальнейшие повышения остановлены.")
    text="\n".join(lines)
    print(text)
    for start in range(0,len(text),3500):
        send_telegram(text[start:start+3500])
    return 0 if not final_items else 2

if __name__=="__main__":
    raise SystemExit(run())
