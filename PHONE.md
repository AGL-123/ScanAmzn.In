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
   **PS5 Test alert**, **PS5 Settings**, **PS5 Check now**, **PS5 Test
   connection**, **PS5 Extensive mode** and **PS5 Data saver**. Tap the
   widget's refresh icon if they don't show up yet.
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
   setting resets. (**Android 12 or 13**: if Developer options has *Feature
   flags*, turn off *settings_enable_monitor_phantom_procs* there; otherwise
   it needs a computer, see <https://github.com/agnostic-apollo/Android-Docs/blob/master/en/docs/apps/processes/phantom-cached-and-empty-processes.md>.
   It matters mostly for the browser engine of Step 7.)

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

## Step 7 (recommended): Add the browser engine, so no check is lost (10 min, Wi-Fi)

Now and then Amazon answers the bot with a **bot check** instead of the page:
a short page with a JavaScript test that only a real web browser can run (your
phone's browser runs it without you noticing). The bot's normal requests can't,
so without help they have to wait and try again a minute later.

Type this in Termux, on Wi-Fi:

```
ps5 browser
```

It first brings Termux's packages up to date, then installs **Chromium** (the
open-source engine of Chrome) from Termux's own repository: about 150-300 MB to
download and 1 GB of space, and checks that it runs. The bot never shows a
window. From then on, when Amazon asks for a bot check, the bot loads that page
with the browser engine at once: it runs Amazon's test like any visitor's
browser, so the check succeeds within seconds instead of being lost. If Amazon
keeps asking, the bot reads every page through the browser until it stops.

* It does not hide what it is (it says it's a headless Chrome), and it never
  solves picture puzzles (CAPTCHAs). If Amazon shows one, the bot waits and
  warns you as before.
* It loads only Amazon's page itself and the test's script: no pictures, ads or
  other scripts, so data use stays the same.
* The package has a light engine (used first) and the full Chromium (used
  when the light one is stopped or won't start), so there is a second chance
  built in.
* It needs about 200-500 MB of memory while it runs (only one engine runs at a
  time). Make sure Step 5.4 is done: Android otherwise closes apps that start
  several processes, as a browser does.
* When it's installed, `ps5 browser` runs **PS5 Test connection** (below) by
  itself, sends you the result and restarts the bot so it uses the browser.

To remove it again: `ps5 browser remove`.

---

## Everyday use

| Button | What it does |
|---|---|
| **PS5 Start** | Starts watching (if it isn't already). |
| **PS5 Stop** | Stops watching. It stays off, even after a reboot, until you tap Start. |
| **PS5 Status** | Is it running, when it last checked, any problems, and the status of every listing it watches. |
| **PS5 Test alert** | Sends a test message on every channel you set up. |
| **PS5 Settings** | Asks the setup questions again, including how often to check in each mode (focused mode: every 10 seconds or more). Press Enter to keep an answer, type `-` to remove a channel. Restarts the bot with the new settings. |
| **PS5 Check now** | Self-test against the real Amazon.in: shows what it finds and whether it would see a restock, and sends the result to Telegram/e-mail (see below). |
| **PS5 Test connection** | Tries every way the bot can ask Amazon for a page (*request styles*: the bot's own requests in a few variants, the curl program, the browser engine from Step 7) on the two main cards, at the bot's own pace, and sends you a table of which got through. The bot then uses them in that order. Takes about 5-10 minutes; the bot pauses meanwhile. Use it when **PS5 Status** shows bot checks often, or after moving to another network. |
| **PS5 Extensive mode** | Switches between the default **focused mode** (only the two cards that come back most often, Rs.4000 and Rs.2000, every 30 s unless you chose otherwise in PS5 Settings) and the **extensive mode** (every value from Rs.500 to Rs.12,000 plus Amazon searches, every ~60 s). Tap again to switch back. |
| **PS5 Data saver** | On mobile data, tap this **instead of stopping the bot**: it checks fewer pages, half as often. Tap it again on Wi-Fi. |

The same commands work typed in Termux: `ps5 start`, `ps5 stop`, `ps5 status`,
`ps5 test`, `ps5 setup`, `ps5 check`, `ps5 probe` (PS5 Test connection),
`ps5 browser` (Step 7), `ps5 extensive` and `ps5 saver` (each also with `on` /
`off`),
`ps5 log` (recent log lines),
`ps5 update` (download the latest version of the bot) and `ps5 export` (see
below).

**What you'll receive**

* **🎮 IN STOCK**: tap the *Buy* link (or *Add to cart*) right away. These
  cards sell out in minutes.
* **🎮 IN STOCK above your price limit**: a card is back, but priced more
  than 10% over its face value. Buy it if that price is OK for you.
* **🎮 CHECK NOW: … may be in stock**: a gift card page that said *currently
  unavailable* has changed to something the bot can't read. That may be a
  restock in a layout the bot hasn't seen yet, so open the link right away.
  If it was a false alarm, run `ps5 export` and send the files so the bot
  learns that page.
* **✅ PS5 gift card monitor is running** every day at 9:00 (your time). If it doesn't
  arrive one morning, Android has probably closed Termux: open **PS5 Status**,
  tap **PS5 Start**, and recheck Step 5.
* **⚠️ ScanAmzn cannot check Amazon.in** if checks keep failing for 10 minutes
  (no internet, or Amazon showing bot checks), so you can watch the cards
  yourself meanwhile. You get **✅ … checking Amazon.in
  again** when it recovers.

* **⚠️ PS5 monitor may miss restocks** if the bot's regular self-test (below)
  fails twice in a row, and **✅ … self-test passes again** when it recovers.

## Be ready to buy

A restock is usually gone within minutes, so sort these out **before** the
first alert:

* Stay signed in to the **Amazon.in** app (or amazon.in in your browser) on
  this phone, so the alert's *Buy* link opens straight into checkout.
* The cards are for **adult PSN accounts registered to India** (the listing
  says so).
* **Check that you can pay.** Amazon.in's gift card help pages say cards
  issued outside India are not accepted for Amazon Pay gift cards. It isn't
  clear whether that also applies to these PlayStation codes, so make sure a
  payment method that works on Amazon.in is ready (for example Amazon Pay
  balance, an Indian card or UPI) - try a small digital purchase first if
  you're unsure.
* When cards are back, the most a seller usually charges is face value or a
  few percent more. A card priced more than 10% above the value printed in
  its title arrives as *IN STOCK above your price limit* instead of *IN
  STOCK*. To get plain *IN STOCK* alerts at such prices, raise
  `max_markup_percent` in the `[filter]` section of the settings file
  (`nano ~/ScanAmzn.In/config.toml` in Termux), then `ps5 restart`.

## Is it really working?

PlayStation gift cards are almost always out of stock, so day-to-day running
only ever shows the bot reading *"unavailable"* pages. That alone doesn't prove
it would notice a card coming back. So the bot tests the in-stock case on the
real site: it picks an everyday product (such as a USB cable) that Amazon.in
shows with a price, opens its page, and checks that it reads it as **in
stock**.

**Tap PS5 Check now** (takes 1-3 minutes; the running bot pauses meanwhile
and carries on by itself afterwards). It checks:

1. that Amazon.in answers (it opens the first gift card page, the same kind of
   page the bot watches), and where Amazon thinks you are (*delivery
   location*);
2. in the extensive mode only: that the searches return results and which
   PlayStation gift card listings they found (Amazon hides out-of-stock items
   from search, so finding none is normal; that's why the bot watches the known
   Sony PlayStation Store gift card listings directly: Rs.1000 / 2000 / 4000 /
   9000 every check, the other values from Rs.500 to Rs.12,000 two per check
   in turn, and all of them at once as soon as any card comes back);
3. that the gift card pages are understood (*unavailable* / *in stock*; *page
   not found* means Amazon removed that listing) - in the focused mode the
   Rs.4000 and Rs.2000 cards, in the extensive mode the main ones and three of
   the other values, different ones each time;
4. that an in-stock product is read as **in stock** with its price.

Amazon answers a burst of requests with bot checks, so the bot - the check
included - never sends more than 8 requests a minute. If Amazon still asks for
a bot check, it waits a minute and retries with a fresh session (up to twice). (The bot never opens
Amazon's home page: Amazon now shows it only to browsers that run JavaScript.
The product and search pages the bot reads are not affected.) It ends with one
of three results, also sent to Telegram (and e-mail):

* **✅ … would see a restock**: all good (⚠️ lines, if any, are details).
* **⚠️ not conclusive**: Amazon blocked part of the test or the connection
  failed, so nothing was proven either way. Try again in a few minutes.
* **❌ something is read wrongly**: send the files (below) so it can be fixed.

While running, the bot repeats check 4 every 6 hours. The daily *still
running* message and **PS5 Status** show when it last passed, and you're
warned if it fails twice in a row.

**If the result is ❌ (or you're asked for the files):**

1. Type `ps5 export` in Termux and allow storage access when asked. It copies
   the report, the pages the bot couldn't read and the recent log into your
   phone's **Downloads/ScanAmzn** folder.
2. Attach those files in the chat where you got this bot, so the page reading
   can be fixed for the real pages. They contain only Amazon's public pages and
   the bot's log, with e-mail addresses and phone numbers masked.
3. After a fix, `ps5 update` installs it.

## Mobile data and battery

Every check downloads the Amazon pages it watches, roughly 0.15-0.3 MB each.
In the default focused mode (two cards every 30 s) that is about **0.8-1.7 GB
a day**; every 20 s about 1.3-2.5 GB, every 10 s about 2.5-5 GB. In the
extensive mode (four cards plus two other values every ~60 s, searches every
3rd check) it is about **1.4-3.4 GB a day**. **PS5 Settings** shows the
estimate for your choices. Use **Wi-Fi** if you can. On mobile data, tap **PS5
Data saver** rather than stopping the bot: it halves the checks (focused mode:
the two cards at twice your interval - every ~60 s at the default 30 s, about
0.4-0.8 GB a day; every ~20 s if you chose 10 s, about 1.3-2.5 GB; extensive
mode: only the four main cards and no searches, but every value as soon as any
card comes back). Tap it again on Wi-Fi. The browser
engine (Step 7) reads the same pages without pictures, so it adds almost
nothing. Battery use is modest, but keep the phone on the charger if you can.

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
* **"Amazon showed a bot check" in the status or log**: Amazon refused the
  bot's request with a bot check. **The fix is Step 7** (`ps5 browser`): with
  the browser engine installed, such a page is read by the browser right away
  and the check isn't lost; the log then says *reading the page with the
  browser*. Without it, the bot tries again after 1, 2, then every 3 minutes,
  and after two bot checks in a row it changes how its requests look
  (*request style*). **PS5 Status** shows each style it used and how many
  pages it read. It remembers the style that works, also after a restart.
  Tap **PS5 Test connection** to measure all styles on your network and let
  the bot use the best one. Checking less often (**PS5 Settings**) also helps:
  Amazon is stricter with frequent requests. Bot checks apply to your whole
  internet connection, so you may briefly see them too when you browse Amazon
  on the same Wi-Fi.
* **"Amazon shows a CAPTCHA"**: Amazon wants a picture puzzle solved, which the
  bot never does. It waits and tries again; if it keeps happening, check less
  often for a while (**PS5 Settings**) or switch networks.
* **`ps5 browser` says it could not install Chromium**: follow the steps it
  prints (`dpkg --configure -a` if asked, then `pkg upgrade`, then `ps5 browser`
  again). If it installs but *does not run on this phone*, send `ps5 export`.
* **"'browser' could not read it", "the browser has quit" or "the browser's
  page crashed" in the log**: Android closed the browser engine. The bot starts
  it again by itself, but if this repeats, redo Step 5 (especially 5.4) and keep
  the phone on the charger. The bot meanwhile uses its other engine and request
  styles.
* **"'browser' got a bot check too" / PS5 Status shows bot checks for
  `browser`**: Amazon is asking this connection for more than a browser check.
  The bot keeps trying by itself; checking less often (**PS5 Settings**) or
  another network (Wi-Fi / mobile data) helps.
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
