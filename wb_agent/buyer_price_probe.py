"""Diagnose PUBLIC WB storefront price fields for two explicit control cards.

No token and no price mutation. Output is sanitized monetary fields only.
"""
import json
import urllib.parse
import urllib.request

IDS = (1308438233, 1438080038)
BASE = "https://card.wb.ru/cards/v4/detail"
PARAMS = {
    "appType": "1", "curr": "rub", "dest": "-1257786",
    "nm": ";".join(map(str, IDS)),
}
HINTS = ("price", "wallet", "club", "sale", "logistic", "spp", "discount")


def collect(obj, path="", depth=0):
    found = {}
    if depth > 7:
        return found
    if isinstance(obj, dict):
        for k, v in obj.items():
            newpath = path + "." + k if path else k
            if any(word in k.lower() for word in HINTS) and not isinstance(v, (dict, list)):
                found[newpath] = v
            elif isinstance(v, (dict, list)):
                found.update(collect(v, newpath, depth + 1))
    elif isinstance(obj, list):
        for idx, val in enumerate(obj[:8]):
            found.update(collect(val, path + "[" + str(idx) + "]", depth + 1))
    return found


def main():
    url = BASE + "?" + urllib.parse.urlencode(PARAMS, safe=";")
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = json.load(response)
    except Exception as error:
        print("PUBLIC WB ENDPOINT UNAVAILABLE:", type(error).__name__,
              getattr(error, "code", ""))
        raise SystemExit(2)
    products = body.get("products") or (body.get("data") or {}).get("products") or []
    output = []
    for wanted in IDS:
        matches = [x for x in products if str(x.get("id", x.get("nmID"))) == str(wanted)]
        if not matches:
            output.append({"nmID": wanted, "status": "NOT_RETURNED"})
            continue
        item = matches[0]
        output.append({"nmID": wanted, "status": "RETURNED",
                       "seller_id": item.get("supplierId"),
                       "prices": collect(item)})
    print(json.dumps(output, ensure_ascii=False, indent=2))
    with open("wb-control-prices.json", "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
