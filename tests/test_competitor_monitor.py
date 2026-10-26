from pathlib import Path
from competitor_monitor.reference_rrp import validate_rrp_reference, REFERENCE_SKUS
from bs4 import BeautifulSoup
from competitor_monitor.monitor import (
    clean_code, load_rrp, match_sku, current_prices, candidate, extract, format_report
)

RRP = {
    clean_code("2020C"): {"sku":"2020C","rrp":14070},
    clean_code("2020C-B"): {"sku":"2020C-B","rrp":18760},
    clean_code("2020C-B-W"): {"sku":"2020C-B-W","rrp":18760},
    clean_code("2020T"): {"sku":"2020T","rrp":10230},
    clean_code("2025CB"): {"sku":"2025CB","rrp":19889},
    clean_code("3000CW-T"): {"sku":"3000CW-T","rrp":17370},
    clean_code("GH2024-1"): {"sku":"GH2024-1","rrp":27455},
}

def test_jrl_rrp_csv_is_complete_and_verified():
    assert Path("competitor_monitor/rrp_jrl.csv").exists()
    prices = load_rrp()
    assert len(prices) == 219 == len(REFERENCE_SKUS)
    assert prices["2020CB"]["rrp"] == 18760
    validate_rrp_reference(prices)

def test_match_full_codes_and_cyrillic_letters():
    assert match_sku("JRL Fresh Fade 2020C",RRP)["sku"]=="2020C"
    assert match_sku("JRL Fresh Fade 2020C-B",RRP)["sku"]=="2020C-B"
    assert match_sku("JRL Fresh Fade 2020C-B-W",RRP)["sku"]=="2020C-B-W"
    assert match_sku("JRL FF2020C-B",RRP)["sku"]=="2020C-B"
    assert match_sku("JRL FreshFade FF2020С-В-W",RRP)["sku"]=="2020C-B-W"

def test_color_aliases():
    assert match_sku("JRL Diamante 2025C Black",RRP)["sku"]=="2025CB"
    assert match_sku("JRL 3000C White",RRP)["sku"]=="3000CW-T"

def test_bundle_not_single_machine():
    assert match_sku("JRL FreshFade 2020C + зарядная база",RRP) is None
    assert match_sku("JRL 2020C X1",RRP) is None
    assert match_sku("JRL Onyx Ghost Collection GH2024-1",RRP)["sku"]=="GH2024-1"

def test_exclude_struck_out_prices():
    soup=BeautifulSoup('<div><h3>JRL 2020C-B</h3><span>15 990 ₽</span><s>18 760 ₽</s><span class="price-old">20 000 ₽</span></div>',"html.parser")
    assert current_prices(soup.div)==[15990.0]

def test_thousands_with_dot_and_decimal_rubles():
    soup=BeautifulSoup("<div>8.990₽ <span>13990.00р.</span></div>","html.parser")
    assert current_prices(soup.div)==[8990.0,13990.0]

def test_no_stock_not_flagged():
    soup=BeautifulSoup("<div><h3>JRL FreshFade 2020C</h3><span>11 990 ₽</span>Нет в наличии</div>","html.parser")
    assert candidate(soup.div,"https://barberkit.ru/mashinki/",RRP,"card")["status"]=="OUT_OF_STOCK"

def test_discount_and_link():
    soup=BeautifulSoup('<div class="product-item"><a href="/item">JRL FreshFade 2020C-B</a><span>15 990 ₽</span><del>18 760 ₽</del></div>',"html.parser")
    found,urls=extract(soup,"https://barberkit.ru/mashinki/",RRP)
    assert any(r["status"]=="MATCHED" and r["price"]==15990 for r in found)
    assert "https://barberkit.ru/item" in urls

def test_variant_ambiguous():
    soup=BeautifulSoup('<div class="product-item"><h3>JRL Diamante 2025CB Black</h3><span>18 990 ₽</span><span>19 990 ₽</span></div>',"html.parser")
    assert candidate(soup.div,"https://jrl-pro.ru/",RRP,"card")["status"]=="MULTIPLE_PRICES"

def test_blocked_site_no_false_all_clear():
    x={"site":"Example","url":"https://example.ru/","items":[],"violations":[],
       "unresolved":[],"pages_ok":0,"pages_attempted":2,"errors":["403"]}
    assert "НЕ завершена" in format_report(x)


def test_accessory_code_reference_cannot_become_main_equipment():
    assert match_sku("Зарядная подставка JRL для машинок 2020C и триммера 2020T", RRP) is None
    assert match_sku("Ножевой блок Fade Blade JRL 2020C", RRP) is None
    assert match_sku("Набор двойных магнитных насадок для машинок JRL 2020C", RRP) is None
    assert match_sku("Набор магнитных насадок для машинок 2020C/2020T", RRP) is None
    assert match_sku("Ножевой блок для триммера JRL 2025T", RRP) is None


def test_accessory_detail_cannot_attribute_other_card_price_to_clipper():
    soup = BeautifulSoup(
        '<h1>Зарядная подставка JRL для машинок 2020C, 2020T</h1>'
        '<div class="product-card"><a href="/clipper">JRL FF2020C</a><span>2 200 ₽</span></div>',
        "html.parser",
    )
    found, _ = extract(soup, "https://jrlrus.ru/aksessuari/zaryadnaya-podstavka", RRP)
    assert not any(row["sku"] == "2020C" and row["status"] == "MATCHED" for row in found)


def test_main_clippers_remain_matchable():
    assert match_sku("Машинка JRL Fresh Fade 2020C", RRP)["sku"] == "2020C"
    assert match_sku("Машинка для стрижки JRL FF2020C-B", RRP)["sku"] == "2020C-B"


def test_accessory_detail_slug_cannot_impersonate_clipper():
    from competitor_monitor.monitor import url_compatible
    assert not url_compatible(
        "https://jrl-pro.ru/product/nabor-dvojnyh-magnitnyh-nasadok-dlya-mashinok-jrl.html",
        "2020C",
    )
    assert not url_compatible(
        "https://jrlrus.ru/aksessuari/prochie-aksessuari/zaryadnaya-podstavka-jrl-2020c",
        "2020C",
    )
    assert url_compatible("https://jrl-pro.ru/product/onyx-2020c-b.html", "2020C-B")
    assert url_compatible(
        "https://jrl-pro.ru/product/nozhevoj-blok-dlya-trimmera-jrl-2025t-jrl-sf11.html",
        "SF11",
    )


def test_accessory_page_no_false_device_even_without_h1():
    soup = BeautifulSoup(
        '<div class="product-card"><a href="/product/nabor-dvojnyh-magnitnyh-nasadok-dlya-mashinok-jrl.html">'
        'JRL FF2020C</a><span>2 090 ₽</span></div>', "html.parser")
    rows, _ = extract(soup, "https://jrl-pro.ru/", RRP)
    assert not any(r["sku"] == "2020C" and r["status"] == "MATCHED" for r in rows)

def test_report_starts_with_site_and_legal_entity():
    data = {"site": "BarberKit", "url": "https://barberkit.ru/",
            "legal_entity": "ИП Хайбуллин Альберт Артурович",
            "verified": True, "items": [], "violations": [],
            "unresolved": [{"sku": "", "status": "UNMATCHED_SKU"}],
            "pages_ok": 1, "pages_attempted": 1, "errors": []}
    body = format_report(data)
    assert body.splitlines()[0] == "barberkit.ru — ИП Хайбуллин Альберт Артурович"
    assert "ТЕСТОВАЯ ПРОВЕРКА" not in body
    assert "Без надёжного сопоставления" not in body

def test_site_count_and_legal_data_saved():
    import json
    sites = json.loads(Path("competitor_monitor/sites.json").read_text(encoding="utf-8"))["sites"]
    active = [x for x in sites if x.get("enabled", True)]
    assert len(sites) == 10
    assert len(active) == 9
    assert len({x["url"] for x in sites}) == len(sites)
    assert all(x.get("legal_entity") and x.get("inn") for x in active)
    assert any("shevelura23.ru" in x["url"] for x in active)
    assert any(x.get("platform") == "avito" and x["enabled"] is False for x in sites)


def test_forwardable_report_lists_product_price_and_rrp():
    row = {"sku": "2020C-B", "name": "Машинка JRL ONYX 2020C-B",
           "price": 17990, "rrp": 18760,
           "url": "https://barberkit.ru/onyx", "price_conflict": False}
    ok = {"sku": "2020C", "name": "Машинка JRL Fresh Fade 2020C",
          "price": 15000, "rrp": 14070,
          "url": "https://barberkit.ru/fade", "price_conflict": False}
    report = {"site": "BarberKit", "url": "https://barberkit.ru/",
              "legal_entity": "ИП Хайбуллин Альберт Артурович",
              "verified": True, "items": [row, ok], "violations": [row],
              "pages_ok": 12, "pages_attempted": 12,
              "errors": [], "unresolved": [{"status": "UNMATCHED_SKU"}]}
    msg = format_report(report)
    assert msg.startswith("barberkit.ru — ИП Хайбуллин Альберт Артурович")
    assert "Машинка JRL ONYX 2020C-B" in msg
    assert "Цена на сайте: 17 990 ₽" in msg
    assert "РРЦ: 18 760 ₽" in msg
    assert "Ниже РРЦ на 770 ₽" in msg
    assert "https://barberkit.ru/onyx" in msg
    assert "Машинка JRL Fresh Fade" not in msg
    assert "БЕЗ НАДЁЖНОГО" not in msg.upper()
    assert "БЕЗ НАДЕЖНОГО" not in msg.upper()
    assert "ТЕСТОВ" not in msg.upper()
    assert "GitHub Actions" not in msg

    full = format_report(report, max_chars=None, only_violations=False)
    assert "Машинка JRL Fresh Fade" in full


def test_forwardable_telegram_report_never_cuts_mid_product():
    row = {"sku": "2020C-B", "name": "Машинка JRL ONYX 2020C-B",
           "price": 17990, "rrp": 18760,
           "url": "https://example.ru/onyx", "price_conflict": False}
    items = [{**row, "sku": f"SKU{i}"} for i in range(300)]
    report = {"site": "Shop", "url": "https://example.ru/",
              "legal_entity": "ООО «Магазин»", "verified": True,
              "items": items, "violations": items, "unresolved": [],
              "pages_ok": 3, "pages_attempted": 3, "errors": []}
    msg = format_report(report)
    assert len(msg) <= 3850
    assert "Остальные позиции:" in msg
    assert msg.count("Цена на сайте:") == msg.count("\nРРЦ: ")


def test_blades_are_not_clippers_even_with_compatibility_model_code():
    from competitor_monitor.monitor import is_compatible, match_sku
    assert not is_compatible("Ножевой блок Fade Blade для машинки JRL FF2020C", "2020C")
    assert not is_compatible("Зарядная база JRL для 2020C-B", "2020C-B")
    assert not is_compatible("Машинка JRL 2020C с ножом BF03", "BF03")
    assert is_compatible("Ножевой блок JRL Standard BF03", "BF03")
    assert is_compatible("Машинка JRL Fresh Fade FF2020C", "2020C")
    assert is_compatible("Триммер JRL 2020T-B", "2020T-B")
    blade_rrp = {clean_code("BF03"): {"sku": "BF03", "rrp": 3051}}
    assert match_sku("Машинка JRL 2020C с ножом BF03", blade_rrp) is None
    assert match_sku("Ножевой блок JRL BF03", blade_rrp)["sku"] == "BF03"


def test_pagination_walks_numbered_pages_and_next():
    from competitor_monitor.crawler import pagination_links
    base = "https://barberkit.ru/mashinki/"
    soup = BeautifulSoup(
      '<nav class="pagination">'
      '<a href="/mashinki/?page=2">2</a>'
      '<a href="/mashinki/?page=3" rel="next">Следующая</a>'
      '</nav><a href="/unrelated/">3</a>', "html.parser")
    links = pagination_links(soup, base, "https://barberkit.ru/")
    assert "https://barberkit.ru/mashinki/?page=2" in links
    assert "https://barberkit.ru/mashinki/?page=3" in links
    assert all("/unrelated/" not in x for x in links)


def test_unknown_coverage_is_not_called_complete():
    report = {"site": "Sample", "url": "https://example.ru/",
              "legal_entity": "ООО «Пример»", "verified": True,
              "coverage": "INCOMPLETE", "pages_ok": 4, "pages_attempted": 5,
              "items": [{"sku": "2020C", "name": "Машинка JRL 2020C",
                         "rrp": 14070, "price": 14070, "url": "https://example.ru/item"}],
              "unresolved": [], "errors": []}
    body = format_report(report)
    assert "ПРОВЕРКА НЕПОЛНА" in body
    assert "отсутствие нарушений не подтверждено" in body


def test_shevelura_url_and_legal_info():
    import json
    cfg = json.loads(Path("competitor_monitor/sites.json").read_text("utf-8"))
    shops = {x["name"]: x for x in cfg["sites"]}
    assert shops["Shevelura23"]["inn"] == "290221495001"
    assert shops["Shevelura23"]["legal_entity"] == "ИП Зотова Эрика Олеговна"
