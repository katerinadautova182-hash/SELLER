/* Observe prices in the real WB purchase block: minimal visible non-struck. */
(() => {
 'use strict';
 const m=location.pathname.match(/^\/catalog\/(\d+)\/detail\.aspx/i);if(!m)return;
 const id=Number(m[1]), core=globalThis.SellerWBPriceCore;if(!core)return;
 const visible=el=>{if(!el||!el.getClientRects().length)return false;const s=getComputedStyle(el);return s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)!==0;};
 const old=(el,scope)=>{
  for(let p=el;p&&p!==scope.parentElement;p=p.parentElement){
   const cls=typeof p.className==='string'?p.className:'';
   if(/old.?price|price.?old|crossed|strike|strikethrough|line-through/i.test(cls)||['DEL','S','STRIKE'].includes(p.tagName)||getComputedStyle(p).textDecorationLine?.includes('line-through'))return true;
  }
  return false;
 };
 const priceCount=el=>(el.innerText||'').match(/\d[\d\s\u00a0\u202f]*\s*₽/g)?.length||0;
 function scope(){
  const buttons=[...document.querySelectorAll('button,[role="button"],a')].filter(el=>visible(el)&&/добавить в корзину|купить сейчас/i.test((el.innerText||el.textContent||'').trim()));
  for(const btn of buttons){
   const y=btn.getBoundingClientRect().top;
   for(let p=btn.parentElement,d=0;p&&d<9;p=p.parentElement,d++){
    if(!visible(p)||p===document.body)break;
    const n=priceCount(p);if(n>=1&&n<=7&&(p.innerText||'').length<1200)return {panel:p,buttonY:y};
   }
  }
  const blocks=[...document.querySelectorAll('[class*="productPrice"],[class*="priceBlock"],[class*="price-block"],[class*="product-price"],[class*="price__wrap"]')].filter(el=>visible(el)&&priceCount(el)>0&&priceCount(el)<=5&&(el.innerText||'').length<600);
  blocks.sort((a,b)=>(priceCount(b)>1)-(priceCount(a)>1)||(a.innerText||'').length-(b.innerText||'').length);
  return blocks.length?{panel:blocks[0],buttonY:Infinity}:null;
 }
 function read(){
  const area=scope();if(!area)return null;
  const found=[],seen=new Set();
  const add=(el,text)=>{
   if(!visible(el)||el.getBoundingClientRect().top>area.buttonY+10)return;
   const price=core.asPrice(text);if(!price)return;
   const isOld=old(el,area.panel),key=price+':'+isOld;if(seen.has(key))return;seen.add(key);
   const classes=[el,el.parentElement].map(x=>typeof x?.className==='string'?x.className:'').join(' ');
   const kind=/wallet|кошел|mo-typography_color_danger|mo-typography_color_accent/i.test(classes)?'wallet':/finalPrice|price-block__final-price/i.test(classes)?'regular':null;
   found.push({price,old:isOld,kind});
  };
  const walker=document.createTreeWalker(area.panel,NodeFilter.SHOW_TEXT);
  while(walker.nextNode()){
   const node=walker.currentNode;
   if(node.nodeValue?.length<=90&&/₽|руб/i.test(node.nodeValue))add(node.parentElement,node.nodeValue);
  }
  for(const el of area.panel.querySelectorAll('span,b,strong,ins,del,s,p,div')){
   if(!visible(el))continue;
   const text=(el.innerText||el.textContent||'').trim();
   if(text.length>60||!text.includes('₽'))continue;
   if(![...el.children].some(ch=>core.asPrice(ch.innerText||ch.textContent)))add(el,text);
  }
  const price=core.classify(found);
  return price?.min_price_rub?{...price,nm_id:id,source:'wb_live_dom_min_price',checked_at:new Date().toISOString()}:null;
 }
 let retries=0;
 function hello(){
  chrome.runtime.sendMessage({type:'SCAN_PAGE_HELLO',nm_id:id},response=>{
   if(chrome.runtime.lastError||!response?.authorized){if(++retries<=15)setTimeout(hello,250);return;}
   let last='',stable=0,finished=false;const started=Date.now();
   let timer;
   const observer=new MutationObserver(()=>tick());
   function finish(result,error){
    if(finished)return;finished=true;observer.disconnect();clearInterval(timer);
    chrome.runtime.sendMessage({type:'SCAN_PRICE_RESULT',nm_id:id,result,error},()=>void chrome.runtime.lastError);
   }
   function tick(){
    if(finished)return;
    const result=read();
    if(result){
     const key=JSON.stringify(result.display_prices_rub);
     stable=last===key?stable+1:0;last=key;
     if(stable>=1&&(result.display_prices_rub.length>=2||Date.now()-started>=1500)){finish(result,null);return;}
    }
    if(Date.now()-started>=9500){
     const text=document.body?.innerText?.slice(0,1200)||'';
     finish(null,/подозрительн|что-то не так|captcha|доступ ограничен/i.test(text)?'WB заблокировал страницу':'Цена не найдена за 9,5 секунды');
    }
   }
   observer.observe(document.body||document.documentElement,{subtree:true,childList:true,characterData:true});
   timer=setInterval(tick,300);tick();
  });
 }
 hello();
})();
