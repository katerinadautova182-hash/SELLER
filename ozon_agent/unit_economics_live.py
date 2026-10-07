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


def load_cost_map() -> dict[str, float]:
    raw = os.getenv("PURCHASE_COST_MAP_B64", "").strip()
    if not raw:
        return {}
    decoded = base64.b64decode(raw).decode("utf-8")
    source = json.loads(decoded)
    return {
        normalize_sku(k): float(v)
        for k, v in source.items()
        if v not in (None, "") and float(v) > 0
    }


def resolve_purchase_cost(offer_id: str, cost_map: dict[str, float]) -> tuple[float | None, str]:
    manual = manual_purchase_cost(offer_id)
    if manual is not None:
        return float(manual), "manual"

    bundle = bundle_components(offer_id)
    if bundle:
        vals = [cost_map.get(normalize_sku(x)) for x in bundle]
        if all(v is not None and v > 0 for v in vals):
            return float(sum(vals)), "bundle_sum"
        return None, "bundle_missing_component"

    box = box_rule(offer_id)
    if box:
        base_sku, multiplier = box
        base = cost_map.get(normalize_sku(base_sku))
        if base is not None and base > 0:
            return float(base) * float(multiplier), "box_multiplier"
        return None, "box_base_missing"

    canonical = canonical_sku(offer_id)
    value = cost_map.get(normalize_sku(canonical))
    if value is not None and value > 0:
        return float(value), "cost_map"
    value = cost_map.get(normalize_sku(offer_id))
    if value is not None and value > 0:
        return float(value), "cost_map_raw"
    return None, "missing"


def classify_margin(profit: float, margin_pct: float) -> str:
    if profit < 0:
        return "LOSS"
    if margin_pct < 5:
        return "THIN"
    return "POSITIVE"


def build_rows(snapshot: dict, cost_map: dict[str, float]) -> list[dict]:
    out: list[dict] = []
    for item in snapshot.get("items", []):
        if item.get("eligibility") != "ELIGIBLE":
            continue
        offer_id = str(item.get("offer_id") or "").strip()
        if not offer_id:
            continue

        purchase_cost, cost_source = resolve_purchase_cost(offer_id, cost_map)
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
            "status": classify_margin(
                m.profit_before_tax_ads_rub,
                m.margin_before_tax_ads_percent,
            ),
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
        "purchase_cost_source","revenue_source","status",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", delimiter=";")
        w.writeheader()
        w.writerows(rows)

    ready = [x for x in rows if x.get("status") not in ("MISSING_COST","INVALID")]
    losses = [x for x in ready if x.get("status") == "LOSS"]
    thin = [x for x in ready if x.get("status") == "THIN"]
    missing = [x for x in rows if x.get("status") == "MISSING_COST"]
    print(
        f"Unit economics: ready={len(ready)}, loss={len(losses)}, "
        f"thin_margin={len(thin)}, missing_cost={len(missing)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
