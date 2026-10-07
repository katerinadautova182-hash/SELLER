from ozon_agent.catalog import Catalog
from ozon_agent.report import build_readiness_report


def test_readiness_summary():
    catalog = Catalog.from_rows(
        alias_rows=[
            {"sku_variant":"A","sku_canonical":"A","purchase_cost":"100","source":"x"},
            {"sku_variant":"B","sku_canonical":"B","purchase_cost":"","source":"x"},
        ],
        rrp_rows=[
            {"Артикул":"A","РРЦ, руб.":"200"},
            {"Артикул":"B","РРЦ, руб.":"300"},
        ],
    )
    report = build_readiness_report(["A","B","C"], catalog)
    assert report["summary"] == {
        "total": 3,
        "ready": 1,
        "missing_cost": 1,
        "missing_rrp": 0,
        "missing_both": 0,
        "unmatched": 1,
    }
