"""Regression tests: Ozon price writes must require deliberate opt-in."""
import pytest

from ozon_agent.auto_fix_yellow import apply_updates
from ozon_agent.promo_reprice import apply


class NeverCallOzon:
    def post(self, endpoint, payload):
        raise AssertionError(f"Unexpected Ozon API write: {endpoint}")


def test_yellow_red_auto_fix_paused_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("OZON_PRICE_WRITES_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="price writes are paused"):
        apply_updates(NeverCallOzon(), [
            {"offer_id": "TEST", "seller_price_after": 100}
        ])


def test_promo_and_restore_paused_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("OZON_PRICE_WRITES_ENABLED", raising=False)
    for mode in ("promo", "restore"):
        with pytest.raises(RuntimeError, match="price writes are paused"):
            apply(NeverCallOzon(), [
                {"offer_id": "TEST", "seller_after": 100, "mode": mode}
            ])
