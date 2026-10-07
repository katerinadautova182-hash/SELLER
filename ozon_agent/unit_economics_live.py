"""Live unit-economics snapshot for Ozon.

This is contribution profit per unit BEFORE advertising, tax and shared overhead.
It uses seller-side revenue (marketing_seller_price when available, otherwise
the seller price), current Ozon commission/logistics/acquiring fields, and
purchase cost x 1.35 for delivery/customs.

Purchase costs are supplied through PURCHASE_COST_MAP_B64 and are never stored
in this public repository.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
from pathlib import Path

from .business_rules import canonical_sku, manual_purchase_cost, box_rule, bundle_components
from .catalog import normalize_sku
from .margin import estimate_live_margin


def load_cost_map() -> dict[str, dict]:
    """Load cost records.

    New format:
      {"SKU": {"cost": 123.45, "source": "invoice ...", "verified": true}}

    Plain numeric values from the old secret remain readable, but are explicitly
    treated as unverified legacy values so they cannot be reported as reliable
    unit economics.
    """
    raw = os.getenv("PURCHASE_COST_MAP_B64", "").strip()
    if not raw:
        return {}
    decoded = base64.b64decode(raw).decode("utf-8")
    source = json.loads(decoded)
    out: dict[str, dict] = {}
    for key, value in source.items():
        if isinstance(value, dict):
            cost = value.get("cost")
            try:
                cost = float(cost)
            except (TypeError, ValueError):
                continue
            if cost <= 0:
                continue
            out[normalize_sku(key)] = {
                "cost": cost,
                "source": str(value.get("source") or "unknown"),
                "verified": bool(value.get("verified")),
            }
        else:
            try:
                cost = float(value)
            except (TypeError, ValueError):
                continue
            if cost <= 0:
                continue
            out[normalize_sku(key)] = {
                "cost": cost,
                "source": "legacy_unverified",
                "verified": False,
            }
    return out


def _cost_record(cost_map: dict[str, dict], sku: str) -> dict | None:
    return cost_map.get(normalize_sku(sku))


def resolve_purchase_cost(
    offer_id: str, cost_map: dict[str, dict]
) -> tuple[float | None, str, bool]:
    manual = manual_purchase_cost(offer_id)
    if manual is not None:
        return float(manual), "manual_confirmed", True

    bundle = bundle_components(offer_id)
    if bundle:
        records = [_cost_record(cost_map, x) for x in bundle]
        if all(r and float(r["cost"]) > 0 for r in records):
            verified = all(bool(r.get("verified")) for r in records)
            sources = "+".join(str(r.get("source") or "unknown") for r in records)
            return float(sum(float(r["cost"]) for r in records)), f"bundle:{sources}", verified
        return None, "bundle_missing_component", False

    box = box_rule(offer_id)
    if box:
        base_sku, multiplier = box
        record = _cost_record(cost_map, base_sku)
        if record and float(record["cost"]) > 0:
            return (
                float(record["cost"]) * float(multiplier),
                f"box:{record.get('source') or 'unknown'}",
                bool(record.get("verified")),
            )
        return None, "box_base_missing", False

    canonical = canonical_sku(offer_id)
    record = _cost_record(cost_map, canonical)
    if record is None:
        record = _cost_record(cost_map, offer_id)
    if record and float(record["cost"]) > 0:
        return float(record["cost"]), str(record.get("source") or "unknown"), bool(record.get("verified"))
    return None, "missing", False


def classify_margin(profit: float, margin_pct: float) -> str:
    if profit < 0:
        return "LOSS"
    if margin_pct < 5:
        return "THIN"
    return "POSITIVE"


def build_rows(snapshot: dict, cost_map: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    for item in snapshot.get("items", []):
        if item.get("eligibility") != "ELIGIBLE":
            continue
        offer_id = str(item.get("offer_id") or "").strip()
        if not offer_id:
            continue

        purchase_cost, cost_source, cost_verified = resolve_purchase_cost(offer_id, cost_map)
        base = {
            "offer_id": offer_id,
            "name": item.get("name") or "",
            "customer_price": item.get("customer_price"),
            "seller_price": item.get("current_price"),
            "marketing_seller_price": item.get("marketing_price"),
            "commission_percent": item.get("commission_percent"),
            "logistics_rub": item.get("logistics_rub"),
            "acquiring_rub": item.get("acquiring_rub"),
            "purchase_cost": purchase_cost,
            "purchase_cost_source": cost_source,
            "purchase_cost_verified": cost_verified,
        }

        if purchase_cost is None:
            out.append({**base, "status": "MISSING_COST"})
            continue

        try:
            m = estimate_live_margin(
                purchase_cost=purchase_cost,
                current_price=float(item.get("current_price") or 0),
                marketing_price=float(item.get("marketing_price") or 0),
                commission_percent=float(item.get("commission_percent") or 0),
                logistics_rub=float(item.get("logistics_rub") or 0),
                acquiring_rub=float(item.get("acquiring_rub") or 0),
            )
        except ValueError as exc:
            out.append({**base, "status": "INVALID", "error": str(exc)})
            continue

        calculated_status = classify_margin(
            m.profit_before_tax_ads_rub,
            m.margin_before_tax_ads_percent,
        )
        out.append({
            **base,
            "seller_revenue_rub": m.seller_revenue_rub,
            "landed_cost_rub": m.landed_cost_rub,
            "commission_rub": m.commission_rub,
            "direct_logistics_rub": m.logistics_rub,
            "direct_acquiring_rub": m.acquiring_rub,
            "profit_before_tax_ads_rub": m.profit_before_tax_ads_rub,
            "margin_before_tax_ads_percent": m.margin_before_tax_ads_percent,
            "revenue_source": m.revenue_source,
            "calculated_status": calculated_status,
            "status": calculated_status if cost_verified else "UNVERIFIED_COST",
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="artifacts/ozon_catalog.json")
    ap.add_argument("--json-output", default="artifacts/unit_economics_live.json")
    ap.add_argument("--csv-output", default="artifacts/unit_economics_live.csv")
    args = ap.parse_args()

    cost_map = load_cost_map()
    if not cost_map:
        print("PURCHASE_COST_MAP_B64 is not configured; unit economics skipped.")
        return 0

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    rows = build_rows(snapshot, cost_map)

    json_path = Path(args.json_output)
    csv_path = Path(args.csv_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = [
        "offer_id","name","customer_price","seller_price","marketing_seller_price",
        "seller_revenue_rub","purchase_cost","landed_cost_rub","commission_percent",
        "commission_rub","direct_logistics_rub","direct_acquiring_rub",
        "profit_before_tax_ads_rub","margin_before_tax_ads_percent",
        "purchase_cost_source","purchase_cost_verified","revenue_source",
        "calculated_status","status",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", delimiter=";")
        w.writeheader()
        w.writerows(rows)

    ready = [x for x in rows if x.get("status") in ("LOSS", "THIN", "POSITIVE")]
    losses = [x for x in ready if x.get("status") == "LOSS"]
    thin = [x for x in ready if x.get("status") == "THIN"]
    missing = [x for x in rows if x.get("status") == "MISSING_COST"]
    unverified = [x for x in rows if x.get("status") == "UNVERIFIED_COST"]
    print(
        f"Unit economics: verified_ready={len(ready)}, loss={len(losses)}, "
        f"thin_margin={len(thin)}, unverified_cost={len(unverified)}, "
        f"missing_cost={len(missing)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
