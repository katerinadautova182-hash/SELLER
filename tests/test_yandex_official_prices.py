import io
import json
import zipfile
import pytest

from yandex_agent.official_prices import extract_report, number


def test_number():
    assert number("13 395 ₽") == 13395
    assert number("5015") == 5015
    assert number("") is None
    assert number("по запросу") is None


def test_json_zip_report():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("business_prices.json", json.dumps({
            "result": {"items": [
                {"offerId": "2020C", "onDisplay": "14920"},
                {"offerId": "201", "onDisplay": "5 015 ₽"}
            ]}}))
    rows = extract_report(buffer.getvalue())
    assert len(rows) == 2
    assert rows[0]["offerId"] == "2020C"


def test_fail_closed_without_on_display():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("data.json", '[{"offerId":"201","price":9200}]')
    with pytest.raises(ValueError):
        extract_report(buffer.getvalue())
