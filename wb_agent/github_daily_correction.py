"""GitHub Actions: daily buyer-only WB snapshot -> bounded WB Seller API update.

Price snapshots committed to the public repository contain public buyer prices
ONLY. Seller prices, private RRP, token are fetched in the action's environment.
One SKU is corrected at most once until a separate verification cycle.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .csv_rrp_control import csv_rows, load_reference, numeric
from .first_pass_controller import (
    LEDGER, catalog_by_id, one_base, posted, propose, read_ledger
)

EXPECTED_COLUMNS = {
    "nm_id", "sku", "buyer_min_rub", "read_status", "checked_at", "source"
}
MAX_DAILY_HOURS = 36
MAX_DAILY_SKUS = 30
ALLOWED_SOURCE = "wb_live_dom_min_price"


def buyer_only(row):
    """Strict validation of a Git-tracked, PUBLIC snapshot row."""
    keys = set(row)
    # A raw SELLER WB Auto export includes confidential seller price
    # fields. Refuse unredacted files in the GitHub route.
    if any(str(k).lower().startswith("seller_") for k in keys):
        raise ValueError("Refuse unredacted seller-price columns in a public GitHub snapshot")
    if not EXPECTED_COLUMNS.issubset(keys):
        raise ValueError("Missing buyer-price snapshot columns")
    clean = {k: str(row.get(k) or "").strip() for k in EXPECTED_COLUMNS}
    try:
        nm_id = int(clean["nm_id"])
    except ValueError:
        raise ValueError("Invalid nm_id") from None
    if nm_id <= 0 or nm_id >= 10**15:
        raise ValueError("Invalid nm_id range")
    if clean["read_status"] not in {"OK", "ERROR", "SKIP_OUT_OF_STOCK"}:
        raise ValueError("Unrecognized buyer scan status")
    if clean["read_status"] == "OK":
        if clean["source"] != ALLOWED_SOURCE or numeric(clean["buyer_min_rub"]) is None:
            raise ValueError("OK row without verified WB live DOM price")
    else:
        clean["buyer_min_rub"] = ""
    return clean


def load_public_snapshot(path):
    rows = csv_rows(path)
    if not rows:
        raise ValueError("WB buyer snapshot is empty")
    seen = set()
    clean = []
    for raw in rows:
        row = buyer_only(raw)
        nm = int(row["nm_id"])
        if nm in seen:
            raise ValueError("Duplicate nmID in WB buyer snapshot")
        seen.add(nm)
        clean.append(row)
    return clean


def enrich(row, current):
    """Seller prices come ONLY from the current authorized WB Seller API."""
    copy = dict(row)
    base, reason = one_base(current)
    if base is not None:
        copy["seller_base_rub"] = str(base)
        disc_prices = set()
        for size in current.get("sizes") or []:
            discount = numeric(size.get("discountedPrice"))
            if discount is not None:
                disc_prices.add(discount)
        if len(disc_prices) == 1:
            copy["seller_discounted_rub"] = str(disc_prices.pop())
    return copy


def make_plan(rows, catalog, rrp, now, ledger, *,
              hours=MAX_DAILY_HOURS, max_step_pct=Decimal("5"),
              max_candidates=MAX_DAILY_SKUS):
    evaluated = []
    for row in rows:
        nm = int(row["nm_id"])
        current = catalog.get(nm)
        enriched = enrich(row, current or {})
        # The seller's base and discounted price are read at planning time,
        # NEVER trusted from a public file. The browser observation can be
        # up to 36 hours old as explicitly requested by the seller.
        candidate = propose(enriched, catalog, rrp, now,
            max_age_minutes=hours * 60, max_change_pct=max_step_pct)
        if candidate.get("status") == "READY" and str(nm) in ledger:
            candidate["status"] = "SKIP"
            candidate["reason"] = "PREVIOUS_BATCH_NOT_VERIFIED"
        evaluated.append(candidate)
    ready = [r for r in evaluated if r.get("status") == "READY"]
    ready.sort(key=lambda r: (
        abs(r["ideal_buyer"] / r["buyer_observed"] - 1),
        abs(r["delta_pct"]), int(r["nm_id"])
    ))
    chosen = ready[:max_candidates]
    for row in ready[max_candidates:]:
        row["status"] = "DEFERRED_DAILY_CAP"
    return evaluated, chosen


def scrub_report(evaluated, chosen, *, mode, upload_id=None):
    # Public GitHub Actions logs/report MUST NOT reveal base/discount prices.
    stats = Counter(row["status"] for row in evaluated)
    return {
        "mode": mode, "total": len(evaluated), "states": dict(stats),
        "selected": len(chosen),
        "selected_articles": [r["nm_id"] for r in chosen],
        "upload_task_id": upload_id,
        "note": "Upload task accepted is NOT verification of published buyer price"
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="data/wb_buyer_latest.csv")
    parser.add_argument("--max-age-hours", type=int, default=36)
    parser.add_argument("--max-step-pct", type=float, default=5)
    parser.add_argument("--max-candidates", type=int, default=30)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.max_age_hours <= 48:
        parser.error("Maximum buyer snapshot age must be 1-48 hours")
    if not 1 <= args.max_candidates <= 30:
        parser.error("First pass at most 30 SKU")
    if not 0.5 <= args.max_step_pct <= 5:
        parser.error("First pass step cannot exceed 5%")

    rows = load_public_snapshot(args.csv)
    rrp = load_reference()
    token = os.environ.get("WB_API_KEY", "").strip()
    if not token:
        raise RuntimeError("WB_API_KEY GitHub secret is missing; no writes made")
    catalog = catalog_by_id(token)
    ledger = read_ledger()
    evaluated, chosen = make_plan(
        rows, catalog, rrp, datetime.now(timezone.utc), ledger,
        hours=args.max_age_hours,
        max_step_pct=Decimal(str(args.max_step_pct)),
        max_candidates=args.max_candidates)
    report = scrub_report(evaluated, chosen, mode="APPLY" if args.apply else "DRY_RUN")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not args.apply or not chosen:
        return 0
    if os.getenv("WB_GITHUB_FIRST_PASS_APPROVED") != "YES":
        raise RuntimeError("WB_GITHUB_FIRST_PASS_APPROVED missing; no writes made")
    # Read current Seller API again immediately before writing. If changed,
    # never guess. Match price, seller discount, article, and sizes.
    fresh = catalog_by_id(token)
    for row in chosen:
        nm = int(row["nm_id"])
        item = fresh.get(nm)
        if item is None:
            raise RuntimeError(f"WB offer disappeared: {nm}")
        base, reason = one_base(item)
        if base != row["seller_before"]:
            raise RuntimeError(f"WB seller base changed immediately before write: {nm}")
        original = catalog.get(nm)
        if original is None or (item.get("vendorCode") != original.get("vendorCode")):
            raise RuntimeError(f"WB offer identity changed before write: {nm}")
        a = {(s.get("price"), s.get("discountedPrice")) for s in original.get("sizes") or []}
        b = {(s.get("price"), s.get("discountedPrice")) for s in item.get("sizes") or []}
        if a != b:
            raise RuntimeError(f"WB seller discount changed before write: {nm}")

    # Ensure the job can never silently submit the same nmID twice across
    # daily runs. GitHub Actions restores/saves the ledger in its cache.
    for row in chosen:
        ledger[str(row["nm_id"])] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "state": "REQUEST_IN_PROGRESS"
        }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    data = [{"nmID": int(row["nm_id"]), "price": int(row["seller_proposed"])}
            for row in chosen]
    response = posted(token, "/api/v2/upload/task", {"data": data}, method="POST")
    upload_id = (response.get("data") or {}).get("id")
    if not upload_id:
        raise RuntimeError("Price task returned no id; check status before retrying")
    for row in chosen:
        ledger[str(row["nm_id"])]["state"] = "SUBMITTED_PENDING_VERIFICATION"
        ledger[str(row["nm_id"])]["upload_id"] = upload_id
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(scrub_report(evaluated, chosen, mode="TASK_SUBMITTED_NOT_VERIFIED",
                                  upload_id=upload_id), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
