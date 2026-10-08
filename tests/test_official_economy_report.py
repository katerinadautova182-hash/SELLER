import os
os.environ.setdefault("OZON_CLIENT_ID","test")
os.environ.setdefault("OZON_API_KEY","test")

from ozon_agent.official_economy_report import calculate

def row(offer,sku,cost,delivered,returned,revenue,points,partners,expenses,profit):
    return {
        "Артикул":offer,"SKU":sku,"Название товара":offer,
        "Себестоимость":cost,
        "Доставлено товаров, шт":delivered,
        "Возвращено товаров, шт":returned,
        "Выручка":revenue,
        "Баллы за скидки":points,
        "Программы партнёров":partners,
        "Прибыль за период":profit,
        "Вознаграждение Ozon":expenses,
    }

def test_ms9500_matches_ozon_model_but_uses_private_cogs():
    src=row("MS9500","496958132",1086,7,0,18503.71,8890.45,45.84,-16033.31,3804.69)
    costs={"MS9500":{"cost":805.47,"source":"owner","verified":True}}
    r=calculate([src],costs)[0]
    assert r["landed_unit_cost_rub"] == 1087.38
    assert r["cogs_rub"] == 7611.69
    assert r["contribution_profit_rub"] == 3795.00

def test_returned_units_do_not_consume_cogs():
    # Ozon report profit = base + expenses - 943*15 = 3906.19.
    src=row("MS101","428005495",943,19,4,22679.43,25337.39,244.18,-30209.81,3906.19)
    costs={"MS101":{"cost":697.96,"source":"owner","verified":True}}
    r=calculate([src],costs)[0]
    assert r["net_units"] == 15
    assert r["cogs_rub"] == 14133.69
    assert r["contribution_profit_rub"] == 3917.50

def test_combo_private_cogs_can_turn_ozon_profit_negative():
    src=row("MS9903R+ms101","5826360669",2043,5,0,11962.52,13917.86,119.62,-15071.74,713.26)
    costs={"MS9903R+MS101":{"cost":1709.23,"source":"owner","verified":True}}
    r=calculate([src],costs)[0]
    assert r["cogs_rub"] == 11537.30
    assert r["contribution_profit_rub"] == -609.04
