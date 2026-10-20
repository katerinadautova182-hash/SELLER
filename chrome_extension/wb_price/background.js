import {parseCardPrice} from './core.mjs';

chrome.runtime.onMessage.addListener((request, _sender, respond) => {
  if (request?.action !== 'WB_CHECK_API') return;
  (async () => {
    const nm = Number(request.nm_id);
    const pct = Number(request.wallet_pct);
    if (!Number.isSafeInteger(nm) || nm <= 0 || !Number.isFinite(pct) || pct < 0 || pct >= 100) {
      respond({ok: false, error: 'Неверный артикул или процент скидки.'}); return;
    }
    const url = 'https://card.wb.ru/cards/v4/detail?' + new URLSearchParams({
      appType: '1', curr: 'rub', lang: 'ru', dest: '-1257786', nm: String(nm)
    });
    try {
      const response = await fetch(url, {credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(16000)});
      if (!response.ok) { respond({ok: false, status: response.status, error: 'WB API вернул HTTP ' + response.status + '; цену не получили.'}); return; }
      const data = await response.json();
      const price = parseCardPrice(data, nm, pct);
      if (!price) {respond({ok: false, error: 'В ответе нет артикула с подтверждённой ценой и наличием.'}); return; }
      respond({ok: true, price});
    } catch (error) {respond({ok: false, error: 'Запрос не выполнен: ' + (error?.message || String(error))});}
  })();
  return true;
});
