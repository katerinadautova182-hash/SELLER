const $ = (id) => document.getElementById(id);
const output = $('status');
let currentRecord = null;
const rub = (price) => Number.isFinite(price) ? new Intl.NumberFormat('ru-RU').format(price) + ' ₽' : '—';
const showStatus = (text) => {output.textContent = text;};
function getID() {
  const value = $('article').value.trim();
  if (!/^\d{5,15}$/.test(value)) throw Error('Введи числовой артикул WB.');
  return Number(value);
}
function showPrice(data) {
  currentRecord = {...data, checked_at: new Date().toISOString()};
  $('priceSection').hidden = false;
  $('publicPrice').textContent = rub(data.public_price_rub);
  $('walletPrice').textContent = rub(data.wallet_price_rub);
  $('source').textContent = data.wallet_is_estimate
    ? 'Источник: API WB · Москва (dest=-1257786). Цена с кошельком расчётная (' + data.wallet_pct_estimate + '%), не подтверждена страницей.'
    : 'Источник: открытая карточка WB · регион Москва подтверждён пользователем · цена с кошельком считана со страницы.';
  chrome.storage.local.get({history: []}).then(({history}) => {
    const rest = history.filter(p => p.nm_id !== data.nm_id);
    return chrome.storage.local.set({history: [currentRecord, ...rest].slice(0, 200)});
  }).catch(() => {});
}
async function busy(button, fn) {
  button.disabled = true;
  try {await fn();} catch (e) {showStatus('Ошибка: ' + e.message);} finally {button.disabled = false;}
}
$('checkApi').addEventListener('click', () => busy($('checkApi'), async () => {
  const nm_id = getID(), wallet_pct = Number($('wallet').value);
  if (!Number.isFinite(wallet_pct) || wallet_pct < 0 || wallet_pct >= 100) throw Error('Неверный процент кошелька.');
  $('priceSection').hidden = true;
  showStatus('Обращаюсь к карточке WB для Москвы…');
  const response = await chrome.runtime.sendMessage({action: 'WB_CHECK_API', nm_id, wallet_pct});
  if (!response?.ok) {showStatus(response?.error || 'Нет ответа от WB API. Открой карточку и попробуй считывание с экрана.');return;}
  showPrice(response.price);
  showStatus('API вернул цену. Сверь с открытой карточкой для Москвы.');
}));
$('openCard').addEventListener('click', () => busy($('openCard'), async () => {
  const nm = getID();
  await chrome.tabs.create({url: 'https://www.wildberries.ru/catalog/' + nm + '/detail.aspx'});
  showStatus('Карточка открыта. Выбери Москву как адрес доставки; вернись к расширению и нажми «Считать обе цены».');
}));
$('readPage').addEventListener('click', () => busy($('readPage'), async () => {
  if (!$('confirmedMoscow').checked) throw Error('Сначала выбери город Москва на сайте и поставь отметку подтверждения.');
  const nm_id = getID();
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !/^https:\/\/www\.wildberries\.ru\/catalog\/\d+\/detail\.aspx/i.test(tab.url || '')) {
    throw Error('Открой карточку товара Wildberries в текущей вкладке обычного Chrome.');
  }
  const tabId = Number((tab.url.match(/\/catalog\/(\d+)\//) || [])[1]);
  if (tabId !== nm_id) throw Error('Артикул текущей карточки отличается от введённого в расширении.');
  $('priceSection').hidden = true;
  const [{result}] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: readDisplayedPrices});
  if (!result?.public_price_rub) throw Error('Не нашёл подтверждённую цену в карточке. Возможно, страница заблокирована либо WB поменял HTML.');
  showPrice({nm_id, ...result, wallet_is_estimate: false, wallet_pct_estimate: null,
    source: 'visible_wb_page', region: 'Москва', region_verification: 'user_confirmed', dest: null});
  showStatus(result.wallet_price_rub ? 'Считал обе цены прямо с открытой карточки.' : 'Основная цена считана. Цена кошелька на странице не найдена.');
}));
$('exportCsv').addEventListener('click', async () => {
  if (!currentRecord) return;
  const fields = ['nm_id','public_price_rub','wallet_price_rub','wallet_is_estimate','wallet_pct_estimate','region','region_verification','source','checked_at'];
  const esc = (v) => '"' + String(v ?? '').replace(/"/g,'""') + '"';
  const csv = '\uFEFF' + fields.join(';') + '\n' + fields.map(k => esc(currentRecord[k])).join(';') + '\n';
  const url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'}));
  const a = document.createElement('a');a.href = url;a.download = 'wb-price-' + currentRecord.nm_id + '.csv';a.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
});
// Executed in the existing ordinary WB Chrome tab, no Selenium / Playwright.
function readDisplayedPrices() {
  const selectors = {
    wallet: ['[class*="priceBlockWalletPrice"]','[class*="price-block__wallet-price"]','[class*="price__wallet-price"]'],
    regular: ['[class*="priceBlockFinalPrice"]','[class*="price-block__final-price"]','[class*="price__lower-price"]'],
  };
  function priceFrom(selectorsList) {
    for (const selector of selectorsList) {
      for (const element of document.querySelectorAll(selector)) {
        const cs = getComputedStyle(element);
        if (cs.display === 'none' || cs.visibility === 'hidden' || !element.getClientRects().length) continue;
        const text = (element.innerText || element.textContent || '').replace(/[\u00a0\u202f]/g, ' ').trim();
        const match = text.match(/(\d[\d ]{0,14})\s*(?:₽|руб\.?)/i);
        if (match) {
          const num = Number(match[1].replace(/\s/g, ''));
          if (Number.isSafeInteger(num) && num > 0) return num;
        }
      }
    }
    return null;
  }
  return {public_price_rub: priceFrom(selectors.regular), wallet_price_rub: priceFrom(selectors.wallet)};
}
