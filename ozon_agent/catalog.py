"""SKU catalog and alias resolver for the Ozon margin agent.

The agent works with canonical SKU keys and keeps source provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CatalogItem:
    sku_canonical: str
    purchase_cost: float | None
    rrp: float | None
    brand: str | None = None
    name: str | None = None
    purchase_source: str | None = None
    rrp_source: str | None = None


def normalize_sku(value: str | None) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    # Normalize visually similar Cyrillic characters often present in marketplace exports.
    tr = str.maketrans({
        "А":"A","В":"B","С":"C","Е":"E","Н":"H","К":"K","М":"M","О":"O","Р":"P","Т":"T","Х":"X",
        "а":"a","в":"b","с":"c","е":"e","н":"h","к":"k","м":"m","о":"o","р":"p","т":"t","х":"x",
    })
    s = s.translate(tr).upper()
    return "".join(ch for ch in s if ch.isalnum())


class Catalog:
    def __init__(self, aliases: dict[str, str], items: dict[str, CatalogItem]) -> None:
        self.aliases = aliases
        self.items = items

    @classmethod
    def from_rows(cls, alias_rows: Iterable[dict], rrp_rows: Iterable[dict]) -> "Catalog":
        aliases: dict[str, str] = {}
        items: dict[str, CatalogItem] = {}

        for row in alias_rows:
            variant = str(row.get("sku_variant") or "").strip()
            canonical = str(row.get("sku_canonical") or "").strip()
            if not canonical:
                continue
            key = normalize_sku(variant or canonical)
            aliases[key] = canonical
            cost_raw = row.get("purchase_cost")
            cost = None
            try:
                if cost_raw not in (None, ""):
                    cost = float(str(cost_raw).replace(",", "."))
            except ValueError:
                cost = None

            prev = items.get(canonical)
            items[canonical] = CatalogItem(
                sku_canonical=canonical,
                purchase_cost=cost if cost is not None else (prev.purchase_cost if prev else None),
                rrp=prev.rrp if prev else None,
                brand=prev.brand if prev else None,
                name=prev.name if prev else None,
                purchase_source=str(row.get("source") or "") or (prev.purchase_source if prev else None),
                rrp_source=prev.rrp_source if prev else None,
            )

        for row in rrp_rows:
            article = str(row.get("Артикул") or row.get("article") or "").strip()
            if not article:
                continue
            canonical = aliases.get(normalize_sku(article), article)
            aliases.setdefault(normalize_sku(article), canonical)

            rrp_raw = row.get("РРЦ, руб.") if "РРЦ, руб." in row else row.get("rrp")
            rrp = None
            try:
                if rrp_raw not in (None, ""):
                    rrp = float(str(rrp_raw).replace(" ", "").replace(",", "."))
            except ValueError:
                rrp = None

            prev = items.get(canonical)
            items[canonical] = CatalogItem(
                sku_canonical=canonical,
                purchase_cost=prev.purchase_cost if prev else None,
                rrp=rrp if rrp is not None else (prev.rrp if prev else None),
                brand=str(row.get("Бренд") or row.get("brand") or "") or (prev.brand if prev else None),
                name=str(row.get("Наименование") or row.get("name") or "") or (prev.name if prev else None),
                purchase_source=prev.purchase_source if prev else None,
                rrp_source="База РРЦ — Mark Shmidt & JRL",
            )

        return cls(aliases=aliases, items=items)

    def resolve(self, offer_id: str | None) -> CatalogItem | None:
        key = normalize_sku(offer_id)
        canonical = self.aliases.get(key)
        if canonical is None:
            return None
        return self.items.get(canonical)
