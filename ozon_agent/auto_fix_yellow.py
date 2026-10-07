"""Automatically raise only YELLOW-zone Ozon seller prices by 3%.

Safety rules:
- only ordinary, in-stock products already classified as YELLOW by verified customer_price;
- use current seller price from the live snapshot ("price.price" from /v5/product/info/prices);
- raise by exactly 3% per run, rounded up to whole RUB;
- never touch RED-zone items, clearance (УЦ), out-of-stock, missing-RRP, or unverified buyer-price items;
- after update the workflow rebuilds the live snapshot and rechecks customer_price.
"""
from __future__ import annotations

import argparse
import json
import os
from math import ceil
from pathlib import Path

from .price_alert import load_rrp_map, collect_violations

RAISE_FACTOR = 1.03


def plan_yellow_updates(snapshot: dict, rrp_map: dict[str, float]) -> list[dict]:
    _, yellow, _ = collect_violations(snapshot, rrp_map)
    by_offer = {
        str(item.get("offer_id") or "").strip(): item
        for item in snapshot.get("items", [])
    }
    planned: list[dict] = []
    for violation in yellow:
        offer_id = violation["offer_id"]
        item = by_offer.get(offer_id) or {}
        current = float(item.get("current_price") or 0)
        if current <= 0:
            continue
        new_price = float(ceil(current * RAISE_FACTOR))
        min_price = float(item.get("ozon_min_price") or 0)
        if min_price >= new_price:
            min_price = 0.0
        planned.append({
            "offer_id": offer_id,
            "seller_price_before": current,
            "seller_price_after": new_price,
            "customer_price_before": float(violation["customer_price"]),
            "rrp": float(violation["rrp"]),
            "green_floor": float(violation["floor"]),
            "min_price": min_price,
        })
    return planned


def apply_updates(client, planned: list[dict]) -> list[dict]:
    results: list[dict] = []
    for row in planned:
        payload = {
            "prices": [{
                "offer_id": row["offer_id"],
                "price": str(int(row["seller_price_after"])),
                "min_price": str(int(row["min_price"])) if row["min_price"] > 0 else "0",
                "old_price": "0",
            }]
        }
        try:
            data = client.post("/v1/product/import/prices", payload)
            result = (data.get("result") or [{}])[0]
            updated = bool(result.get("updated"))
            errors = result.get("errors") or []
            results.append({**row, "api_updated": updated, "api_errors": errors})
        except Exception as exc:
            results.append({**row, "api_updated": False, "api_errors": [str(exc)[:500]]})
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="artifacts/ozon_catalog.json")
    ap.add_argument("--output", default="artifacts/auto_fix_yellow.json")
    args = ap.parse_args()

    if os.getenv("AUTO_FIX_YELLOW_ENABLED", "").strip().upper() != "YES":
        print("AUTO_FIX_YELLOW_ENABLED is not YES; no prices changed.")
        return 0

    rrp_map = load_rrp_map()
    if not rrp_map:
        raise RuntimeError("RRP_MAP_B64 is not configured")

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    planned = plan_yellow_updates(snapshot, rrp_map)
    print(f"Yellow-zone seller price updates planned: {len(planned)}")

    if not planned:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text("[]", encoding="utf-8")
        return 0

    from ozon_export.client import OzonClient
    client = OzonClient()
    results = apply_updates(client, planned)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ok = [x for x in results if x["api_updated"]]
    failed = [x for x in results if not x["api_updated"]]
    print(f"Yellow-zone prices updated: {len(ok)}; failed: {len(failed)}")
    for x in ok:
        print(
            f"UPDATED {x['offer_id']}: seller {x['seller_price_before']:.0f} -> "
            f"{x['seller_price_after']:.0f}; customer before {x['customer_price_before']:.0f}; "
            f"green floor {x['green_floor']:.0f}"
        )
    for x in failed:
        print(f"FAILED {x['offer_id']}: {x['api_errors'][:2]}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
