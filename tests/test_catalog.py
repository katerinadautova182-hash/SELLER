from ozon_agent.catalog import Catalog, normalize_sku
from ozon_agent.reconcile import reconcile_offer_ids


def test_normalize_cyrillic_c_to_latin_c():
    assert normalize_sku("2020С") == normalize_sku("2020C")


def test_alias_resolves_to_canonical_with_cost_and_rrp():
    catalog = Catalog.from_rows(
        alias_rows=[
            {"sku_variant":"2020С","sku_canonical":"2020C","purchase_cost":"3267.73","source":"AUTO_ALIAS"},
            {"sku_variant":"2020c","sku_canonical":"2020C","purchase_cost":"3267.73","source":"AUTO_ALIAS"},
        ],
        rrp_rows=[
            {"Бренд":"JRL","Артикул":"2020C","Наименование":"Clipper","РРЦ, руб.":"15000"}
        ],
    )
    item = catalog.resolve("2020c")
    assert item is not None
    assert item.sku_canonical == "2020C"
    assert item.purchase_cost == 3267.73
    assert item.rrp == 15000


def test_reconcile_flags_missing_data():
    catalog = Catalog.from_rows(
        alias_rows=[{"sku_variant":"A1","sku_canonical":"A1","purchase_cost":"","source":"AUTO_ALIAS"}],
        rrp_rows=[],
    )
    rows = reconcile_offer_ids(["A1","UNKNOWN"], catalog)
    assert rows[0].status == "MISSING_BOTH"
    assert rows[1].status == "UNMATCHED"
