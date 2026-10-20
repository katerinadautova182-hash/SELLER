import vm from 'node:vm';
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {test} from 'node:test';

const sandbox = {};
vm.runInNewContext(fs.readFileSync(new URL('../chrome_extension/wb_auto/price-core.js', import.meta.url), 'utf8'), sandbox);
const core = sandbox.SellerWBPriceCore;
test('parse spaced prices in rubles', () => {
  assert.equal(core.asPrice('8 134 ₽'), 8134);
  assert.equal(core.asPrice('8\u202f386 ₽'), 8386);
  assert.equal(core.asPrice('18 000 ₽'), 18000);
});
test('extract two storefront prices, ignore crossed-out RRP', () => {
  const p = core.classify([
    {price: 8134, old: false, kind: 'wallet'},
    {price: 8386, old: false, kind: 'regular'},
    {price: 18000, old: true, kind: null}
  ]);
  assert.equal(p.wallet_price_rub, 8134);
  assert.equal(p.public_price_rub, 8386);
});
test('partial display does not invent wallet discount', () => {
  const p = core.classify([{price: 11000, old: false, kind: 'regular'}]);
  assert.equal(p.wallet_price_rub, null);
});
test('old-only and empty returns null', () => {
  assert.equal(core.classify([{price: 18000, old: true, kind: null}]), null);
  assert.equal(core.classify([]), null);
});

test('lowest non-struck price wins even when WB changes markup classes', () => {
  const p = core.classify([
    {price: 8134, old: false, kind: null},
    {price: 8386, old: false, kind: null},
    {price: 18000, old: true, kind: null}
  ]);
  assert.equal(p.min_price_rub, 8134);
  assert.equal(p.public_price_rub, 8386);
});
test('one visible price is valid without inventing wallet value', () => {
  const p = core.classify([{price: 8134, old: false},{price: 18000, old: true}]);
  assert.equal(p.min_price_rub, 8134);
  assert.equal(p.wallet_price_rub, null);
});
