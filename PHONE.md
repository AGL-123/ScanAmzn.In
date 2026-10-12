# Run the bot using only your phone

The bot runs **on your Android phone itself**, using the free Termux app. No PC
is needed at any step. Once it is set up you control it with buttons on your home
screen: **PS5 Start**, **PS5 Stop**, **PS5 Status**, **PS5 Test alert**,
**PS5 Settings** and **PS5 Check now**. Alerts arrive by e-mail and WhatsApp
like before, and every morning at 9:00 you get a short *"still running"*
message so you know it's alive.

Why on the phone: Amazon.in trusts a normal Indian home or mobile connection far
more than cloud servers, which it often answers with captchas. Your phone's
connection is the best place to check from.

> **iPhone?** iOS does not let apps run a program in the background 24/7, so
> the bot can't run on an iPhone. See [No Android phone?](#no-android-phone)
> at the end.

Setup takes about 15 minutes. Have these ready:

* your **Gmail address**, and
* your **WhatsApp number**.

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
   their current WhatsApp number on that page.
2. Save that number in your contacts (any name, e.g. *CallMeBot*).
3. On WhatsApp, send that contact exactly:
   `I allow callmebot to send me messages`
4. Within a couple of minutes it replies with your **API key** (a few digits).
   Keep that chat; you'll paste the key in Step 3.

## Step 2: Install Termux (3 min)

Termux is a free, open-source app that runs Linux programs on Android.

1. Install **F-Droid** (the open-source app store) from <https://f-droid.org>,
   then install **Termux** from F-Droid. Allow "install unknown apps" for your
   browser and F-Droid when Android asks.
2. In F-Droid, also install **Termux:Widget** (home-screen buttons) and
   **Termux:Boot** (restarts the bot after the phone reboots).

> Get Termux and its add-ons from the **same** store. Add-ons from Google Play
> don't work with Termux from F-Droid, and the other way round.

3. Open **Termux** once and wait until you see the `$` prompt.

## Step 3: Install the bot (5 min)

In Termux, type (or paste) this line and press Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/AGL-123/ScanAmzn.In/HEAD/termux/install.sh | bash
```

> **If the repository is private** (it is at the moment), that link gives a
> *404* error. Use these two lines instead. The first one signs you in to GitHub:
> choose *GitHub.com → HTTPS → Login with a web browser*, copy the 8-character
> code, and approve it in the browser page that opens.
>
> ```bash
> pkg install -y git gh && gh auth login
> gh repo clone AGL-123/ScanAmzn.In && bash ScanAmzn.In/termux/install.sh
> ```

The installer downloads everything it needs and then asks a few questions:

| Question | What to enter |
|---|---|
| Your Gmail address | `you@gmail.com` (or press Enter to skip e-mail) |
| Gmail app password | the 16 letters from Step 1 (typing is hidden; spaces are fine) |
| Send alerts to | press Enter to send them to yourself |
| Your WhatsApp number | e.g. `9876543210` (+91 is added for you) |
| Your CallMeBot API key | the key from Step 1 |
| ASINs | press Enter. The bot finds the gift cards by searching. |
| Check every how many seconds | press Enter (60) |

Then it offers to **send a test alert**: say yes, and check that an e-mail and
a WhatsApp message arrive. Finally, say yes to **start the monitor**.

## Step 4: Add the home-screen buttons (2 min)

1. Long-press an empty spot on your home screen → **Widgets**.
2. Find **Termux:Widget**, drag it onto the home screen.
3. The widget lists the buttons: **PS5 Start**, **PS5 Stop**, **PS5 Status**,
   **PS5 Test alert**, **PS5 Settings**, **PS5 Check now**. (Tap the refresh
   icon on the widget if they don't show up yet.)

If Android asks whether Termux may *display over other apps*, allow it. The
buttons need that to open.

## Step 5: Stop Android from killing the bot (2 min, important)

Android closes background apps to save battery, and the bot can't see restocks
while it is closed. Do these once:

1. **Settings → Apps → Termux → Battery → Unrestricted** (on some phones:
   *Battery optimisation → All apps → Termux → Don't optimise*).
2. Open Termux, pull down the notification shade: the Termux notification
   should say **wake lock held**. The bot turns this on by itself when it starts.
3. Brand-specific extras (names vary a little by model):
   * **Xiaomi / Redmi / POCO**: Settings → Apps → Manage apps → Termux →
     **Autostart: on**, Battery saver → **No restrictions**. In the recent-apps
     screen, long-press Termux → **lock** (padlock).
   * **Samsung**: Settings → Battery → Background usage limits → make sure
     Termux is **not** in *Sleeping apps* / *Deep sleeping apps*, and add it to
     **Never sleeping apps**.
   * **Oppo / Realme / OnePlus / Vivo / iQOO**: App info → Battery →
     **Allow background activity** and **Allow auto launch**. Lock Termux in
     the recent-apps screen.
   * See <https://dontkillmyapp.com> for your exact model.

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
| **PS5 Check now** | Checks Amazon once right now and shows the result (sends no alerts). |

The same commands work typed in Termux: `ps5 start`, `ps5 stop`, `ps5 status`,
`ps5 test`, `ps5 setup`, `ps5 check`, `ps5 log` (recent log lines) and
`ps5 update` (download the latest version of the bot).

**What you'll receive**

* **🎮 IN STOCK** alert: tap the *Buy* link (or *Add to cart*) right away.
  These cards sell out in minutes.
* **✅ PS5 gift card monitor is running** every day at 9:00 IST. If this
  message doesn't arrive one morning, open **PS5 Status**.
* **⚠️ ScanAmzn cannot check Amazon.in** if checks keep failing for 30 minutes
  (no internet, or Amazon showing captchas). You get **✅ … checking Amazon.in
  again** when it recovers.

## Mobile data and battery

Each check downloads Amazon pages of roughly 0.2 MB each. With the default
settings (a check every 60 s, searches every 3rd check, up to 8 listings), that
can add up to **1-3 GB a day**. Use **Wi-Fi** if you can. To use less, run
**PS5 Settings** and choose a longer interval (e.g. 120 seconds). Battery use is
modest, but keeping the phone on the charger is safest.

## Troubleshooting

* **No test message arrives**: run **PS5 Test alert** and read the result. For
  WhatsApp, check the CallMeBot key and that your number has the country code.
  For Gmail, create a new app password and run **PS5 Settings**.
* **PS5 Status says NOT RUNNING**: tap **PS5 Start**. If it keeps stopping by
  itself, Android is killing Termux: redo Step 5. On Android 12-13 some phones
  also need the *phantom process* fix from <https://dontkillmyapp.com>; on
  Android 14+ turn on Settings → Developer options → **Disable child process
  restrictions**.
* **"Amazon is showing a captcha" in the status**: the bot waits and retries
  by itself. If it lasts for hours, choose a longer interval in **PS5 Settings**.
* **Buttons don't appear in the widget**: open Termux once, then tap the
  widget's refresh icon. Termux and Termux:Widget must come from the same store.

## No Android phone?

The bot also runs on any always-on computer, for example a free cloud server
or a Raspberry Pi. See [README.md](README.md#running-it-247). Cloud servers get
more captchas from Amazon than a home connection does.

Running the monitor around the clock on **GitHub Actions** is *not* offered.
GitHub's terms only allow Actions for building, testing and deploying the
repository's own software, so a 24/7 shopping monitor there can get your GitHub
account restricted. Amazon also blocks many of GitHub's servers.
