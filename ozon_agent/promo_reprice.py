"""Two-pass helper for the temporary Mark Shmidt Ozon promotion."""
from __future__ import annotations

import argparse
import json
import os
from math import ceil
from pathlib import Path

from ozon_export.client import OzonClient
from .price_alert import load_rrp_map
from .promo_policy import (
    PROMO_END,
    is_promo_offer,
    moscow_date,
    promo_active,
    regular_rrp,
    target_buyer_price,
)
from .telegram import send_telegram

TOLERANCE_RUB = 10.0


def _mode_allowed(mode: str) -> bool:
    day = moscow_date()
    if mode == "promo":
        return promo_active(day)
    # Automatic restore is only for the day immediately after the promo.
    return day.year == 2026 and day.month == 11 and day.day == 21


def plan(snapshot: dict, rrp_map: dict[str, float], mode: str) -> list[dict]:
    rows = []
    for item in snapshot.get("items", []):
        offer_id = str(item.get("offer_id") or "").strip()
        if not is_promo_offer(offer_id):
            continue
        if item.get("eligibility") != "ELIGIBLE":
            continue
        buyer = item.get("customer_price")
        seller = item.get("current_price")
        if not item.get("customer_price_verified") or buyer in (None, 0, 0.0) or seller in (None, 0, 0.0):
            continue
        base_rrp = regular_rrp(offer_id, rrp_map)
        target = target_buyer_price(offer_id, base_rrp, rrp_map, mode=mode)
        if target is None:
            continue
        buyer = float(buyer)
        seller = float(seller)
        if abs(buyer - target) <= TOLERANCE_RUB:
            continue

        # Calibrate from the marketplace's current seller->buyer relationship.
        # A fresh snapshot is used before each pass, so pass 2 corrects any
        # residual marketplace discount/rounding drift from pass 1.
        new_seller = float(max(1, ceil(seller * float(target) / buyer)))
        if int(new_seller) == int(seller):
            new_seller = float(int(seller) + (1 if buyer < target else -1))
        rows.append({
            "offer_id": offer_id,
            "buyer_before": buyer,
            "seller_before": seller,
            "seller_after": new_seller,
            "target_buyer": float(target),
            "regular_rrp": base_rrp,
            "mode": mode,
        })
    return rows


def apply(client: OzonClient, rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        value = int(row["seller_after"])
        payload = {"prices": [{
            "offer_id": row["offer_id"],
            "price": str(value),
            "min_price": str(value),
            "old_price": "0",
        }]}
        try:
            data = client.post("/v1/product/import/prices", payload)
            result = (data.get("result") or [{}])[0]
            out.append({
                **row,
                "updated": bool(result.get("updated")),
                "errors": result.get("errors") or [],
            })
        except Exception as exc:
            out.append({**row, "updated": False, "errors": [str(exc)[:500]]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--mode", choices=("promo", "restore"), required=True)
    ap.add_argument("--round", type=int, required=True)
    args = ap.parse_args()

    if os.getenv("MARK_SHMIDT_PROMO_APPROVED") != "YES":
        raise RuntimeError("Promo repricing disabled: approval flag missing")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not _mode_allowed(args.mode):
        state = {"mode": args.mode, "round": args.round, "skipped": True,
                 "reason": f"date guard; promo ends {PROMO_END.isoformat()}"}
        out_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(state["reason"])
        return 0

    rrp_map = load_rrp_map()
    if not rrp_map:
        raise RuntimeError("RRP_MAP_B64 is not configured")

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    planned = plan(snapshot, rrp_map, args.mode)
    results = apply(OzonClient(), planned) if planned else []
    state = {"mode": args.mode, "round": args.round, "planned": planned, "results": results}
    out_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = [x for x in results if x["updated"]]
    failed = [x for x in results if not x["updated"]]
    print(f"Ozon promo {args.mode} round {args.round}: planned={len(planned)} updated={len(ok)} failed={len(failed)}")
    for x in results:
        status = "OK" if x["updated"] else "FAILED"
        print(
            f"{status} {x['offer_id']}: buyer {x['buyer_before']:.0f} -> target "
            f"{x['target_buyer']:.0f}; seller {x['seller_before']:.0f} -> {x['seller_after']:.0f}"
        )
    # Do not abort the second pass because one SKU failed in the first pass.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
