/* Lowest visible non-crossed-out buyer price is authoritative for the audit. */
(function (root) {
  'use strict';
  function asPrice(text) {
    const match=String(text||'').replace(/[\u00a0\u202f]/g,' ')
      .match(/(?:^|[^\d])(\d{1,3}(?:\s\d{3})+|\d{2,7})\s*(?:₽|руб(?:\.|лей)?)/i);
    if (!match) return null;
    const n=Number(match[1].replace(/\s/g,''));
    return Number.isSafeInteger(n) && n>=100 && n<=10000000 ? n : null;
  }
  function classify(prices) {
    const candidates=(prices||[]).filter(p=>!p.old && Number.isSafeInteger(p.price) && p.price>=100 && p.price<=10000000);
    const values=[...new Set(candidates.map(p=>p.price))].sort((a,b)=>a-b);
    if(!values.length) return null;
    const min=values[0], pair=values.length>=2 && values[1]<=min*1.15;
    const reg=candidates.find(p=>p.kind==='regular')?.price ?? null;
    const wallet=candidates.find(p=>p.kind==='wallet')?.price ?? null;
    return {min_price_rub:min, public_price_rub:reg??(pair?values[1]:min),
      wallet_price_rub:wallet??(pair?min:null),
      display_prices_rub:values,
      confidence:values.length>=2?'multiple_visible_prices':'one_visible_price'};
  }
  root.SellerWBPriceCore={asPrice,classify};
})(typeof globalThis!=='undefined'?globalThis:this);
