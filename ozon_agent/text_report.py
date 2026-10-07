"""Plain-text alert formatting for Ozon margin agent."""
from __future__ import annotations

def _money(v):
    return f"{float(v):,.0f}".replace(",", " ") + " ₽"

def build_text_alert(*, rrp_violations: list[dict], margin_rows: list[dict],
                     unmatched_rrp: int = 0, unmatched_cost: int = 0,
                     max_items: int = 12) -> str:
    lines = ["Ozon — что исправить"]

    if rrp_violations:
        lines.append("")
        lines.append(f"🔴 РРЦ: {len(rrp_violations)} нарушений")
        for r in rrp_violations[:max_items]:
            need = max(0, float(r["rrp_floor"]) - float(r["customer_price"]))
            lines.append(
                f"• {r['offer_id']}: покупатель {_money(r['customer_price'])}, "
                f"минимум {_money(r['rrp_floor'])} → поднять минимум на {_money(need)}"
            )
    else:
        lines += ["", "✅ Нарушений РРЦ+5% нет"]

    negative = sorted(
        [r for r in margin_rows if r.get("profit_before_tax_ads_rub", 0) < 0],
        key=lambda x: x["profit_before_tax_ads_rub"],
    )
    if negative:
        lines.append("")
        lines.append(f"🔴 Маржа до налога и рекламы: {len(negative)} убыточных SKU")
        for r in negative[:max_items]:
            lines.append(
                f"• {r['offer_id']}: {r['margin_before_tax_ads_percent']:.1f}% "
                f"({_money(r['profit_before_tax_ads_rub'])}/шт) → пересмотреть цену/расходы"
            )

    positive = sorted(
        [r for r in margin_rows if r.get("profit_before_tax_ads_rub", 0) >= 0],
        key=lambda x: x.get("margin_before_tax_ads_percent", 999),
    )
    if positive:
        lines.append("")
        lines.append("⚠️ Самая низкая положительная маржа")
        for r in positive[:5]:
            lines.append(
                f"• {r['offer_id']}: {r['margin_before_tax_ads_percent']:.1f}% "
                f"({_money(r['profit_before_tax_ads_rub'])}/шт)"
            )

    if unmatched_rrp or unmatched_cost:
        lines.append("")
        lines.append(
            f"⚠️ Не хватает данных: РРЦ — {unmatched_rrp}, закупка — {unmatched_cost}"
        )
    return "\n".join(lines)
