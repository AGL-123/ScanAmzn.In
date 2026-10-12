# ScanAmzn.In

A bot that watches **Amazon.in** for **Indian-rupee PlayStation (PS5) gift cards**
(PSN wallet top-up / PlayStation Store cards). These cards show as *Currently
unavailable* almost all the time and sell out within minutes of a restock. When
one becomes buyable, the bot sends you an **e-mail** and a **WhatsApp message**
right away (Telegram is also supported, and is the most reliable instant
channel), with a direct product link and a one-click *add to cart* link.

> **📱 No PC? Run it on your Android phone:** follow **[PHONE.md](PHONE.md)**.
> One install command, then home-screen buttons to start, stop and check it.

## How it works

Every ~45 seconds (randomised), the bot:

1. **Checks each watched product page** (`/dp/<ASIN>`). A product counts as in
   stock when the page has an *Add to Cart* / *Buy Now* button, a *See All
   Buying Options* box, or positive availability text such as "In stock" or
   "Only 2 left". "Currently unavailable" and similar text count as out of stock.
2. **Runs your search queries** (e.g. `playstation gift card`) and keeps only
   titles that look like INR PlayStation wallet cards. US/UAE/other-currency
   cards, PS Plus memberships, controllers and the like are filtered out.
   Matching listings are **added to the watch list automatically**. If a search
   result looks in stock, the bot opens its product page to confirm before
   alerting you.
3. **Alerts once per restock.** An item that stays in stock does not trigger
   repeat alerts. If it sells out and comes back, you get a new alert (after a
   10-minute cooldown, so a flickering listing doesn't flood you). Several
   items restocking together send one alert each.
4. **Skips scalper prices.** A listing priced more than 10% above the face value
   in its title (e.g. a ₹1000 card offered at ₹1,500) is marked *overpriced* and
   doesn't alert. When the price drops to normal, it alerts.
5. **Backs off when Amazon shows a captcha.** It starts a fresh session with a
   different browser fingerprint and waits longer each time (up to 15 minutes).
   If no check has succeeded for 30 minutes, you get a warning so you know the
   bot can't see restocks. When checks work again, you get a recovery message.

Alerts are sent on all channels in parallel and retried, so a slow mail server
never delays the WhatsApp message or the next stock check. If one channel gives
up on a stock alert (e.g. CallMeBot paused your account), the other channels
tell you. Routine messages (the daily status, health warnings) go by e-mail when
it is set up, so WhatsApp's small free message allowance is kept for stock alerts.

## Quick start

Requires Python 3.11+.

```bash
git clone https://github.com/AGL-123/ScanAmzn.In.git
cd ScanAmzn.In
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config.example.toml config.toml                        # then edit config.toml
export SCANAMZN_SMTP_PASSWORD='your-gmail-app-password'
export SCANAMZN_CALLMEBOT_APIKEY='your-callmebot-key'

python -m scanamzn test-notify     # check that e-mail and WhatsApp both arrive
python -m scanamzn discover        # list the gift-card listings Amazon.in currently has
python -m scanamzn once            # one check cycle, prints a status table, sends nothing
python -m scanamzn run             # monitor until stopped (Ctrl+C)
```

`discover` prints an `asins = [...]` block. Paste it into `[amazon] asins` in
`config.toml`. Product pages are the most reliable signal, so watch the exact
denominations you want. The searches still pick up new listings.

Instead of editing the file by hand you can run `python -m scanamzn setup`,
which asks for your Gmail and WhatsApp details and writes `config.toml`.

### Commands

| Command | What it does |
|---|---|
| `setup` | Answer a few questions to create (or change) `config.toml`. |
| `run` | Monitor continuously. `--dry-run` logs alerts instead of sending them; `--max-runtime 5.5h` stops after that long. |
| `once` | One check cycle with a status table. `--notify` really sends alerts. |
| `discover` | Run the search queries and show which listings match the filter. |
| `test-notify` | Send a test message on every enabled channel. |
| `check-config` | Validate the settings and show what will be watched and where alerts go. |
| `status` | Show what the running monitor is doing (needs `monitor.status_file`). |
| `parse FILE [--search]` | Parse a saved Amazon page, for debugging. |

### Settings from environment variables

Every important setting can also come from a `SCANAMZN_*` environment
variable. Environment variables override `config.toml`, and with them the config
file is optional. That is handy for Docker (`--env-file`) and cloud servers.

| Variable | Meaning |
|---|---|
| `SCANAMZN_SMTP_USER`, `SCANAMZN_SMTP_PASSWORD` | Gmail address and app password (turns e-mail alerts on; alerts go to that address unless `SCANAMZN_EMAIL_TO` is set) |
| `SCANAMZN_TELEGRAM_TOKEN`, `SCANAMZN_TELEGRAM_CHAT_ID` | Telegram bot token and your chat ID (turns Telegram alerts on) |
| `SCANAMZN_WHATSAPP_PHONE`, `SCANAMZN_CALLMEBOT_APIKEY` | WhatsApp number(s) and CallMeBot key(s), comma-separated, in the same order |
| `SCANAMZN_ASINS` | ASINs to watch, comma-separated |
| `SCANAMZN_SEARCH_QUERIES` | search queries separated by `;` |
| `SCANAMZN_INTERVAL_SECONDS`, `SCANAMZN_DAILY_STATUS_HOUR`, `SCANAMZN_TIMEZONE`, `SCANAMZN_HEARTBEAT_URL`, `SCANAMZN_MAX_MARKUP_PERCENT`, `SCANAMZN_MAX_PRICE`, `SCANAMZN_NOTIFY_WHEN_SOLD_OUT`, `SCANAMZN_STATUS_FILE` | same as the config keys |
| `SCANAMZN_SMTP_HOST` / `_PORT` / `_SECURITY`, `SCANAMZN_EMAIL_FROM`, `SCANAMZN_EMAIL_TO` | other mail servers |
| `SCANAMZN_TWILIO_ACCOUNT_SID` / `_AUTH_TOKEN` / `_FROM` / `_CONTENT_SID`, `SCANAMZN_META_TOKEN` / `_PHONE_NUMBER_ID` / `_TEMPLATE` | Twilio or Meta instead of CallMeBot (with `SCANAMZN_WHATSAPP_PHONE`) |

Empty variables are ignored. The full list is in `scanamzn/envconfig.py`.

Global options: `-c config.toml` (config path) and `-v` (debug logging).

## Setting up notifications

Secrets can go straight into `config.toml`, but it is safer to reference
environment variables as `${NAME}` (as the example does). Only the channels you
enable need their variables set.

### E-mail (Gmail)

1. Turn on 2-Step Verification for your Google account.
2. Create an **App Password** (Google Account → Security → 2-Step Verification →
   App passwords) and put it in `SCANAMZN_SMTP_PASSWORD`.
3. Set `username`, `from_addr` and `to` in `[email]`.

Other providers work the same way. Use `security = "starttls"` with port 587
where required, for example Outlook (`smtp.office365.com`).

The alert e-mail is marked high-priority. Consider a Gmail filter that marks it
important, so it bypasses notification bundling on your phone.

### Telegram (recommended for instant phone alerts)

Free, official and reliable: messages from your own bot ring like any chat.

1. In Telegram, open **@BotFather**, send `/newbot` and follow the prompts. It
   gives you a bot token like `123456789:AAH...`.
2. Open your new bot and tap **Start** (a bot can only message people who did).
3. Run `python -m scanamzn setup`, which finds your chat ID automatically, or set
   `[telegram] enabled = true`, `bot_token` and `chat_id` yourself. The chat ID
   appears in `https://api.telegram.org/bot<token>/getUpdates` after step 2.

### WhatsApp

WhatsApp has no free, official way for a bot to message a personal number, so
every option has a catch. CallMeBot often doesn't reply or pauses accounts (2026),
the Twilio sandbox must be re-joined every 3 days, and Meta's API needs a
business setup and pre-approved message templates. Prefer Telegram.

Pick one provider in `[whatsapp] provider`:

| Provider | Setup effort | Notes |
|---|---|---|
| **`callmebot`** (default) | 2 minutes, free | Personal use only. Save CallMeBot's number from [their instructions page](https://www.callmebot.com/blog/free-api-whatsapp-messages/), send it *"I allow callmebot to send me messages"* on WhatsApp, and it replies with your API key. Put your number in international format (e.g. `+972…`) and the key in `[whatsapp.callmebot]`. Each recipient needs their own key. |
| **`twilio`** | Twilio account | Works with the Twilio sandbox for testing. WhatsApp only delivers free-form text within 24 h of your last message to the sender. For reliable alerts, create an approved **Content Template** with one variable `{{1}}` and set `content_sid`. |
| **`meta`** | Meta developer app | Official WhatsApp Cloud API. The same 24-hour rule applies. Outside it, set `template_name` to an approved template with a body variable `{{1}}`, or use `hello_world` with `template_body_param = false` as a bare ping. |

Run `python -m scanamzn test-notify` after any change.

## Running it 24/7

The bot only helps while it's running. Run it on an always-on device with a
residential (home or mobile) internet connection: an **Android phone**
([PHONE.md](PHONE.md)), a home PC, a Raspberry Pi or a NAS. Amazon is much
quicker to show captchas to cloud/datacenter IPs.

GitHub Actions is not suitable. GitHub's terms only allow Actions for building,
testing and deploying the repository's own software, so a 24/7 monitor there
risks your account. Its cron runs are also throttled and often late, and Amazon
blocks many of its IP ranges.

**systemd (Linux / Raspberry Pi)**: `/etc/systemd/system/scanamzn.service`:

```ini
[Unit]
Description=Amazon.in PS5 gift card monitor
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/ScanAmzn.In
EnvironmentFile=/home/pi/ScanAmzn.In/.env
ExecStart=/home/pi/ScanAmzn.In/.venv/bin/python -m scanamzn -c config.toml run
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

`.env` holds lines like `SCANAMZN_SMTP_PASSWORD=...`. Then run
`sudo systemctl enable --now scanamzn` and `journalctl -u scanamzn -f`.

**Docker**:

```bash
docker build -t scanamzn .
docker run -d --name scanamzn --restart unless-stopped \
  -v "$PWD/config.toml:/app/config.toml:ro" --env-file .env scanamzn
```

**Windows**: run `python -m scanamzn run` in a terminal that stays open, or add
it to Task Scheduler *at log on* with *restart on failure*. Also stop the PC from
sleeping.

## Tuning

* `monitor.interval_seconds` (default 45) sets the trade-off between detection
  speed and captchas. The bot refuses anything under 10 s. Below ~30 s, Amazon
  starts challenging most connections. Don't run several copies from the same
  IP: they share one captcha budget.
* Each cycle costs one request per watched ASIN plus one per search query. Each
  cycle runs these requests one after another, with a short random pause
  between them. Fewer, well-chosen ASINs mean faster cycles. Use
  `monitor.search_every_cycles = 2` or `3` to search less often once your ASIN
  list is complete.
* `[filter] max_markup_percent` (default 10, `-1` disables) and `max_price`
  control what counts as worth an alert.
* `notify_when_sold_out = true` also tells you when an alerted item is gone.

## Troubleshooting

* **"Amazon is showing a captcha"**: the bot already backs off and changes
  session automatically. If it happens constantly, raise `interval_seconds`,
  reduce the number of searches, or run from a different network.
  `curl_cffi` (installed by default) matters here: it makes the requests look
  like Chrome's at the TLS level.
* **A product shows as `unknown`**: Amazon changed its page layout. Set
  `monitor.debug_dump_dir = "debug_pages"`. Unrecognised pages are saved there,
  and you can inspect one with `python -m scanamzn parse debug_pages/<file>.html`.
* **`not_found`**: the ASIN is wrong or the listing was removed.
* **WhatsApp test works but real alerts don't arrive (Twilio/Meta)**: you are
  outside the 24-hour window. Configure a template as described above.

## Development

```bash
pip install -e '.[dev]'
pytest
```

The tests use saved-page fixtures in `tests/fixtures/` and fake HTTP/SMTP, so
they run offline.

## Notes

This is a personal restock notifier. It does not buy anything, solve captchas
or get around Amazon's protections, and it polls about as often as a person
refreshing a page. Automated access may still be against Amazon's Conditions of
Use, so keep the polling rate modest and use it at your own risk.
