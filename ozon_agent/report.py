"""Build SKU readiness reports for Ozon margin calculations."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict

from .reconcile import reconcile_offer_ids


def build_readiness_report(offer_ids, catalog) -> dict:
    rows = reconcile_offer_ids(offer_ids, catalog)
    counts = Counter(r.status for r in rows)
    return {
        "summary": {
            "total": len(rows),
            "ready": counts.get("READY", 0),
            "missing_cost": counts.get("MISSING_COST", 0),
            "missing_rrp": counts.get("MISSING_RRP", 0),
            "missing_both": counts.get("MISSING_BOTH", 0),
            "unmatched": counts.get("UNMATCHED", 0),
        },
        "issues": [asdict(r) for r in rows if r.status != "READY"],
        "rows": [asdict(r) for r in rows],
    }
