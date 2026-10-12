# Run the bot using only your phone

The bot runs **on your Android phone itself**, inside the free Termux app. No PC
is needed at any step. Once it is set up you control it with buttons on your home
screen: **PS5 Start**, **PS5 Stop**, **PS5 Status**, **PS5 Test alert**,
**PS5 Settings** and **PS5 Check now**. Alerts arrive by e-mail and WhatsApp,
and every morning at 9:00 IST you get a short *"still running"* message so you
know it's alive.

Why on the phone: Amazon.in trusts a normal Indian mobile or home connection far
more than cloud servers, which it often answers with captchas.

> **iPhone?** iOS does not let apps run a program in the background 24/7, so
> the bot can't run on an iPhone. See [No Android phone?](#no-android-phone).

Setup takes about 15-20 minutes. You need your **Gmail address** and your
**WhatsApp number**.

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

**CallMeBot API key** (for WhatsApp alerts, free)

1. Open <https://www.callmebot.com/blog/free-api-whatsapp-messages/> and find
   their current WhatsApp number on that page. It changes from time to time.
2. Save that number in your contacts (any name, e.g. *CallMeBot*).
3. On WhatsApp, send that contact exactly:
   `I allow callmebot to send me messages`
4. It replies with your **API key** (a few digits). This can take a couple of
   minutes. If nothing arrives, try again later. Keep that chat.

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

> **If the repository is private**, that link gives a *404* error. Use these
> two lines instead:
>
> ```bash
> pkg install -y git gh && gh auth login
> gh repo clone AGL-123/ScanAmzn.In && bash ScanAmzn.In/termux/install.sh
> ```
>
> `gh auth login` signs you in to GitHub. Answer *GitHub.com → HTTPS → Yes →
> Login with a web browser*. It shows an 8-character code. Open
> <https://github.com/login/device> in your browser, enter the code, and
> approve. Then come back to Termux.

The installer downloads everything (answer **Y** if it asks about updating
packages) and then asks a few questions:

| Question | What to enter |
|---|---|
| Your Gmail address | `you@gmail.com` (or press Enter to skip e-mail) |
| Gmail app password | the 16 letters from Step 1 (typing is hidden; spaces are fine) |
| Send alerts to | press Enter to send them to yourself |
| Your WhatsApp number | e.g. `9876543210` (+91 is added for you) |
| Your CallMeBot API key | the key from Step 1 |
| ASINs | press Enter. The bot finds the gift cards by searching. |
| Check every how many seconds | press Enter (60) |

Then it offers to **send a test alert**: answer yes and check that an e-mail and
a WhatsApp message arrive. Finally, answer yes to **start the monitor**.

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

---

## Everyday use

| Button | What it does |
|---|---|
| **PS5 Start** | Starts watching (if it isn't already). |
| **PS5 Stop** | Stops watching. It stays off, even after a reboot, until you tap Start. |
| **PS5 Status** | Is it running, when it last checked, any problems, and the status of every listing it watches. |
| **PS5 Test alert** | Sends a test e-mail and WhatsApp. |
| **PS5 Settings** | Asks the setup questions again. Press Enter to keep an answer, type `-` to remove a channel. Restarts the bot with the new settings. |
| **PS5 Check now** | Checks Amazon once right now and shows what it finds (sends no alerts). |

The same commands work typed in Termux: `ps5 start`, `ps5 stop`, `ps5 status`,
`ps5 test`, `ps5 setup`, `ps5 check`, `ps5 log` (recent log lines) and
`ps5 update` (download the latest version of the bot).

**What you'll receive**

* **🎮 IN STOCK**: tap the *Buy* link (or *Add to cart*) right away. These
  cards sell out in minutes.
* **✅ PS5 gift card monitor is running** every day at 9:00 IST. If it doesn't
  arrive one morning, Android has probably closed Termux: open **PS5 Status**,
  tap **PS5 Start**, and recheck Step 5.
* **⚠️ ScanAmzn cannot check Amazon.in** if checks keep failing for 30 minutes
  (no internet, or Amazon showing captchas). You get **✅ … checking Amazon.in
  again** when it recovers.

## Mobile data and battery

Every check downloads the Amazon pages it watches, roughly 0.15-0.3 MB each.
With the default settings (a check every 60 s, searches every 3rd check, up to
8 listings) that is about **1-3 GB a day**. The setup wizard shows the estimate
for your settings. Use **Wi-Fi** if you can, or choose a longer interval in
**PS5 Settings** (120 s roughly halves it). Battery use is modest, but keep the
phone on the charger if you can.

## Troubleshooting

* **No test message arrives**: tap **PS5 Test alert** and read the result.
  For WhatsApp, check the CallMeBot key and that your number has the country
  code. For Gmail, create a new app password and run **PS5 Settings**.
* **PS5 Status says NOT RUNNING**: tap **PS5 Start**. If it keeps stopping by
  itself, Android is killing Termux. Redo Step 5. When Android kills Termux,
  it does not restart by itself until you open it.
* **"Amazon is showing a captcha" in the status**: the bot waits and retries
  by itself. If it lasts for hours, choose a longer interval in **PS5 Settings**.
* **Buttons don't appear in the widget**: open Termux once, then tap the
  widget's refresh icon. Termux and its add-ons must come from the same store.
* **Something else**: `ps5 log` shows the last log lines, and `ps5 update`
  fetches the newest version of the bot.

## No Android phone?

The bot can't run on an iPhone, but it can run on any always-on computer: a home
PC or a Raspberry Pi ([README.md](README.md#running-it-247)), or a cloud server
you set up and control from your phone's browser.

The only cloud server that stays **free** with an **Indian** address is
**Oracle Cloud's Always Free** tier (Mumbai or Hyderabad region). It has real
drawbacks:

* signing up needs a Visa or Mastercard credit card (RuPay is often declined);
* Oracle may reclaim "idle" free servers, and this bot uses very little CPU;
* Amazon shows captchas to cloud servers much more often than to phones.

If that's your only option, ask for an Oracle setup script, which can be
pasted while creating the server so no PC or terminal is needed.

Running the monitor around the clock on **GitHub Actions** is *not* offered.
GitHub's terms only allow Actions for building, testing and deploying the
repository's own software, so a 24/7 shopping monitor there can get your GitHub
account restricted. Amazon also blocks many of GitHub's servers.
