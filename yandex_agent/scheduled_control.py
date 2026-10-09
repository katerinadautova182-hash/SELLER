"""Scheduled Yandex price corrections and history-based high-price alerts.

For every six-hour run, RED increases seller price by missing buyer-side
amount once, YELLOW increases seller price by 3% once. Report is re-read
after the update; WHITE alerts compare current prices to preceding runs.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ozon_agent.business_rules import is_clearance_sku
from ozon_agent.price_alert import _high_price_rule
from ozon_agent.telegram import send_telegram
from statistics import median

from .client import YandexMarketClient
from .official_prices import obtain_report, number
from .one_shot_reprice import apply_one_shot, candidates
from .price_check import _catalog, classify, load_rrp, resolve_rrp
from .reporting import money

STATE_FILE = Path(".state/yandex-price-history.json")
OUTPUT_FILE = Path("artifacts/yandex-scheduled-control.json")
HISTORY_LENGTH = 28
MIN_SAMPLES = 4


def detect_high_prices(rows, history):
    high = []
    for row in rows:
        sku = str(row.get("offerId") or "")
        buyer = number(row.get("onDisplay"))
        if buyer is None or is_clearance_sku(sku):
            continue
        samples = [float(v) for v in history.get(sku, []) if float(v) > 0][-HISTORY_LENGTH:]
        if len(samples) < MIN_SAMPLES:
            continue
        base = median(samples)
        relative, absolute = _high_price_rule(base)
        if buyer > base * (1 + relative) and buyer - base > absolute:
            high.append({"sku": sku, "price": buyer, "baseline": base,
                         "delta": buyer - base, "percent": (buyer / base - 1) * 100})
    return sorted(high, key=lambda x: -x["delta"])


def add_history(rows, history):
    updated = {k: list(v)[-HISTORY_LENGTH:] for k, v in history.items()}
    for row in rows:
        sku = str(row.get("offerId") or "")
        buyer = number(row.get("onDisplay"))
        if buyer is not None and sku and not is_clearance_sku(sku):
            updated.setdefault(sku, []).append(buyer)
            updated[sku] = updated[sku][-HISTORY_LENGTH:]
    return updated


def report_lines(changes, skipped, latest, high):
    by_sku = {str(r.get("offerId") or ""): number(r.get("onDisplay")) for r in latest}
    remaining = []
    for row in latest:
        sku = str(row.get("offerId") or "")
        value = number(row.get("onDisplay"))
        rrp, _ = resolve_rrp(sku, load_rrp_cached)
        if not sku or value is None or rrp is None or is_clearance_sku(sku):
            continue
        status, target = classify(value, rrp)
        if status != "OK":
            remaining.append({"sku": sku, "status": status, "price": value, "rrp": rrp, "target": target})
    red = sorted((x for x in remaining if x["status"] == "RED"), key=lambda x: x["price"] - x["rrp"])
    yellow = sorted((x for x in remaining if x["status"] == "YELLOW"), key=lambda x: x["price"] - x["target"])
    lines = ["Яндекс Маркет — контроль после автокоррекции",
             f"Изменено цен: {len(changes)}; пропущено: {len(skipped)}",
             f"🔴 Ниже РРЦ: {len(red)}",
             f"🟡 Ниже РРЦ+5%: {len(yellow)}",
             f"⚪ Аномально высокая цена: {len(high)}"]
    for title, subset in (("🔴 Ниже РРЦ", red), ("🟡 Ниже РРЦ+5%", yellow)):
        if subset:
            lines.append("")
            lines.append(title)
            for x in subset:
                gap = (x["rrp"] if x["status"] == "RED" else x["target"]) - x["price"]
                lines.append(f"• {x['sku']}: покупатель {money(x['price'])} ₽, "
                             f"{'РРЦ' if x['status']=='RED' else 'минимум'} "
                             f"{money(x['rrp'] if x['status']=='RED' else x['target'])} ₽"
                             f" → не хватает {money(gap)} ₽")
    if high:
        lines.append("")
        lines.append("⚪ Аномально высокая цена")
        for x in high:
            lines.append(f"• {x['sku']}: {money(x['price'])} ₽; обычно ≈ "
                         f"{money(x['baseline'])} ₽ → +{money(x['delta'])} ₽ / +{x['percent']:.1f}%")
    if skipped:
        lines.append("")
        lines.append("Пропущены по ограничениям: " + ", ".join(x["sku"] for x in skipped))
    return lines, remaining


def send_lines(lines):
    chunk = ""
    for line in lines:
        new = (chunk + "\n" + line).strip() if chunk else line
        if len(new) > 3600 and chunk:
            print(chunk)
            send_telegram(chunk)
            chunk = line
        else:
            chunk = new
    if chunk:
        print(chunk)
        send_telegram(chunk)


def main():
    if os.getenv("YANDEX_SCHEDULED_CORRECTION_ENABLED") != "YES":
        raise RuntimeError("Scheduled corrections disabled")
    global load_rrp_cached
    load_rrp_cached = load_rrp()
    client = YandexMarketClient()
    catalog, _, _ = _catalog(client)
    ids = sorted({int(row["business_id"]) for row in catalog})
    if len(ids) != 1:
        raise RuntimeError("Exactly one business required")
    business_id = ids[0]
    before = obtain_report(client, business_id)
    try:
        saved = json.loads(STATE_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        saved = {}
    history = saved.get("history") or {}
    high = detect_high_prices(before, history)
    proposed, skipped = candidates(before, load_rrp_cached)
    # Both RED and YELLOW are eligible, one increase each. candidates()
    # rejects changes greater than 15% of seller price.
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {"business_id": business_id, "planned": proposed, "skipped": skipped,
             "high_price_alerts": high, "write_sent": False}
    OUTPUT_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    if proposed:
        apply_one_shot(client, business_id, proposed)
        state["write_sent"] = True
        OUTPUT_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        time.sleep(155)  # report generation quota: at least two minutes
        after = obtain_report(client, business_id)
    else:
        after = before
    lines, remaining = report_lines(proposed, skipped, after, high)
    state["remaining"] = remaining
    state["buyer_after"] = {str(r.get("offerId")): number(r.get("onDisplay"))
                            for r in after if r.get("offerId")}
    OUTPUT_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"history": add_history(before, history)},
                                     ensure_ascii=False), "utf-8")
    send_lines(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
