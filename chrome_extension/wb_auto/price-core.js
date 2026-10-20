/* Shared isolated parser. Does not access seller APIs or any credentials. */
(function (root) {
  'use strict';
  const asPrice = text => {
    const m = String(text || '').replace(/[\u00a0\u202f]/g, ' ').trim()
      .match(/^(?:от\s*)?(\d[\d ]{0,14})\s*(?:₽|руб\.?)/i);
    if (!m) return null;
    const n = Number(m[1].replace(/\s/g, ''));
    return Number.isSafeInteger(n) && n >= 50 && n <= 10000000 ? n : null;
  };
  function classify(prices) {
    const clean = (prices || []).filter(p => Number.isSafeInteger(p.price) && p.price > 0);
    const uniq = new Map();
    for (const p of clean) {
      const prev = uniq.get(p.price);
      if (!prev || (prev.old && !p.old) || (!prev.kind && p.kind)) uniq.set(p.price, p);
    }
    const valid = [...uniq.values()].filter(p => !p.old).sort((a, b) => a.price - b.price);
    if (!valid.length) return null;
    let wallet = valid.find(p => p.kind === 'wallet')?.price ?? null;
    let regular = valid.find(p => p.kind === 'regular' && p.price !== wallet)?.price ?? null;
    if (wallet !== null && regular === null) regular = valid.find(p => p.price > wallet)?.price ?? null;
    if (regular !== null && wallet === null) wallet = valid.find(p => p.price < regular && p.price >= regular * 0.85)?.price ?? null;
    if (wallet === null && regular === null) {
      if (valid.length >= 2 && valid[1].price <= valid[0].price * 1.15) {
        wallet = valid[0].price;
        regular = valid[1].price;
      } else regular = valid[0].price;
    }
    if (wallet !== null && regular !== null && wallet >= regular) wallet = null;
    return {public_price_rub: regular, wallet_price_rub: wallet,
      display_prices_rub: valid.map(p => p.price),
      confidence: (regular && wallet) ? 'two_visible_prices' : 'partial_price'};
  }
  root.SellerWBPriceCore = {asPrice, classify};
})(typeof globalThis !== 'undefined' ? globalThis : this);
