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
    assert load_rrp()["2020CB"]["rrp"] == 18760

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
