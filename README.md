# Masterly — Telegram marketplace for plumbing services

Production-ready Telegram bot that connects clients with plumbers ("masters").
Clients create an order, choose a master and pay online; the money is held in
escrow until the client confirms the work is done. Masters track their balance
and request payouts, admins moderate the whole marketplace.

The bot is fully bilingual (**Uzbek / Russian**), stores the chosen language in
PostgreSQL and localizes every menu, button, message and notification.

## Features

- Client flow: service catalog → master selection → description / photo →
  date & time → address / location → confirmation → online payment.
- Master flow: application with services, prices and card details, incoming
  orders with accept / decline, work start → finish, balance, withdrawal
  requests, ratings and reviews.
- Payments: `mock` (signed local page, no merchant account), **Click** SHOP API,
  **Payme** Merchant API. Escrow hold → release (with commission) or refund.
- Disputes: the client can report a problem; an admin resolves it in favor of
  the client (refund) or the master (payout).
- Admin panel: dashboard, users, masters (approve / reject / pause), orders,
  payments, withdrawals, services & prices, complaints, broadcast, settings
  (commission, minimum withdrawal, timeouts).
- Background scheduler: expires master-acceptance and payment deadlines,
  marks stale orders and notifies both sides.
- Persistent notification log for every message sent to a user.

## Requirements

- Python 3.11+ (tested on 3.13)
- PostgreSQL 14+ for production (`asyncpg`)
- SQLite (`aiosqlite`) is supported for local development and the smoke test

## Quick start

```powershell
cd C:\Users\HP\Desktop\MASTERLY
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
Copy-Item .env.example .env
# edit .env: BOT_TOKEN, ADMIN_IDS, DATABASE_URL, SECRET_KEY
py main.py
```

Get your Telegram ID with `/myid` after the first start and put it into
`ADMIN_IDS` (comma separated for several admins). Restart the bot.

### Run without PostgreSQL (SQLite)

Environment variables take precedence over `.env`, so you can point the bot to
a local SQLite file without editing `.env`:

```powershell
$env:DATABASE_URL="sqlite+aiosqlite:///./masterly.db"
$env:BOT_TOKEN="123456789:your-token"
py main.py
```

With `AUTO_CREATE_SCHEMA=true` the schema and default services/settings are
created automatically on startup.

### Smoke test (no Telegram, no PostgreSQL)

```powershell
py scripts/smoke_test.py
```

The script creates a throwaway SQLite database, seeds it, and walks through the
main business flows: order creation, acceptance, mock payment + escrow, work
completion + commission, withdrawal payout, dispute refund, cancellation
cleanup, deadline expiry and notification persistence.

### Webhook smoke test (no Telegram, no PostgreSQL)

```powershell
py scripts/webhook_smoke.py
```

Boots the aiohttp app with a fake bot/dispatcher and a throwaway SQLite
database, then exercises `/health`, Telegram webhook secret validation, the
signed mock payment page, Payme authentication + `CheckPerformTransaction` /
`CreateTransaction` / `PerformTransaction` (idempotent), and Click prepare /
complete signatures including a cross-order `merchant_prepare_id` rejection.

## Configuration

All settings are environment variables (see `.env.example`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_TOKEN` | — | Telegram bot token (required) |
| `ADMIN_IDS` | — | Comma separated Telegram IDs with admin access |
| `SUPPORT_CONTACT` | `masterly_support` | Support username shown to users |
| `DATABASE_URL` | local PostgreSQL | `postgresql+asyncpg://…` or `sqlite+aiosqlite:///…` |
| `AUTO_CREATE_SCHEMA` | `true` | Create tables on startup (disable to use Alembic only) |
| `DEFAULT_LANGUAGE` | `uz` | `uz` or `ru` |
| `COMMISSION_PERCENT` | `15` | Platform commission (can be overridden per master) |
| `MIN_WITHDRAWAL` | `50000` | Minimum payout amount |
| `MASTER_ACCEPT_TIMEOUT_MINUTES` | `15` | Time a master has to accept an order |
| `PAYMENT_TIMEOUT_MINUTES` | `120` | Time a client has to pay |
| `TIMEZONE` | `Asia/Tashkent` | Display timezone |
| `PORT` | `8080` | HTTP port (health check + webhooks); Render injects its own |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | Base URL used in payment links |
| `WEBHOOK_BASE_URL` | — | Set to your public HTTPS URL to enable webhook mode |
| `WEBHOOK_SECRET` | `change-me-webhook-secret` | Path/header secret for the Telegram webhook |
| `PAYMENT_PROVIDER` | `mock` | `mock`, `click` or `payme` |
| `ALLOW_MOCK_PAYMENTS` | `true` | Must be `true` to expose the mock payment page |
| `CLICK_*` / `PAYME_*` | — | Merchant credentials for real providers |
| `SECRET_KEY` | — | Signs mock payments and derives the card encryption key |
| `CARD_ENCRYPTION_KEY` | — | Optional explicit Fernet key for card numbers |
| `LOG_LEVEL` | `INFO` | Logging level |
| `RATE_LIMIT_SECONDS` | `0.4` | Per-user throttle between updates |

`COMMISSION_PERCENT`, `MIN_WITHDRAWAL` and the timeouts can also be changed at
runtime from the admin panel (stored in `admin_settings` and layered over the
environment defaults).

## Payments

- **mock** — the bot generates a signed link to `/payments/mock/pay/{id}?sig=…`.
  The page confirms the payment and the order moves to `PAID`. Use it for demos
  and staging; disable with `ALLOW_MOCK_PAYMENTS=false` in production.
- **click** — set `CLICK_SERVICE_ID`, `CLICK_MERCHANT_ID`, `CLICK_SECRET_KEY`;
  prepare URL `/payments/click/prepare`, complete URL
  `/payments/click/complete`.
- **payme** — set `PAYME_MERCHANT_ID`, `PAYME_KEY`; endpoint
  `/payments/payme` (Basic auth, JSON-RPC).

Payment links are built from `PUBLIC_BASE_URL`; if it is left at the localhost
default, `WEBHOOK_BASE_URL` (or Render's `RENDER_EXTERNAL_URL`) is used instead.

## Deployment (Render)

`render.yaml` is included: a Python web service + managed PostgreSQL.

1. Create a new Blueprint from the repository.
2. Fill the `sync: false` variables: `BOT_TOKEN`, `ADMIN_IDS`, `WEBHOOK_BASE_URL`
   (your Render URL, e.g. `https://masterly-bot.onrender.com`), optionally
   `PUBLIC_BASE_URL` if payment links should use another domain.
3. Deploy. With `WEBHOOK_BASE_URL` set the bot starts in webhook mode and
   registers `POST /telegram/webhook/{WEBHOOK_SECRET}` automatically; otherwise
   it falls back to long polling. Health check: `GET /health`.

If you create the PostgreSQL instance manually instead of using the Blueprint,
set `DATABASE_URL` to its **internal** connection string
(`postgresql://…`, converted to asyncpg automatically) and keep
`AUTO_CREATE_SCHEMA=true` for the first start. Do not set `PORT` yourself —
Render assigns it and the app reads it from the environment.

Locally you can run the same server with `py main.py` (polling by default).

## Database migrations

```powershell
alembic upgrade head
```

`AUTO_CREATE_SCHEMA=true` is convenient for the first deploy; for strict
production use set it to `false` and run migrations explicitly.

## Project layout

```
handlers/        Telegram routers (start, registration, customer, master,
                 balance, payments, admin, language, common)
keyboards/       reply & inline keyboards, callback data factories
services/        business logic: orders, payments, balances, notifications,
                 settings, scheduler
database/        SQLAlchemy models, enums, engine/session, seed data
middlewares/     database session, current user, i18n, throttling
migrations/      Alembic async migrations
locales/         uz.json / ru.json (flat key/value)
utils/           formatting, rendering, i18n, crypto, validators, guards
webhook.py       aiohttp app: health, Telegram webhook, payment endpoints
scripts/         smoke_test.py, webhook_smoke.py
main.py          entrypoint (polling or webhook)
```

## Security notes

- Card numbers are encrypted at rest with Fernet (`SECRET_KEY` or
  `CARD_ENCRYPTION_KEY`); only the masked value and last 4 digits are shown.
- Mock payment pages are signed with HMAC-SHA256.
- Telegram webhook requests are protected by the URL secret and the
  `X-Telegram-Bot-Api-Secret-Token` header.
- Set a long random `SECRET_KEY`, keep `.env` out of version control, and set
  `ALLOW_MOCK_PAYMENTS=false` when a real provider is enabled.

## Troubleshooting

- `ConnectionDoesNotExistError` on startup → PostgreSQL is not running; start
  it or use the SQLite quick start above.
- `BOT_TOKEN` invalid → create a bot with [@BotFather](https://t.me/BotFather)
  and copy the token into `.env`.
- No admin panel → send `/myid`, add the number to `ADMIN_IDS`, restart.
- Webhook not receiving updates → make sure `WEBHOOK_BASE_URL` is public HTTPS
  and that `WEBHOOK_SECRET` matches; the bot re-registers it on startup.
