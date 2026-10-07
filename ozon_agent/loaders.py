"""Load reference data exported from Google Drive / CSV files."""
from __future__ import annotations

import csv
from pathlib import Path


def load_alias_csv(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_rrp_csv(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))
