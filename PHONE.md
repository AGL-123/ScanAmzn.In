# Run the bot using only your phone

The bot runs **on your Android phone itself**, inside the free Termux app. No PC
is needed at any step. Once it is set up you control it with buttons on your home
screen: **PS5 Start**, **PS5 Stop**, **PS5 Status**, **PS5 Test alert**,
**PS5 Settings** and **PS5 Check now**. Alerts arrive by e-mail and Telegram
(WhatsApp is optional), and every morning (9:00 your time, adjustable) you get
a short *"still running"* message so you know it's alive.

Why on the phone: Amazon trusts a normal mobile or home connection far more than
cloud servers, which it often answers with captchas.

> **Living outside India (for example in Israel)?** That's fine: Amazon.in is
> open to visitors from anywhere, and the bot uses your phone's time zone and
> country code. One thing could not be tested in advance: exactly how Amazon.in
> shows stock to visitors outside India. The bot treats *"can't be delivered to
> your location"* as in stock. After setup, tap **PS5 Check now**: each
> PlayStation card should show as *unavailable* or *in stock*. If you see
> *unknown*, or get a *"cannot check Amazon.in"* warning, the bot can't read
> Amazon's pages correctly from where you are. Note what PS5 Status shows so it
> can be fixed.

> **iPhone?** iOS does not let apps run a program in the background 24/7, so
> the bot can't run on an iPhone. See [No Android phone?](#no-android-phone).

Setup takes about 15-20 minutes. You need your **Gmail address** and the
**Telegram** app (free, from Google Play).

---

## Step 1: Create your alert keys (5 min)

You can skip either channel, but set up at least one.

**Gmail app password** (for e-mail alerts)

1. Open <https://myaccount.google.com/security> and make sure **2-Step
   Verification** is on.
2. Open <https://myaccount.google.com/apppasswords>, type a name such as
   `PS5 bot`, and tap **Create**.
3. Google shows a 16-letter password (like `abcd efgh ijkl mnop`). Copy it;
   you'll paste it in Step 3.

> **"The setting you are looking for is not available for your account"?**
> Google only offers app passwords when **2-Step Verification is on**, and
> signing in with a passkey does *not* turn it on. Open
> <https://myaccount.google.com/signinoptions/twosv>, tap **Turn on 2-Step
> Verification**, and add your phone number (or Google prompts) as the second
> step. If 2-Step Verification is already on with *only* passkeys or security
> keys, add a phone number or Google prompts there too. Then open the app
> passwords page again. It is also unavailable for work/school accounts and
> with Google's *Advanced Protection*.
>
> Tip: a separate free Gmail account just for the bot also works (with 2-Step
> Verification and an app password), and its alerts to your main address then
> ring like any other mail.

**Telegram bot** (instant phone alerts, free, recommended)

Telegram lets anyone create a personal "bot" that can message you, using
Telegram's official service. It's free, and its messages arrive and ring like
any chat message.

1. Install **Telegram** from Google Play and sign up with your phone number
   (skip if you already use it).
2. In Telegram, search for **@BotFather** (the official one, with a blue tick)
   and tap **Start**.
3. Send `/newbot`. Give it any name (e.g. *My PS5 alerts*), then a username
   that ends in `bot` (e.g. `avi_ps5_alerts_bot`).
4. BotFather replies with a **token** like `123456789:AAH...`. Tap it to copy
   it; you'll paste it in Step 3. Keep it private: whoever has it can send
   messages as your bot. Use a new bot just for these alerts, not one that is
   already connected to another app.

You don't need to find any "chat ID": during setup, the installer asks you to
open your bot and tap **Start**, and finds your chat by itself.

**WhatsApp via CallMeBot** (optional; skip it if it doesn't answer)

CallMeBot is a free one-person service. In 2026 it often doesn't reply or pauses
accounts, which is why Telegram is recommended. If you still want WhatsApp too:

1. Open <https://www.callmebot.com/blog/free-api-whatsapp-messages/> and find
   their **current** WhatsApp number on that page (it changes often).
2. Save that number in your contacts and send it exactly, from your normal (not
   Business) WhatsApp: `I allow callmebot to send me messages`
3. It replies with an **API key**. If nothing comes within a few minutes, check
   that you used today's number and the exact sentence. Otherwise skip WhatsApp:
   you can add it later with **PS5 Settings**.

## Step 2: Install Termux (3 min)

**Android 11 or newer: use Google Play (easiest).** Install **Termux** from the
Play Store. That version already includes the home-screen widget and
start-at-boot support. Nothing else to install.

**Older Android, or if the Play version gives you trouble:** download these
three apps from <https://f-droid.org>: **Termux**, **Termux:Widget** and
**Termux:Boot** (on each app's page, tap *Download APK*; allow your browser to
install apps when Android asks). Then open **Termux:Boot** once, so it can start
the bot after a reboot.

> Never mix stores: Termux and its add-ons must all come from the same place
> (all Play Store, or all F-Droid). You don't need *Termux:API*.

Then open **Termux**, wait for the `$` prompt, and **allow notifications** if
Android asks. If you deny this, Android won't ask again.

## Step 3: Install the bot (5-10 min)

In Termux, type (or paste) this line and press Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/AGL-123/ScanAmzn.In/HEAD/termux/install.sh | bash
```

The installer downloads everything (answer **Y** if it asks about updating
packages) and then asks a few questions:

| Question | What to enter |
|---|---|
| Time zone | press Enter: it's read from your phone (e.g. `Asia/Jerusalem`) |
| Your Gmail address | `you@gmail.com` (or press Enter to skip e-mail) |
| Gmail app password | the 16 letters from Step 1 (typing is hidden; spaces are fine) |
| Send alerts to | press Enter to send them to yourself |
| Bot token | the Telegram token from Step 1 |
| *Now open https://t.me/your_bot and tap START* | tap the link (or find your bot in Telegram), tap **Start** (no Start button? send it any message), come back to Termux and press Enter. It confirms *Found your chat*. If it says the token is not valid, copy it again from BotFather; type `-` to skip Telegram for now. |
| Your WhatsApp number | press Enter to skip WhatsApp. If you set up CallMeBot: type it as usual, e.g. `052-123-4567` (the country code is added for you and the result is shown). |
| Your CallMeBot API key | (only if you entered a WhatsApp number) the key from CallMeBot |
| ASINs | press Enter. The bot finds the gift cards by searching. |
| Check every how many seconds | press Enter (60) |
| At what hour (daily *still running* message) | press Enter (9), or `-1` for none. It goes by e-mail/Telegram when you set either up, so WhatsApp's small free allowance is kept for stock alerts. |
| Ping URL (safety net) | press Enter to skip, or see Step 6 |

Then it offers to **send a test alert**: answer yes and check that the e-mail and
the Telegram message arrive. Finally, answer yes to **start the monitor**.

## Step 4: Add the home-screen buttons (2 min)

1. Long-press an empty spot on your home screen → **Widgets**.
2. Find **Termux** (Play Store version) or **Termux:Widget** (F-Droid), and drag
   the widget onto the home screen.
3. It lists the buttons: **PS5 Start**, **PS5 Stop**, **PS5 Status**,
   **PS5 Test alert**, **PS5 Settings** and **PS5 Check now**. Tap the widget's
   refresh icon if they don't show up yet.
4. Allow Termux to **display over other apps** (Settings → Apps → Termux →
   *Display over other apps* → Allow). Without it the buttons only open after
   you tap the Termux notification.

## Step 5: Stop Android from killing the bot (3 min, important)

Android closes background apps to save battery, and the bot can't see restocks
while it is closed. Do these once:

1. **Settings → Apps → Termux → Battery → Unrestricted** (or *Battery
   optimisation → Termux → Don't optimise*). If Termux asks to ignore battery
   optimisation, tap **Allow**.
2. **Settings → Apps → Termux**: turn **off** *Pause app activity if unused* /
   *Remove permissions if app is unused*.
3. Your phone brand's extras (names vary a little by model):
   * **Xiaomi / Redmi / POCO**: App info → **Autostart: on**, Battery saver →
     **No restrictions**. Open the recent-apps screen, long-press Termux →
     **lock** it. (Do the same Autostart step for Termux:Boot if you have it.)
   * **Samsung**: Settings → Battery → Background usage limits → turn **off**
     *Put unused apps to sleep*, and add Termux to **Never sleeping apps**.
   * **Oppo / Realme / OnePlus**: App info → Battery → allow **background
     activity** and **auto launch**, turn off *Optimise battery use*. Lock
     Termux in recent apps.
   * **Vivo / iQOO**: allow **Autostart** and *High background power
     consumption*. Lock Termux in recent apps.
   * Other phones: <https://dontkillmyapp.com>.
4. **Android 14 or newer**: Settings → Developer options → turn on **Disable
   child process restrictions**. To see Developer options, tap *Build number*
   7 times in *About phone*. Leave Developer options switched on, or this
   setting resets.

While the bot runs, the Termux notification says **wake lock held**. Don't tap
**Exit** in that notification; that stops the bot.

Best setup: an **old spare Android phone** that stays at home on Wi-Fi and on
the charger.

## Step 6 (optional): Get warned if the bot stops (3 min)

Even with Step 5, some phones still close Termux now and then, and a stopped
bot can't warn you itself. A free outside service can:

1. Open <https://healthchecks.io>, sign up (free), and tap **Add Check**.
2. Set **Period** to *15 minutes* and **Grace Time** to *15 minutes*, then save.
3. Copy the check's **ping URL** (it looks like `https://hc-ping.com/…`).
4. Tap **PS5 Settings**, press Enter through the questions, and paste the URL at
   *Ping URL*.

The bot checks in every few minutes while it runs. If it goes quiet for half
an hour, healthchecks.io e-mails you, and you can tap **PS5 Start**.

---

## Everyday use

| Button | What it does |
|---|---|
| **PS5 Start** | Starts watching (if it isn't already). |
| **PS5 Stop** | Stops watching. It stays off, even after a reboot, until you tap Start. |
| **PS5 Status** | Is it running, when it last checked, any problems, and the status of every listing it watches. |
| **PS5 Test alert** | Sends a test message on every channel you set up. |
| **PS5 Settings** | Asks the setup questions again. Press Enter to keep an answer, type `-` to remove a channel. Restarts the bot with the new settings. |
| **PS5 Check now** | Self-test against the real Amazon.in: shows what it finds and whether it would see a restock, and sends the result to Telegram/e-mail (see below). |

The same commands work typed in Termux: `ps5 start`, `ps5 stop`, `ps5 status`,
`ps5 test`, `ps5 setup`, `ps5 check`, `ps5 log` (recent log lines),
`ps5 update` (download the latest version of the bot) and `ps5 export` (see
below).

**What you'll receive**

* **🎮 IN STOCK**: tap the *Buy* link (or *Add to cart*) right away. These
  cards sell out in minutes.
* **✅ PS5 gift card monitor is running** every day at 9:00 (your time). If it doesn't
  arrive one morning, Android has probably closed Termux: open **PS5 Status**,
  tap **PS5 Start**, and recheck Step 5.
* **⚠️ ScanAmzn cannot check Amazon.in** if checks keep failing for 30 minutes
  (no internet, or Amazon showing captchas). You get **✅ … checking Amazon.in
  again** when it recovers.

* **⚠️ PS5 monitor may miss restocks** if the bot's regular self-test (below)
  fails twice in a row, and **✅ … self-test passes again** when it recovers.

## Is it really working?

PlayStation gift cards are almost always out of stock, so day-to-day running
only ever shows the bot reading *"unavailable"* pages. That alone doesn't prove
it would notice a card coming back. So the bot tests the in-stock case on the
real site: it picks an everyday product (such as a USB cable) that Amazon.in
shows with a price, opens its page, and checks that it reads it as **in
stock**.

**Tap PS5 Check now** (takes about half a minute). It checks:

1. that Amazon.in can be reached without a captcha, and where Amazon thinks
   you are (*delivery location*);
2. that the searches return results and which PlayStation gift card listings
   they found;
3. that those gift card pages are understood (*unavailable* / *in stock*);
4. that an in-stock product is read as **in stock** with its price.

A good result ends with *"RESULT: the bot reads Amazon.in correctly and would
see a restock"*, with ✅ on every line. The same report arrives in Telegram
(and e-mail). Copy it from there if you want to share it.

While running, the bot repeats check 4 every 6 hours. The daily *still
running* message and **PS5 Status** show when it last passed, and you're
warned if it fails twice in a row.

**If something shows ❌:**

1. Type `ps5 export` in Termux and allow storage access when asked. It copies
   the report, the pages the bot couldn't read and the recent log into your
   phone's **Downloads/ScanAmzn** folder.
2. Attach those files in the chat where you got this bot, so the page reading
   can be fixed for the real pages. They contain only Amazon's public pages and
   the bot's log, with e-mail addresses and phone numbers masked.
3. After a fix, `ps5 update` installs it.

## Mobile data and battery

Every check downloads the Amazon pages it watches, roughly 0.15-0.3 MB each.
With the default settings (a check every 60 s, searches every 3rd check, up to
8 listings) that is about **1-3 GB a day**. The setup wizard shows the estimate
for your settings. Use **Wi-Fi** if you can, or choose a longer interval in
**PS5 Settings** (120 s roughly halves it). Battery use is modest, but keep the
phone on the charger if you can.

## Troubleshooting

* **No test message arrives**: tap **PS5 Test alert** and read the result.
  For Telegram, open your bot's chat: if it shows **Restart** or **Unblock**,
  tap it (a "Delete and stop bot" earlier blocks it); otherwise send it any
  message, then tap **PS5 Test alert** again. To link a different Telegram
  account, run **PS5 Settings** and paste the token again (pressing Enter keeps
  the current chat).
  For Gmail, create a new app password and run **PS5 Settings**. Changing your
  Google password cancels app passwords. For WhatsApp, check the CallMeBot key
  and that your number has the country code; if it says the account is
  *paused*, send `resume` to the CallMeBot contact.
* **E-mail arrives but your phone doesn't ring**: Gmail may not notify you about
  mail you sent to yourself. At *Send alerts to*, enter a different address
  (any second mailbox), or rely on the Telegram alert.
* **PS5 Status says NOT RUNNING**: tap **PS5 Start**. If it keeps stopping by
  itself, Android is killing Termux. Redo Step 5. When Android kills Termux,
  it does not restart by itself until you open it.
* **"Amazon is showing a captcha" in the status**: the bot waits and retries
  by itself. If it lasts for hours, choose a longer interval in **PS5 Settings**.
  Captchas apply to your whole internet connection, so you may briefly see them
  too when you browse Amazon on the same Wi-Fi.
* **Buttons don't appear in the widget**: open Termux once, then tap the
  widget's refresh icon. Termux and its add-ons must come from the same store.
* **Something else**: `ps5 log` shows the last log lines.

**Updating the bot**: type `ps5 update` in Termux (or paste the install line
from Step 3 again). It downloads the newest version, refreshes the buttons and
restarts the monitor if it was running. Your settings are kept, and **PS5
Settings** only changes what it asks about.

## No Android phone?

The bot can't run on an iPhone, but it can run on any always-on computer: a home
PC or a Raspberry Pi ([README.md](README.md#running-it-247)), or a cloud server
you set up and control from your phone's browser.

The best free cloud option is **Oracle Cloud's Always Free** tier. Choose the
Mumbai or Hyderabad region when signing up: the region is permanent, and an
Indian address suits Amazon.in best. It has real drawbacks:

* signing up needs a credit card (debit and prepaid cards are often declined);
* Oracle may reclaim "idle" free servers, and this bot uses very little CPU;
* Amazon shows captchas to cloud servers much more often than to phones.

If that's your only option, ask for an Oracle setup script, which can be
pasted while creating the server so no PC or terminal is needed.

Running the monitor around the clock on **GitHub Actions** is *not* offered.
GitHub's terms only allow Actions for building, testing and deploying the
repository's own software, so a 24/7 shopping monitor there can get your GitHub
account restricted. Amazon also blocks many of GitHub's servers.
