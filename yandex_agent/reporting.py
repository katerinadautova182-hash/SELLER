"""Readable buyer-price Telegram alerts and safe SKU match suggestions."""
from __future__ import annotations

from difflib import get_close_matches

from ozon_agent.catalog import normalize_sku


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def suggest_rrp(offer_id: str, rrp_map: dict[str, float]) -> list[str]:
    """Suggestions only: NEVER use fuzzy matches as a verified RRP."""
    key = normalize_sku(offer_id)
    if not key or len(key) < 3:
        return []
    keys = list(rrp_map)
    return get_close_matches(key, keys, n=3, cutoff=0.82)


def format_alerts(results: list[dict], stats: dict) -> list[str]:
    """Return bounded Telegram messages; include every RED/YELLOW item."""
    red = sorted(
        (r for r in results if r["status"] == "RED"),
        key=lambda r: (r["display_price"] - r["rrp"], r["offer_id"]),
    )
    yellow = sorted(
        (r for r in results if r["status"] == "YELLOW"),
        key=lambda r: (r["display_price"] - r["target_price"], r["offer_id"]),
    )
    summary = (
        "Яндекс Маркет — контроль цен на витрине\n"
        f"🔴 Ниже РРЦ: {len(red)}\n"
        f"🟡 Ниже РРЦ+5%: {len(yellow)}\n"
        f"✅ Соответствуют: {stats['OK']}\n"
        f"⚪ Нет цены: {stats['PRICE_NOT_NUMERIC']}; нет РРЦ: {stats['MISSING_RRP']}"
    )
    sections = [summary]
    if red:
        sections.append(
            "🔴 Ниже РРЦ\n" + "\n".join(
                f"• {r['offer_id']}: покупатель {money(r['display_price'])} ₽, "
                f"РРЦ {money(r['rrp'])} ₽ → ниже на {money(r['rrp'] - r['display_price'])} ₽"
                for r in red
            )
        )
    if yellow:
        sections.append(
            "🟡 Ниже РРЦ+5%\n" + "\n".join(
                f"• {r['offer_id']}: покупатель {money(r['display_price'])} ₽, "
                f"минимум {money(r['target_price'])} ₽ → не хватает "
                f"{money(r['target_price'] - r['display_price'])} ₽"
                for r in yellow
            )
        )
    if not red and not yellow:
        sections.append("Отклонений по подтверждённым ценам нет.")
    # Keep all violations even if alert exceeds Telegram's 4096-char limit.
    messages = []
    current = ""
    for section in sections:
        for line in section.splitlines():
            candidate = (current + "\n" + line).strip() if current else line
            if len(candidate) > 3900 and current:
                messages.append(current)
                current = line
            else:
                current = candidate
        if current:
            current += "\n"
    if current.strip():
        messages.append(current.strip())
    return messages
