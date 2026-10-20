const $ = id => document.getElementById(id);
function status(message) {$('status').textContent = message;}
async function refresh() {
  const res = await chrome.runtime.sendMessage({type: 'SCAN_STATUS'});
  if (!res?.ok) return;
  const s = res.state;
  status(s.message || 'Ожидание запуска.');
  $('numbers').textContent = `Товаров: ${s.total} · Проверено: ${s.index} · Цен получено: ${s.success} · Ошибок: ${s.errors}`;
  $('progress').value = s.total ? Math.round(100 * s.index / s.total) : 0;
  $('start').disabled = Boolean(s.running);
  $('stop').disabled = !s.running;
}
$('start').addEventListener('click', async () => {
  const token = $('token').value.trim();
  if (!token) {status('Нужен токен WB категории «Цены и скидки».');return;}
  $('start').disabled = true;
  status('Загружаю весь каталог из Seller API WB...');
  try {
    const res = await chrome.runtime.sendMessage({type: 'SCAN_START', token, limit: Number($('limit').value)});
    if (!res?.ok) throw Error(res?.error || 'Seller API не ответил.');
    $('token').value = '';
    await refresh();
  } catch (err) {status('Ошибка: ' + err.message);}
  finally {$('start').disabled = false;}
});
$('stop').addEventListener('click', async () => {await chrome.runtime.sendMessage({type:'SCAN_STOP'});await refresh();});
$('export').addEventListener('click', async () => {
  const {wb_auto_rows: rows = []} = await chrome.storage.local.get('wb_auto_rows');
  if (!rows.length) {status('Пока нет результатов для выгрузки.');return;}
  const keys = ['nm_id','sku','seller_base_rub','seller_discounted_rub','buyer_public_rub','buyer_wallet_rub','read_status','error','region','checked_at','source'];
  const cell = value => '"' + String(value ?? '').replace(/"/g, '""') + '"';
  const csv = '﻿' + keys.join(';') + '\r\n' + rows.map(row => keys.map(k => cell(row[k])).join(';')).join('\r\n') + '\r\n';
  const a = document.createElement('a');
  const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
  a.href = url;
  a.download = 'WB_prices_' + new Date().toISOString().slice(0,10) + '.csv';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
});
refresh().catch(e => status('Ошибка состояния: '+e.message));
setInterval(() => refresh().catch(()=>{}), 1700);
