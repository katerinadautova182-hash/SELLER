/* Read-only WB auto scanner: Seller API catalog -> real Chrome storefront card. */
const STATE = 'wb_auto_state', ROWS = 'wb_auto_rows', ITEMS = 'wb_auto_items', ALARM = 'wb_auto_watchdog';
let processing = false;
const INITIAL = {running:false,status:'idle',index:0,total:0,success:0,errors:0,
  current_nm:null,tab_id:null,consecutive_blocked:0,started_at:null,updated_at:null,message:''};
async function stateGet() {
  const obj = await chrome.storage.local.get(STATE);
  return {...INITIAL,...(obj[STATE] || {})};
}
async function stateSave(patch) {
  const state = {...(await stateGet()),...patch,updated_at:Date.now()};
  await chrome.storage.local.set({[STATE]:state});
  return state;
}
const delay = ms => new Promise(r => setTimeout(r,ms));
function validateArticle(id) {
  const n = Number(id);
  return Number.isSafeInteger(n) && n > 0 && n < 1000000000000000 ? n : null;
}
async function readCatalog(token) {
  if (!token || token.trim().length < 15) throw new Error('Укажи API-токен WB категории «Цены и скидки».');
  const goods=[], uniq=new Set();
  for (let offset=0;offset<1000000;offset+=1000) {
    const url='https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter?limit=1000&offset='+offset;
    let data;
    for (let attempt=0;attempt<3;attempt++) {
      const response=await fetch(url,{headers:{Authorization:token.trim(),Accept:'application/json'},
        cache:'no-store',signal:AbortSignal.timeout(30000)});
      if (response.status===429 || response.status>=500) {
        if (attempt<2) {await delay(2500*(attempt+1));continue;}
      }
      if (!response.ok) throw new Error('Seller API WB вернул HTTP '+response.status+' (проверь права токена «Цены и скидки»).');
      data=await response.json();break;
    }
    if (!data || data.error===true || !Array.isArray(data?.data?.listGoods))
      throw new Error('Seller API вернул ошибку или неожиданный формат каталога.');
    const page=data.data.listGoods;
    for (const good of page) {
      const nm=validateArticle(good.nmID);
      if (!nm || uniq.has(nm)) continue;
      uniq.add(nm);
      goods.push({nm_id:nm,sku:String(good.vendorCode || ''),
        seller_base_rub:good.sizes?.[0]?.price ?? null,
        seller_discounted_rub:good.sizes?.[0]?.discountedPrice ?? null});
    }
    if (page.length===0 || page.length<1000) break;
    await delay(1000);
  }
  return goods;
}
async function nextItem() {
  if (processing) return;
  processing=true;
  try {
    const state=await stateGet();
    if (!state.running) return;
    const items=(await chrome.storage.local.get(ITEMS))[ITEMS] || [];
    if (state.index>=items.length) {
      await stateSave({running:false,status:'completed',current_nm:null,message:'Обход завершён. Можно выгрузить CSV.'});
      await chrome.alarms.clear(ALARM);return;
    }
    const item=items[state.index];
    if (!item) return;
    const tabUrl='https://www.wildberries.ru/catalog/'+item.nm_id+'/detail.aspx';
    let tab;
    if (state.tab_id) {try {tab=await chrome.tabs.get(state.tab_id);} catch {tab=null;}}
    if (!tab) tab=await chrome.tabs.create({url:tabUrl,active:true});
    else tab=await chrome.tabs.update(tab.id,{url:tabUrl,active:true});
    await stateSave({tab_id:tab.id,current_nm:item.nm_id,status:'scanning',page_started_at:Date.now(),
      message:'Читаем карточку '+(state.index+1)+' из '+items.length+': '+item.nm_id});
  } catch(e) {
    await stateSave({running:false,status:'failed',message:'Ошибка запуска карточки: '+e.message});
  } finally {processing=false;}
}
async function recordPrice(nm,sender,payload) {
  if (processing) return {ok:false};
  processing=true;
  let continuation=false;
  try {
    const state=await stateGet();
    if (!state.running || state.current_nm!==nm || sender.tab?.id!==state.tab_id) return {ok:false};
    const items=(await chrome.storage.local.get(ITEMS))[ITEMS] || [];
    const item=items[state.index];
    if (!item || item.nm_id!==nm) return {ok:false};
    const result=payload.result;
    const valid=result && result.nm_id===nm && Number.isSafeInteger(result.public_price_rub)
      && result.public_price_rub>0;
    const row={nm_id:nm,sku:item.sku,seller_base_rub:item.seller_base_rub,
      seller_discounted_rub:item.seller_discounted_rub,
      buyer_public_rub:valid?result.public_price_rub:null,
      buyer_wallet_rub:valid && Number.isSafeInteger(result.wallet_price_rub)?result.wallet_price_rub:null,
      read_status:valid?'OK':'ERROR',
      error:valid?'':String(payload.error || 'Не удалось подтвердить цену'),
      region:'регион текущей браузерной сессии',
      checked_at:new Date().toISOString(),source:valid?result.source:''};
    const rows=(await chrome.storage.local.get(ROWS))[ROWS] || [];
    rows.push(row);
    await chrome.storage.local.set({[ROWS]:rows});
    const blocked=/заблокировал|подозрительн|captcha/i.test(row.error);
    const strike=blocked?state.consecutive_blocked+1:0;
    const more=state.index+1<items.length,halt=strike>=3;
    await stateSave({index:state.index+1,success:state.success+(valid?1:0),
      errors:state.errors+(valid?0:1),consecutive_blocked:strike,
      status:halt?'blocked':more?'waiting_next':'completed',
      running:!halt && more,
      message:halt?'WB заблокировал три карточки подряд. Сбор остановлен во избежание ограничений.':
        'Проверено '+(state.index+1)+' из '+items.length+'. Успешно: '+(state.success+(valid?1:0))+'. Ошибок: '+(state.errors+(valid?0:1))+'.'});
    continuation=!halt && more;
    if (halt || !more) await chrome.alarms.clear(ALARM);
    return {ok:true};
  } finally {
    processing=false;
    if (continuation) setTimeout(()=>nextItem(),5500);
  }
}
async function startScan(message) {
  if ((await stateGet()).running) throw new Error('Обход уже запущен.');
  const goods=await readCatalog(message.token);
  if (!goods.length) throw new Error('Seller API не вернул ни одного артикула.');
  let limit=Number(message.limit);
  if (!Number.isSafeInteger(limit) || limit<=0) limit=goods.length;
  const items=goods.slice(0,limit);
  await chrome.storage.local.set({[ITEMS]:items,[ROWS]:[]});
  await stateSave({...INITIAL,running:true,status:'starting',index:0,total:items.length,
    started_at:Date.now(),updated_at:Date.now(),
    message:'Получено '+goods.length+' артикулов через Seller API WB. Запускаем обход.'});
  await chrome.alarms.create(ALARM,{periodInMinutes:1});
  await nextItem();
  return {catalog_total:goods.length,scan_count:items.length};
}
async function stopScan() {
  const old=await stateGet();
  await stateSave({running:false,status:'stopped',message:'Остановлено пользователем. Результаты сохранены.'});
  await chrome.alarms.clear(ALARM);
  if (old.tab_id) {try {await chrome.tabs.remove(old.tab_id);}catch {}}
}
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  (async()=>{
    if (message?.type==='SCAN_START') return {ok:true,...(await startScan(message))};
    if (message?.type==='SCAN_STOP') {await stopScan();return {ok:true};}
    if (message?.type==='SCAN_STATUS') return {ok:true,state:await stateGet()};
    if (message?.type==='SCAN_PAGE_HELLO') {
      const state=await stateGet();
      return {authorized:state.running && state.status==='scanning' &&
        sender.tab?.id===state.tab_id && state.current_nm===validateArticle(message.nm_id)};
    }
    if (message?.type==='SCAN_PRICE_RESULT') return await recordPrice(validateArticle(message.nm_id),sender,message);
    return {ok:false,error:'Unknown action'};
  })().then(sendResponse).catch(e=>sendResponse({ok:false,error:e.message || String(e)}));
  return true;
});
chrome.tabs.onRemoved.addListener(async tabId=>{
  const state=await stateGet();
  if (state.running && state.tab_id===tabId) {
    await stateSave({running:false,status:'stopped',message:'Вкладка сканирования закрыта. Сбор остановлен; данные сохранены.'});
    await chrome.alarms.clear(ALARM);
  }
});
chrome.alarms.onAlarm.addListener(async alarm=>{
  if (alarm.name!==ALARM) return;
  const state=await stateGet();
  if (!state.running) {await chrome.alarms.clear(ALARM);return;}
  if (state.status==='waiting_next') {await nextItem();return;}
  if (state.status==='scanning' && Date.now()-(state.page_started_at || 0)>55000) {
    await recordPrice(state.current_nm,{tab:{id:state.tab_id}},{error:'Таймаут ожидания ответа страницы WB'});
  }
});
