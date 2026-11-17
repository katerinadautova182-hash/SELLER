# Ozon Margin Agent — setup

The agent branch is intentionally read-only at this stage.

## Required GitHub Secrets

Repository → Settings → Secrets and variables → Actions → New repository secret

Create exactly two secrets:

- `OZON_CLIENT_ID`
- `OZON_API_KEY`

Never commit these values to the repository.

## Run

Actions → **Ozon Margin Check** → Run workflow.

The workflow:
1. runs unit tests;
2. verifies that Ozon credentials exist;
3. performs a read-only Seller API smoke test;
4. downloads the live Ozon catalog/pricing snapshot;
5. uploads `ozon-margin-diagnostics` as a workflow artifact.

Scheduled execution is every 6 hours.

No price mutation endpoint is called in this version.


<!-- Telegram notification test trigger -->

<!-- Telegram retest after chat id fix -->

<!-- manual price alert verification -->


## Temporary Ozon price-change pause — 2026-11-17

Ozon price mutation is **disabled until the owner explicitly requests re-enabling**.

- Scheduled **Ozon Price Check** continues every six hours: snapshot, RRP+5% checks, Telegram alerts, diagnostics.
- Its automatic yellow/small-red repricing step, the post-change delay, and the second snapshot were removed.
- The **Ozon** job in **Mark Shmidt promo 19 Oct - 20 Nov** is disabled, including the scheduled November 21 restoration.
- The **Yandex** job of the promotion and other marketplaces were not changed.
- Both write functions (auto-fix and promo restore/reprice) require `OZON_PRICE_WRITES_ENABLED=YES` as an additional explicit opt-in. Without it, no Ozon price POST is issued.
- To re-enable: confirm the desired pricing rules, restore the Ozon job/step in workflows, and set the explicit write opt-in. Do not rely on the legacy approval flags alone.

Note: this repository-only change cannot retroactively stop a GitHub Actions run that was already executing when the commits were made.
