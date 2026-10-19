# WB: контроль цены покупателя и РРЦ

## Что решает модуль

`wb_agent/buyer_price.py` получает не цену продавца из Seller API, а публичную
региональную цену покупателя из текущего WB card API. Для каждого доступного
варианта используется:

```
buyer_public = sizes[].price.product + sizes[].price.logistics
```

Скидка WB Кошелька не захардкожена. Текущий процент берётся из публичной
конфигурации Wildberries:

- `global-payment/default-payment.json` — активная скидка типа
  «Незалогиненный кошелёк»;
- `settings-front.json` — `wlt1DiscountDisplayMaxPrice`.

Так получается `wallet_price_rub` — цена публичного покупателя с WB Кошельком
для заданного региона (`dest`).

## Важное ограничение

Это максимально близкая автоматизируемая **публичная** цена, но не обещание
цены конкретного авторизованного аккаунта. Персональная акция, промокод,
индивидуальный Wallet-статус или иная аккаунтная скидка могут изменить checkout.
Для такой проверки нужен авторизованный buyer-session/сторонний API.

## РРЦ

`wb_agent/rrp_reprice.py` сравнивает минимальную найденную buyer price с:

```
target = RRP * (1 + WB_RRP_BUFFER_PCT / 100)
```

По умолчанию буфер — 5%.

Если buyer price ниже цели, модуль рассчитывает необходимую базовую цену
продавца пропорционально текущему отношению seller price -> buyer price.

## Предохранители

Автоматическая запись цены:

- по умолчанию выключена;
- только повышает цену, никогда не снижает;
- не применяется к товарам с WB Club discount;
- не применяется к неоднозначным/размерным ценам;
- не применяется, если предложенный шаг выше `WB_REPRICE_MAX_STEP_PCT`;
- перед записью повторно читает buyer price;
- пишет через официальный Seller Prices & Discounts endpoint
  `POST /api/v2/upload/task`.

## GitHub Actions

Workflow: `.github/workflows/wb-buyer-price-rrp.yml`.

Ручной запуск:
- `apply=false` — только аудит;
- `apply=true` — разрешить безопасные повышения.

Плановый запуск выполняется раз в 2 часа. Автоматическая запись на плановом
запуске включается только если repository variable `WB_AUTO_REPRICE=1`.

### Secrets

- `WB_API_KEY`
- `RRP_MAP_B64`
- опционально Telegram: `TELEGRAM_WB_BOT_TOKEN`, `TELEGRAM_WB_CHAT_ID`

### Variables

- `WB_BUYER_DEST` — WB dest нужного региона;
- `WB_EXPECTED_SUPPLIER_ID` — ID нашего продавца для защиты от чужого оффера;
- `WB_RRP_BUFFER_PCT` — по умолчанию 5;
- `WB_REPRICE_MAX_STEP_PCT` — по умолчанию 20;
- `WB_AUTO_REPRICE` — 1 только после проверки dry-run.
