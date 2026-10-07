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
