"""Read-only FBS stock lookup. Fail closed if either WB API permission is unavailable."""
import json
import urllib.request
from collections import defaultdict

CONTENT = "https://content-api.wildberries.ru/content/v2/get/cards/list"
MARKETPLACE = "https://marketplace-api.wildberries.ru"


def request(url, token, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": token, "Content-Type": "application/json",
        "Accept": "application/json"}, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=35) as response:
        return json.load(response)


def get_card_size_ids(token):
    """Map nmID to official product chrtId(s), distinct from price sizeID."""
    found = defaultdict(set)
    cursor = {"limit": 100}
    for _ in range(150):
        result = request(CONTENT, token, {
            "settings": {"cursor": cursor, "filter": {"withPhoto": -1}}})
        cards = result.get("cards")
        if not isinstance(cards, list):
            raise ValueError("WB Content API returned no cards array")
        for card in cards:
            nm_id = str(card.get("nmID", ""))
            for size in card.get("sizes") or []:
                chrt = size.get("chrtID")
                if nm_id and chrt is not None:
                    found[nm_id].add(int(chrt))
        if len(cards) < 100:
            return dict(found)
        last = result.get("cursor") or {}
        if not last.get("updatedAt") or not last.get("nmID"):
            raise ValueError("WB Content API pagination cursor missing")
        cursor = {"limit": 100, "updatedAt": last["updatedAt"], "nmID": last["nmID"]}
    raise RuntimeError("Too many WB card pages")


def available_nmids(marketplace_token, content_token):
    mapping = get_card_size_ids(content_token)
    if not mapping:
        raise ValueError("No WB product cards with chrtID")
    warehouses = request(MARKETPLACE + "/api/v3/warehouses", marketplace_token)
    if not isinstance(warehouses, list):
        raise ValueError("Unexpected warehouse list")
    ids = sorted(set().union(*mapping.values()))
    qty = defaultdict(int)
    for warehouse in warehouses:
        wid = warehouse.get("id")
        if wid is None:
            continue
        for i in range(0, len(ids), 1000):
            response = request(MARKETPLACE + "/api/v3/stocks/" + str(int(wid)),
                               marketplace_token, {"chrtIds": ids[i:i + 1000]})
            stocks = response.get("stocks")
            if not isinstance(stocks, list):
                raise ValueError("Missing stocks array for warehouse")
            for stock in stocks:
                qty[int(stock["chrtId"])] += max(0, int(stock.get("amount") or 0))
    return {nm for nm, chrts in mapping.items() if sum(qty[c] for c in chrts) > 0}
