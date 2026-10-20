/* Runs only on WB product cards. No token, no cookie access. */
(() => {
  'use strict';
  const match = location.pathname.match(/^\/catalog\/(\d+)\/detail\.aspx/i);
  if (!match) return;
  const nmID = Number(match[1]);
  let helloTries = 0;
  const handshake = () => chrome.runtime.sendMessage({type: 'SCAN_PAGE_HELLO', nm_id: nmID}, response => {
    if (chrome.runtime.lastError || !response?.authorized) {
      if (++helloTries < 8) setTimeout(handshake, 1200);
      return;
    }
    const core = globalThis.SellerWBPriceCore;
    if (!core) return;
    const endAt = Date.now() + 28000;
    let attempts = 0;
    let previousSample = '';
    let matchingSamples = 0;
    const getVisible = el => {
      if (!el || !el.getClientRects().length) return false;
      const style = getComputedStyle(el);
      return style.display !== 'none' && style.visibility !== 'hidden';
    };
    const isOld = el => {
      for (let x = el, n = 0; x && n < 3; x = x.parentElement, n++) {
        const cls = typeof x.className === 'string' ? x.className : '';
        if (/oldPrice|old-price|price-old|crossed|strike|price__old/i.test(cls)) return true;
        if (/line-through/.test(getComputedStyle(x).textDecorationLine)) return true;
        if (['DEL', 'S', 'STRIKE'].includes(x.tagName)) return true;
      }
      return false;
    };
    function findPricePanel() {
      const wrapSelectors = [
        '[class*="productPrice"]', '[class*="priceBlock"]',
        '[class*="price-block"]', '[class*="priceBlockPrice"]',
        '[class*="product-price"]', '[class*="price__wrap"]'
      ];
      const candidates = [];
      for (const selector of wrapSelectors) {
        for (const el of document.querySelectorAll(selector)) {
          if (!getVisible(el)) continue;
          const txt = (el.innerText || '').slice(0, 700);
          const nums = txt.match(/\d[\d\s\u00a0\u202f]*\s*₽/g) || [];
          if (nums.length >= 1 && nums.length <= 7 && txt.length < 700) {
            candidates.push({el, count: nums.length, length: txt.length});
          }
        }
      }
      candidates.sort((a, b) => {
        const score = v => (v.count >= 2 ? 100 : 0) + (v.count <= 4 ? 20 : 0) - v.length / 100;
        return score(b) - score(a);
      });
      if (candidates.length) return candidates[0].el;
      const button = [...document.querySelectorAll('button,a')]
        .find(el => /добавить в корзину/i.test((el.textContent || '').trim()));
      if (!button) return null;
      for (let p = button.parentElement, depth = 0; p && depth < 7; p = p.parentElement, depth++) {
        const text = (p.innerText || '').slice(0, 2500);
        if ((text.match(/\d[\d\s\u00a0\u202f]*\s*₽/g) || []).length >= 2) return p;
      }
      return null;
    }
    function readPanel(panel) {
      const matched = [];
      const leaves = [panel, ...panel.querySelectorAll('span,b,strong,ins,del,s,p,div')];
      for (const el of leaves) {
        if (!getVisible(el)) continue;
        const full = (el.innerText || el.textContent || '').trim();
        if (!full || full.length > 55) continue;
        const price = core.asPrice(full);
        if (!price) continue;
        const childrenWithPrice = [...el.children].some(child => core.asPrice((child.innerText || '').trim()) !== null);
        if (childrenWithPrice) continue;
        const cls = typeof el.className === 'string' ? el.className : '';
        const parentCls = typeof el.parentElement?.className === 'string' ? el.parentElement.className : '';
        const combined = cls + ' ' + parentCls;
        const kind = /priceBlockFinalPrice|price-block__final-price/i.test(combined) ? 'regular'
          : /priceBlockWalletPrice|wallet-price|mo-typography_color_danger|mo-typography_color_accent/i.test(combined)
            ? 'wallet' : null;
        matched.push({price, old: isOld(el), kind});
      }
      return core.classify(matched);
    }
    function extract() {
      const panel = findPricePanel();
      if (!panel) return null;
      const data = readPanel(panel);
      if (!data || !data.public_price_rub) return null;
      return {...data, source: 'wb_live_dom', nm_id: nmID, url: location.href,
        checked_at: new Date().toISOString()};
    }
    const tick = () => {
      attempts++;
      const result = extract();
      if (result) {
        const currentSample = JSON.stringify([result.public_price_rub, result.wallet_price_rub]);
        matchingSamples = previousSample === currentSample ? matchingSamples + 1 : 0;
        previousSample = currentSample;
        if (matchingSamples < 2 || (result.wallet_price_rub === null && attempts < 7)) {
          setTimeout(tick, 1250);
          return;
        }
        chrome.runtime.sendMessage({type: 'SCAN_PRICE_RESULT', nm_id: nmID, result}, () => void chrome.runtime.lastError);
        return;
      }
      if (Date.now() >= endAt) {
        const denied = /подозрительн|не так|captcha|доступ ограничен/i.test(document.body?.innerText?.slice(0, 700) || '');
        chrome.runtime.sendMessage({type: 'SCAN_PRICE_RESULT', nm_id: nmID,
          error: denied ? 'WB заблокировал страницу' : 'Цена на карточке не найдена за 28 секунд'}, () => void chrome.runtime.lastError);
        return;
      }
      setTimeout(tick, 1350);
    };
    setTimeout(tick, 900);
  });
  handshake();
})();
