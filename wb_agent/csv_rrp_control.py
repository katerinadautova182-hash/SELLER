"""WB buyer-price vs RRP control, using real local Chrome CSV; read-only.

Use the exact shared RRP rules (including promotional RRP) from SELLER.
Does not call card.wb.ru or upload/update any WB seller prices.

Examples:
  python -m wb_agent.csv_rrp_control "WB_prices_2026-10-20 (3).csv"
  python -m wb_agent.csv_rrp_control export.csv --rrp-csv rrp-export.csv
  python -m wb_agent.csv_rrp_control export.csv --as-of 2026-10-20
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, InvalidOperation
from pathlib import Path

from ozon_agent.business_rules import (
    box_rule, canonical_sku, is_clearance_sku, manual_rrp,
)
from ozon_agent.catalog import normalize_sku
from ozon_agent.promo_policy import MOSCOW, effective_rrp
from .sku_aliases import WB_RRP_ALIASES


def numeric(value):
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value).replace(" ", "").replace(",", "."))
        return result if result.is_finite() and result > 0 else None
    except (InvalidOperation, ValueError):
        return None


def ceiling(value):
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_CEILING))


def csv_rows(path: str | Path):
    with open(path, encoding="utf-8-sig", newline="") as stream:
        first = stream.read(8192)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(first, delimiters=";,\t")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";" if first.count(";") >= first.count(",") else ","
        return list(csv.DictReader(stream, delimiter=delimiter))


def load_reference(*, rrp_csv: str | None = None):
    if rrp_csv:
        reference = {}
        for item in csv_rows(rrp_csv):
            sku = item.get("Артикул") or item.get("article") or item.get("sku")
            value = numeric(item.get("РРЦ, руб.") or item.get("rrp"))
            if sku and value:
                reference[normalize_sku(sku)] = float(value)
        if not reference:
            raise ValueError("The supplied RRP CSV contains no matching RRP columns/values")
        return reference
    raw = os.getenv("RRP_MAP_B64", "").strip()
    if not raw:
        raise RuntimeError("Missing RRP_MAP_B64; supply --rrp-csv with the private RRP export")
    value = json.loads(base64.b64decode(raw).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("RRP_MAP_B64 must encode a dictionary")
    return {normalize_sku(key): float(v) for key, v in value.items() if numeric(v)}


def references(sku, rrp_map, day: date):
    """Regular and current promotional RRP, matching WB alias/manual rules."""
    if not sku or is_clearance_sku(sku):
        return None, None, "CLEARANCE"
    base = manual_rrp(sku)
    rule = "manual" if base is not None else ""
    if base is None:
        box = box_rule(sku)
        if box:
            first = rrp_map.get(normalize_sku(box[0]))
            base = first * box[1] if first is not None else None
            rule = "box"
        else:
            canonical = WB_RRP_ALIASES.get(normalize_sku(sku), canonical_sku(sku))
            base = manual_rrp(canonical)
            if base is None:
                base = rrp_map.get(normalize_sku(canonical))
            rule = "alias" if normalize_sku(canonical) != normalize_sku(sku) else "direct"
    if base is None:
        return None, None, "MISSING_RRP"
    effective = effective_rrp(sku, float(base), rrp_map, day=day)
    return float(base), float(effective), rule


def price_day(item, override=None):
    if override:
        return override
    raw = item.get("checked_at") or ""
    try:
        instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return instant.astimezone(MOSCOW).date()
    except (TypeError, ValueError):
        return datetime.now(MOSCOW).date()


def high_markup_level(buyer: Decimal | None, applied_rrp: float | None) -> str:
    """Price anomaly rules for non-promo and promotional items.

    Above RRP 1500: flag strictly over 30%.
    RRP <= 1500: up to +70% is allowed; review strictly over +70%.
    Never treats this alert as an automatic authorization to lower prices.
    """
    if buyer is None or applied_rrp is None or applied_rrp <= 0:
        return ""
    base = Decimal(str(applied_rrp))
    if base > 1500:
        return "REVIEW" if buyer > base * Decimal("1.30") else ""
    return "URGENT" if buyer > base * Decimal("1.70") else ""


def analyze(rows, rrp_map, *, as_of=None, max_step_pct=Decimal("5")):
    output = []
    for item in rows:
        sku = str(item.get("sku") or "").strip()
        nm = str(item.get("nm_id") or "").strip()
        buyer = numeric(item.get("buyer_min_rub"))
        base_price = numeric(item.get("seller_base_rub"))
        day = price_day(item, override=as_of)
        regular, applied, rr_rule = references(sku, rrp_map, day)
        is_promo = bool(regular is not None and applied is not None and regular != applied)
        target = ceiling(Decimal(str(applied)) * Decimal("1.05")) if applied is not None else None
        tolerance_floor = ceiling(Decimal(str(applied)) * Decimal("1.02")) if applied is not None else None
        tolerance_ceiling = (
            int((Decimal(str(applied)) * (Decimal("1.70") if applied <= 1500 else Decimal("1.08")))
                .quantize(Decimal("1"), rounding=ROUND_FLOOR))
            if applied is not None else None
        )
        scraped = str(item.get("read_status") or "").strip().upper()
        if scraped == "SKIP_OUT_OF_STOCK":
            status = "NO_STOCK"
        elif scraped != "OK" or buyer is None:
            status = "NO_BUYER_PRICE"
        elif is_clearance_sku(sku):
            status = "CLEARANCE"
        elif applied is None:
            status = "NO_RRP"
        elif buyer < Decimal(str(applied)):
            status = "RED"
        elif buyer < Decimal(target):
            status = "YELLOW"
        else:
            status = "OK"
        # The old RED/YELLOW/OK zone is a LOWER-bound classification.
        # Upper-bound anomalies are separate: normal-price markup warnings
        # and a strict promotional target (effective RRP + 5%).
        markup_pct = (
            float(((buyer / Decimal(str(applied)) - 1) * 100).quantize(Decimal("0.01")))
            if buyer is not None and applied is not None and applied > 0 else None
        )
        high_level = high_markup_level(buyer, applied) if status == "OK" else ""
        promo_over_target = bool(
            status == "OK" and is_promo and buyer is not None and
            tolerance_ceiling is not None and buyer > Decimal(tolerance_ceiling)
        )
        if promo_over_target:
            upper_zone = "PROMO_ABOVE_TARGET"
        elif high_level == "URGENT":
            upper_zone = "HIGH_MARKUP_URGENT"
        elif high_level == "REVIEW":
            upper_zone = "HIGH_MARKUP_REVIEW"
        else:
            upper_zone = "NORMAL"
        control_status = upper_zone if status == "OK" and upper_zone != "NORMAL" else status
        if applied is not None and buyer is not None:
            if tolerance_floor <= buyer <= tolerance_ceiling:
                control_status = "OK"
            elif buyer < tolerance_floor:
                control_status = "BELOW_TOLERANCE"
            elif applied <= 1500 and buyer > tolerance_ceiling:
                control_status = "CHEAP_ABOVE_170_PCT_RRP"
            elif not is_promo and buyer > tolerance_ceiling:
                control_status = "HIGH_MARKUP_REVIEW" if high_level else "ABOVE_TOLERANCE"

        proposed = None
        change_pct = None
        action = ""
        if control_status == "BELOW_TOLERANCE":
            if base_price is None:
                action = "REVIEW_NO_BASE_PRICE"
            else:
                # Like Yandex: RED -> first restore exact RRP, YELLOW -> +3% base.
                # Scale the RED correction because WB public price is often
                # only a fraction of the seller's base.
                if status == "RED":
                    proposed = ceiling(base_price * Decimal(str(applied)) / buyer)
                else:
                    proposed = ceiling(base_price * Decimal("1.03"))
                change_pct = float((Decimal(proposed) / base_price - 1) * 100)
                if change_pct > float(max_step_pct):
                    action = "REVIEW_STEP_TOO_LARGE"
                elif proposed <= base_price:
                    action = "NO_ACTION"
                else:
                    action = "PROPOSE_UPWARD_CHANGE"
        elif promo_over_target:
            # Estimate ONLY, never authorize an automatic write. A change to
            # seller base may not scale buyer price linearly under WB promotions.
            action = "REVIEW_PROMO_OVER_TARGET"
            if base_price is not None and buyer is not None:
                estimated = (base_price * Decimal(target) / buyer).quantize(
                    Decimal("1"), rounding=ROUND_FLOOR)
                if estimated > 0 and estimated < base_price:
                    proposed = int(estimated)
                    change_pct = float((estimated / base_price - 1) * 100)
        elif control_status == "CHEAP_ABOVE_170_PCT_RRP":
            action = "REVIEW_CHEAP_ABOVE_LIMIT"
        elif status == "OK" and high_level:
            action = "REVIEW_HIGH_MARKUP_" + high_level
        elif control_status == "ABOVE_TOLERANCE":
            action = "REVIEW_ABOVE_TOLERANCE"
            if base_price is not None and buyer is not None:
                estimated = (base_price * Decimal(target) / buyer).quantize(
                    Decimal("1"), rounding=ROUND_FLOOR)
                if estimated > 0 and estimated < base_price:
                    proposed = int(estimated)
                    change_pct = float((estimated / base_price - 1) * 100)
        elif status != "OK" and control_status != "OK":
            action = "SKIP_" + status
        result = {**item, "analysis_day":day.isoformat(), "rrp_regular":regular,
            "rrp_applied":applied, "promo_rrp_active":is_promo, "rrp_rule":rr_rule,
            "rrp_plus_5":target,
            "allowed_buyer_min":tolerance_floor,
            "allowed_buyer_max":tolerance_ceiling,
            "buyer_price":float(buyer) if buyer else None,
            "zone":status, "control_status":control_status, "upper_zone":upper_zone,
            "markup_over_applied_rrp_pct":markup_pct,
            "high_markup_level":high_level,
            "promo_over_target":promo_over_target,
            "promo_excess_rub":max(0, float(buyer) - target) if promo_over_target else None,
            "gap_to_floor":max(0, target - float(buyer)) if buyer and target else None,
            "seller_price_proposed":proposed,
            "seller_increase_pct":round(change_pct, 2) if change_pct is not None and change_pct > 0 else None,
            "seller_change_pct":round(change_pct, 2) if change_pct is not None else None,
            "action":action, "apply":False}
        output.append(result)
    return output


def main():
    parser = argparse.ArgumentParser(description="Read-only WB RRP and promotional price comparison")
    parser.add_argument("input_csv", help="CSV exported by SELLER WB Auto")
    parser.add_argument("--rrp-csv", help="Locally exported private Google Sheets RRP CSV")
    parser.add_argument("--as-of", help="Override promotion date: YYYY-MM-DD; otherwise use row checked_at")
    parser.add_argument("--max-step-pct", default="5", help="Single-step safety limit; default 5%")
    parser.add_argument("--output", default="artifacts/wb-rrp-csv-control.csv")
    args = parser.parse_args()
    references_map = load_reference(rrp_csv=args.rrp_csv)
    source = csv_rows(args.input_csv)
    required = {"nm_id", "sku", "buyer_min_rub", "seller_base_rub", "read_status"}
    if not source or not required.issubset(source[0].keys()):
        raise SystemExit("Invalid WB CSV; required headers: " + ", ".join(sorted(required)))
    result = analyze(source, references_map,
                     as_of=date.fromisoformat(args.as_of) if args.as_of else None,
                     max_step_pct=Decimal(args.max_step_pct))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    columns = list(result[0])
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter=";", fieldnames=columns)
        writer.writeheader()
        writer.writerows(result)
    summary = {"mode":"DRY_RUN_NO_PRICE_CHANGES","input_count":len(result),
        "zones":{name:sum(row["zone"] == name for row in result)
                 for name in ("RED","YELLOW","OK","NO_BUYER_PRICE","NO_STOCK","NO_RRP","CLEARANCE")},
        "promotional_rrp_rows":sum(row["promo_rrp_active"] for row in result),
        "upper_price_alerts":{
            "promo_above_target":sum(row["upper_zone"] == "PROMO_ABOVE_TARGET" for row in result),
            "high_markup_review":sum(row["upper_zone"] == "HIGH_MARKUP_REVIEW" for row in result),
            "high_markup_urgent":sum(row["upper_zone"] == "HIGH_MARKUP_URGENT" for row in result),
        },
        "high_markup_regardless_of_promo":{
            "review":sum(row["high_markup_level"] == "REVIEW" for row in result),
            "urgent":sum(row["high_markup_level"] == "URGENT" for row in result),
        },
        "proposed":sum(row["action"] == "PROPOSE_UPWARD_CHANGE" for row in result),
        "manual_review":sum(row["action"] == "REVIEW_STEP_TOO_LARGE" for row in result),
        "input":str(args.input_csv),"result":str(destination)}
    destination.with_suffix(".json").write_text(
        json.dumps({"summary":summary,"rows":result},ensure_ascii=False,indent=2),
        encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
