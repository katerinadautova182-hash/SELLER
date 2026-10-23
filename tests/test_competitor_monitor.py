import base64
import json
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

def test_rrp_secret_shape(monkeypatch):
    data={"2020C":14070,"2020C-B":18760}
    monkeypatch.setenv("RRP_MAP_B64", base64.b64encode(json.dumps(data).encode()).decode())
    assert load_rrp(validate_reference=False)["2020CB"]["rrp"] == 18760

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



def test_manager_message_has_real_name_price_rrp_and_no_technical_line():
    item = {"sku":"2020C-B","name":"Машинка JRL ONYX 2020C-B",
            "price":17990,"rrp":18760,"url":"https://example.ru/onyx",
            "price_conflict":False}
    good = {"sku":"2020C","name":"Машинка JRL 2020C",
            "price":15000,"rrp":14070,"url":"https://example.ru/fade",
            "price_conflict":False}
    report = {"site":"BarberKit","url":"https://barberkit.ru/",
              "items":[item,good],"unresolved":[{"status":"UNMATCHED_SKU"}],
              "pages_ok":10,"pages_attempted":10,"errors":[]}
    msg = format_report(report)
    assert "Машинка JRL ONYX 2020C-B" in msg
    assert "Цена на сайте: 17 990 ₽" in msg
    assert "РРЦ: 18 760 ₽" in msg
    assert "Ниже РРЦ на 770 ₽" in msg
    assert "https://example.ru/onyx" in msg
    assert "БЕЗ НАДЕЖНОГО" not in msg.upper()
    assert "НЕОДНОЗНАЧН" not in msg.upper()
    assert "ТЕСТОВ" not in msg.upper()
    assert "Машинка JRL 2020C" not in msg
    assert "GitHub Actions" not in msg

    full = format_report(report, max_chars=None, only_violations=False)
    assert "Машинка JRL 2020C" in full


def test_telegram_does_not_cut_product_rows():
    item = {"sku":"2020C-B","name":"Машинка JRL ONYX 2020C-B",
            "price":17990,"rrp":18760,"url":"https://example.ru/onyx",
            "price_conflict":False}
    report = {"site":"Shop","url":"https://example.ru/",
              "items":[{**item,"sku":f"SKU{i}"} for i in range(300)],
              "unresolved":[],"pages_ok":1,"pages_attempted":1,"errors":[]}
    msg = format_report(report)
    assert len(msg) <= 3850
    assert "Остальные позиции:" in msg
    assert msg.count("Цена на сайте:") == msg.count("РРЦ:")


def test_reference_check_rejects_missing_or_wrong_prices(tmp_path):
    from competitor_monitor.monitor import verify_rrp_reference, fingerprint_rrp
    rows={"2020C":{"sku":"2020C","rrp":14070},
          "2020CB":{"sku":"2020C-B","rrp":18760}}
    ref={"sku_codes":["2020C","2020CB"],"sku_count":2,
         "checked_on":"2026-10-23","fnv1a64_cents":fingerprint_rrp(rows,["2020C","2020CB"])}
    path=tmp_path/"ref.json"
    path.write_text(json.dumps(ref))
    verify_rrp_reference(rows,path)

    with __import__("pytest").raises(RuntimeError,match="отсутствует"):
        verify_rrp_reference({"2020C":rows["2020C"]},path)
    with __import__("pytest").raises(RuntimeError,match="не совпадают"):
        verify_rrp_reference({**rows,"2020CB":{**rows["2020CB"],"rrp":18000}},path)


def test_official_reference_contains_219_unique_skus():
    from pathlib import Path
    meta = json.loads(Path("competitor_monitor/rrp_reference.json").read_text(encoding="utf-8"))
    assert meta["brand"]=="JRL"
    assert len(meta["sku_codes"])==219
    assert len(set(meta["sku_codes"]))==219
