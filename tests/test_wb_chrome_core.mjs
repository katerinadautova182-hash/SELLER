import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseCardPrice} from '../chrome_extension/wb_price/core.mjs';

const item={products:[{id:1308438233,supplierId:123,
  sizes:[{optionId:2,stocks:[{qty:5}],price:{product:838600,logistics:0}}]}]};

test('Moscow buyer price 8386, wallet estimated 8134 at 3%', ()=>{
  const result=parseCardPrice(item,1308438233,3);
  assert.equal(result.public_price_rub,8386);
  assert.equal(result.wallet_price_rub,8134);
  assert.equal(result.wallet_is_estimate,true);
  assert.equal(result.dest,'-1257786');
});
test('wrong article not used', ()=>assert.equal(parseCardPrice(item,99999999,3),null));
test('unavailable stock not used', ()=>{
  const unavailable={products:[{...item.products[0],sizes:[{stocks:[{qty:0}],price:{product:838600}}]}]};
  assert.equal(parseCardPrice(unavailable,1308438233,3),null);
});
test('empty payload not mistaken for price', ()=>assert.equal(parseCardPrice({data:{}},1308438233,3),null));
