/** Pure price parser; missing / blocked responses never become fake prices. */
export function parseCardPrice(payload, nmID, walletPct = 3) {
  if (!Number.isSafeInteger(Number(nmID)) || !Number.isFinite(Number(walletPct)) || Number(walletPct) < 0 || Number(walletPct) >= 100) {
    throw Error("Неверный артикул или процент кошелька");
  }
  const products = Array.isArray(payload?.products) ? payload.products
    : Array.isArray(payload?.data?.products) ? payload.data.products : [];
  const product = products.find(p => Number(p?.id ?? p?.nmID) === Number(nmID));
  if (!product) return null;
  const variants = [];
  for (const size of product.sizes ?? []) {
    if (!size || !Array.isArray(size.stocks)) continue;
    const stocks = size.stocks.reduce((sum, stock) => sum + Math.max(0, Number(stock?.qty) || 0), 0);
    const p = Number(size?.price?.product);
    const logistics = Number(size?.price?.logistics ?? 0);
    if (!stocks || !Number.isSafeInteger(p) || p <= 0 || !Number.isSafeInteger(logistics) || logistics < 0) continue;
    const publicPrice = Math.floor((p + logistics) / 100);
    const productRub = p / 100;
    // Wallet result from API is an estimate, not a logged-in buyer price.
    const walletProduct = productRub <= 15000 ? Math.floor(productRub * (1 - Number(walletPct) / 100)) : Math.floor(productRub);
    const walletEstimate = Math.floor(walletProduct + logistics / 100);
    variants.push({nm_id: Number(nmID), public_price_rub: publicPrice,
      wallet_price_rub: walletEstimate, wallet_is_estimate: true,
      wallet_pct_estimate: Number(walletPct), option_id: Number(size.optionId) || null,
      supplier_id: Number(product.supplierId) || null,
      source: "wb_card_api_moscow", region: "Москва", dest: "-1257786"});
  }
  if (!variants.length) return null;
  return variants.sort((a, b) => a.wallet_price_rub - b.wallet_price_rub)[0];
}
