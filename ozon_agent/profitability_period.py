from __future__ import annotations
import argparse,csv,os
from pathlib import Path
from ozon_export.client import OzonClient
from .business_rules import is_clearance_sku
from .economy_costs import economy_costs
from .realization_period import aggregate_realization_period
from .telegram import send_telegram
from .unit_economics_live import load_cost_map,resolve_purchase_cost

LANDED_COST_FACTOR=1.35

def _base_offer(x):
    raw=str(x or "").strip(); up=raw.upper()
    for s in (".УЦ","-УЦ","_УЦ"," УЦ"):
        if up.endswith(s): return raw[:-len(s)].strip()
    return raw

def _cost(offer,cost_map):
    c,s,v=resolve_purchase_cost(offer,cost_map)
    if c is not None: return c,s,v
    if is_clearance_sku(offer):
        b=_base_offer(offer)
        if b and b!=offer:
            c,s,v=resolve_purchase_cost(b,cost_map)
            if c is not None: return c,"clearance_base:"+s,v
    return None,"missing",False

def _status(profit,margin):
    if profit<0:return "RED"
    if margin<20:return "YELLOW"
    return "GREEN"

def build_profitability(realization,costs,cost_map,allow_unverified=True):
    rows=[]
    zero={"acquiring_rub":0.0,"shipment_processing_rub":0.0,"logistics_rub":0.0,
          "last_mile_rub":0.0,"placement_rub":0.0,"returns_rub":0.0,
          "operational_errors_rub":0.0,"promotion_rub":0.0}
    for sku,s in realization.items():
        offer=str(s.get("offer_id") or "").strip()
        units=float(s.get("delivered_units") or 0)
        pc,src,verified=_cost(offer,cost_map)
        base={"ozon_sku":sku,"offer_id":offer,"name":s.get("name") or "",
              "delivered_units":units,
              "buyer_revenue_rub":float(s.get("buyer_revenue_rub") or 0),
              "ozon_discount_points_rub":float(s.get("ozon_discount_points_rub") or 0),
              "partner_programs_rub":float(s.get("partner_programs_rub") or 0),
              "economic_sales_base_rub":float(s.get("economic_sales_base_rub") or 0),
              "ozon_sales_fee_rub":float(s.get("ozon_sales_fee_rub") or 0),
              **{**zero,**costs.get(sku,{})},
              "purchase_cost_source":src,
              "purchase_cost_verified":bool(verified)}
        if pc is None:
            rows.append({**base,"status":"MISSING_COST"});continue
        if not verified and not allow_unverified:
            rows.append({**base,"status":"UNVERIFIED_COST"});continue
        landed=float(pc)*LANDED_COST_FACTOR
        cogs=landed*units
        ozon_costs=(base["ozon_sales_fee_rub"]
                    -base["acquiring_rub"]
                    -base["shipment_processing_rub"]
                    -base["logistics_rub"]
                    -base["last_mile_rub"]
                    -base["placement_rub"]
                    -base["returns_rub"]
                    -base["operational_errors_rub"]
                    -base["promotion_rub"])
        profit=base["economic_sales_base_rub"]-ozon_costs-cogs
        margin=profit/base["economic_sales_base_rub"]*100 if base["economic_sales_base_rub"]>0 else 0
        rows.append({**base,
                     "purchase_cost_rub":round(float(pc),2),
                     "landed_unit_cost_rub":round(landed,2),
                     "cogs_rub":round(cogs,2),
                     "ozon_costs_total_rub":round(ozon_costs,2),
                     "contribution_profit_rub":round(profit,2),
                     "contribution_profit_per_unit_rub":round(profit/units,2) if units else None,
                     "contribution_margin_percent":round(margin,2),
                     "status":_status(profit,margin)})
    rows.sort(key=lambda x:({"RED":0,"YELLOW":1,"GREEN":2}.get(x.get("status"),9),float(x.get("contribution_profit_rub") or 0)))
    return rows

def _rub(v):return f"{float(v):,.0f}".replace(","," ")+" ₽"

def send_summary(rows,a,b):
    red=[r for r in rows if r.get("status")=="RED"]
    yellow=[r for r in rows if r.get("status")=="YELLOW"]
    lines=[f"Ozon — маржинальность {a}–{b}",
           f"🔴 {len(red)} | 🟡 {len(yellow)} | 🟢 {sum(r.get('status')=='GREEN' for r in rows)}"]
    for r in red[:30]:
        lines.append(f"• {r['offer_id']}: {_rub(r['contribution_profit_rub'])} ({r['contribution_margin_percent']:.1f}%)")
    send_telegram("\n".join(lines))

def write_csv(path,rows):
    fields=["ozon_sku","offer_id","name","delivered_units","buyer_revenue_rub",
            "ozon_discount_points_rub","partner_programs_rub","economic_sales_base_rub",
            "ozon_sales_fee_rub","acquiring_rub","shipment_processing_rub","logistics_rub",
            "last_mile_rub","placement_rub","returns_rub","operational_errors_rub",
            "promotion_rub","purchase_cost_rub","landed_unit_cost_rub","cogs_rub",
            "ozon_costs_total_rub","contribution_profit_rub","contribution_profit_per_unit_rub",
            "contribution_margin_percent","status"]
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("w",encoding="utf-8-sig",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=fields,delimiter=";",extrasaction="ignore")
        w.writeheader();w.writerows(rows)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--date-from",required=True)
    ap.add_argument("--date-to",required=True)
    ap.add_argument("--output",default="artifacts/private/ozon_profitability.csv")
    args=ap.parse_args()
    cost_map=load_cost_map()
    if not cost_map: raise RuntimeError("PURCHASE_COST_MAP_B64 is empty")
    client=OzonClient()
    realization=aggregate_realization_period(client,args.date_from,args.date_to)
    costs=economy_costs(client,args.date_from,args.date_to,realization)
    rows=build_profitability(realization,costs,cost_map,os.getenv("ALLOW_LEGACY_COSTS","").upper() in {"YES","TRUE","1"})
    write_csv(args.output,rows)
    x=next((r for r in rows if r.get("offer_id")=="MS9500"),None)
    if x:
        print("MS9500 CHECK "
              f"units={x['delivered_units']:.0f} buyer={x['buyer_revenue_rub']:.2f} "
              f"points={x['ozon_discount_points_rub']:.2f} partners={x['partner_programs_rub']:.2f} "
              f"base={x['economic_sales_base_rub']:.2f} fee={x['ozon_sales_fee_rub']:.2f} "
              f"acq={x['acquiring_rub']:.2f} proc={x['shipment_processing_rub']:.2f} "
              f"log={x['logistics_rub']:.2f} last={x['last_mile_rub']:.2f} "
              f"ret={x['returns_rub']:.2f} err={x['operational_errors_rub']:.2f} "
              f"promo={x['promotion_rub']:.2f} cogs={x['cogs_rub']:.2f} "
              f"profit={x['contribution_profit_rub']:.2f}")
    for offer in ("2020C-B","MS101","PB74","MS8828 BLACK","MS9903R+ms101"):
        r=next((x for x in rows if str(x.get("offer_id") or "").upper()==offer.upper()),None)
        if r:
            print(
                "VALIDATION",offer,
                f"units={r['delivered_units']:.0f}",
                f"buyer={r['buyer_revenue_rub']:.2f}",
                f"placement={r['placement_rub']:.2f}",
                f"profit={r['contribution_profit_rub']:.2f}",
                f"margin={r['contribution_margin_percent']:.2f}"
            )
    send_summary(rows,args.date_from,args.date_to)
    return 0
if __name__=="__main__":raise SystemExit(main())
