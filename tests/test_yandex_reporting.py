from yandex_agent.reporting import format_alerts, suggest_rrp

def test_alert():
    rows = [{"offer_id": "403", "status": "RED", "display_price": 3885, "rrp": 4511, "target_price": 4737}]
    stats = {"OK": 0, "PRICE_NOT_NUMERIC": 0, "MISSING_RRP": 0}
    text = "\n".join(format_alerts(rows, stats))
    assert "ниже на 626 ₽" in text
    assert "3 885 ₽" in text

def test_pagination():
    rows = [{"offer_id": f"ITEM{i}", "status": "RED", "display_price": 100, "rrp": 150, "target_price": 158} for i in range(200)]
    stats = {"OK": 0, "PRICE_NOT_NUMERIC": 0, "MISSING_RRP": 0}
    messages = format_alerts(rows, stats)
    assert len(messages) > 1
    assert all(len(x) < 4096 for x in messages)
    assert sum(x.count("покупатель") for x in messages) == 200

def test_rrp_suggestions():
    assert suggest_rrp("JRL-BR04-25", {"JRLBR0425": 1076.0}) == ["JRLBR0425"]

def test_alias_rrp_lookup():
    from yandex_agent.price_check import resolve_rrp
    assert resolve_rrp("801з", {}) == (9824.0, "alias_manual")
    assert resolve_rrp("JRL-BR1-32", {"BR132MM": 1902.0})[0] == 1902.0
    assert resolve_rrp("JRL-BR1-53", {"BR153MM": 2178.0})[0] == 2178.0
