from yandex_agent.one_shot_reprice import candidates

def test_one_shot_red_and_yellow():
    data = [
        {"offerId":"403", "onDisplay":"3885", "basicPrice":5000},
        {"offerId":"GSH9", "onDisplay":"1776", "basicPrice":3000},
    ]
    mapped={"GSH9":1718}
    plan, skipped=candidates(data,mapped)
    by_sku={x["sku"]:x for x in plan}
    assert by_sku["403"]["seller_after"] == 5626
    assert by_sku["GSH9"]["seller_after"] == 3090
    assert not skipped

def test_exact_red_shortfall():
    data=[{"offerId":"403","onDisplay":"3885","basicPrice":1000}]
    plan, skipped=candidates(data,{})
    assert len(plan)==1
    assert plan[0]["seller_after"]==1626
    assert not skipped

def test_no_clearance():
    plan, skip=candidates([{"offerId":"403УЦ","onDisplay":"100","basicPrice":500}],{})
    assert not plan


def test_large_batch_is_chunked():
    from yandex_agent.one_shot_reprice import apply_one_shot

    class Client:
        def __init__(self):
            self.batches = []

        def request(self, method, path, *, payload):
            assert method == "POST"
            assert path == "/v2/businesses/123/offer-prices/updates"
            self.batches.append(payload["offers"])
            return {"status": "OK"}

    records = [
        {"offerId": f"BULK-{idx}", "onDisplay": 900, "basicPrice": 1000}
        for idx in range(144)
    ]
    rrp_map = {f"BULK-{idx}": 1000 for idx in range(144)}
    planned, skipped = candidates(records, rrp_map)
    assert len(planned) == 144
    assert not skipped
    client = Client()
    apply_one_shot(client, 123, planned)
    assert [len(batch) for batch in client.batches] == [40, 40, 40, 24]
    assert len({entry["offerId"] for batch in client.batches for entry in batch}) == 144
