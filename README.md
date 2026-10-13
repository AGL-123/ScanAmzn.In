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
4. **Flags scalper prices.** A listing priced more than 10% above the face value
   in its title (e.g. a ₹1000 card offered at ₹1,500) is marked *overpriced*.
   A watched card that comes back at such a price sends an *IN STOCK above your
   price limit* alert (at most hourly); resellers' listings that are always in
   stock at a markup stay quiet. When the price drops to normal, it alerts.
5. **Recovers quickly when Amazon shows a bot check.** It retries after 60 s,
   120 s, then every `monitor.max_backoff_seconds` (180 s) with a fresh session,
   and after two bot checks in a row switches its *request style*
   (`fetcher.PROFILES`: "legacy" - desktop Chrome 140 with a first visit to the
   home page, the style that ran for hours on a phone without a block - then
   "plain", then "client-hints"). The style that works is remembered in
   `request-profile` next to the status file. If no check has succeeded for 10
   minutes (`health_alert_after_minutes`), you get a warning so you know the
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
| `selftest [--send]` | Check against the live site that Amazon.in is read correctly, including an in-stock reference product (see below). `--send` also sends the report. |
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

## Self-test: is it really reading Amazon.in?

The gift cards are nearly always out of stock, so normal running never shows the
bot reading an *in-stock* page. `python -m scanamzn selftest` therefore checks,
against the live site:

- reachability (on the first watched page - Amazon's home page is an AWS
  JavaScript challenge for non-browsers, so the bot never uses it) and Amazon's
  guessed delivery location;
- the search results and matching gift card listings;
- that those product pages are understood;
- a **positive control**: an everyday product that search shows with a price
  (`[amazon] control_queries`) must be read as in stock.

The verdict is *pass*, *fail* (something was read wrongly; pages are saved), or
*inconclusive* (a bot check or network error prevented the positive control),
with exit codes 0, 1 and 3. A bot check is retried with a fresh session.

Amazon hides out-of-stock items from search results, so the bot also watches
the known Sony PlayStation Store gift card listings unless
`[amazon] watch_known_listings = false`: `KNOWN_LISTINGS` in
`scanamzn/config.py` every round, and the other values (`MORE_LISTINGS`, ₹500
to ₹12,000) `monitor.other_listings_per_round` (default 2) per round in turn -
all of them at once in a round where any card has just come back in stock,
since restocks often come together.

Amazon answers bursts with bot checks (on the phone, the 12th request within
about 40 seconds was challenged) but lets a steady few pages a minute through
for hours. So every bot process - monitor, self-test, in-stock check - draws
from one budget of `monitor.max_requests_per_minute` (default 8), kept in a
file next to the status file. The self-test also opens only the main cards and
three of the values checked in turn, and asks a running monitor to pause while
it runs: a `paused-until` file that the self-test keeps locked, so the pause
ends as soon as the self-test does (however it ends), and after 10 minutes at
the most.

**Focused and extensive mode**: by default the monitor watches only
`[amazon] focus_asins` (`FOCUS_LISTINGS`, the Rs.4000 and Rs.2000 cards that
come back most often, plus any `asins` you add) every
`monitor.focus_interval_seconds` (default 30), with no searches and no other
values. While a file `extensive` exists next to the status file (`ps5
extensive` / the PS5 Extensive mode button), it watches everything described
above every `monitor.interval_seconds`. It is read every round. An empty
`focus_asins` means the extensive mode always.

**Data saver**: while a file `data-saver` exists next to the status file
(`ps5 saver` on the phone), the monitor checks only `asins` (listings found by
searches only while in stock, values in turn only right after a restock, no
searches) and waits twice as long between rounds. It is read every round, so switching needs no restart.

The running monitor repeats the positive control every
`monitor.selftest_every_hours` (default 6). It warns after two failures in a
row, and reports the last result in the daily status message and in `status`.
Pages it could not read are saved to `monitor.debug_dump_dir`. If a watched
page that read *unavailable* turns into a product page the bot can't read, it
sends a **CHECK NOW: may be in stock** alert (at most once an hour per
listing): a restock in an unfamiliar layout must not go unnoticed.

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
