# Running Verifyr

Full instructions for running the Phase 0 mobile QA agent end-to-end.

This guide assumes the app under test is the Empire Crypto app
(`com.empirecrypto.mobile`) on the `Medium_Phone_API_36.1` emulator, matching the
current `.env`. Adjust names as needed for a different app/device.

---

## 1. One-time setup

Only needed once per machine (already done on this machine).

```bash
cd /Users/kumartanay/Verifyr

# Python environment + dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
playwright install chromium            # browser for web source-of-truth capture

# Appium server + Android driver (Node.js required)
npm install -g appium
appium driver install uiautomator2

# Config
cp .env.example .env                   # then edit (see section 2)
```

> **All CLI commands below run from `backend/`** (`cd backend`), since the engine
> is the `verifyr` package. The `.env` stays at the repo root and is auto-loaded.

---

## 2. Configure `.env`

The engine auto-loads `.env` from the repo root. Required values:

```ini
# VLM
VLM_PROVIDER=openai
VLM_MODEL=gpt-5.5                       # any vision-capable model; gpt-4o also works
OPENAI_API_KEY=sk-...
VLM_MAX_TOKENS=4096                     # headroom for reasoning models

# Device / Appium
APPIUM_SERVER_URL=http://127.0.0.1:4723
ANDROID_AVD=Medium_Phone_API_36.1
ANDROID_UDID=emulator-5554

# App under test (package+activity OR an apk path)
APP_PACKAGE=com.empirecrypto.mobile
APP_ACTIVITY=com.empirecrypto.mobile.MainActivity
# APP_PATH=/Users/kumartanay/empire-mobile-app/android/app/build/outputs/apk/release/main-app-release.apk
```

> Security: never commit `.env` (it's gitignored). Rotate any API key that has
> been shared.

Sanity-check the config without a device:
```bash
source .venv/bin/activate
python -m verifyr.config
```

---

> **Auto-launch:** Verifyr bootstraps the device stack for you on the first run —
> it starts a local **Appium** server if one isn't reachable (`AUTO_START_APPIUM`)
> and launches an existing **AVD** if no device is running (`AUTO_START_EMULATOR`),
> both on by default. It shows a clear error if `appium` isn't installed or no
> AVD exists. So Section 3 below is optional — set `AUTO_START_*=false` to manage
> them yourself.

## 3. (Optional) Start the emulator and Appium manually

These must be running before the agent. Use separate terminals (or run in the
background) and leave them up.

### Terminal A — emulator
```bash
~/Library/Android/sdk/emulator/emulator -avd Medium_Phone_API_36.1
```
Wait for the home screen, then confirm:
```bash
adb devices            # expect: emulator-5554   device
```

### Terminal B — Appium server
```bash
appium
```
Wait for:
```
Appium REST http interface listener started on http://127.0.0.1:4723
```

---

## 4. Install the app (first time, or after a rebuild)

If using `APP_PACKAGE`/`APP_ACTIVITY`, the app must be installed on the emulator:
```bash
adb install -r /Users/kumartanay/empire-mobile-app/android/app/build/outputs/apk/release/main-app-release.apk
adb shell pm list packages | grep empirecrypto      # confirm
```
(If you set `APP_PATH` instead, Appium installs it automatically each session.)

---

## 5. Run the agent

### Terminal C — the agent
```bash
cd /Users/kumartanay/Verifyr/backend
source ../.venv/bin/activate

# Optional: reset the app to a clean state first
adb shell am force-stop com.empirecrypto.mobile

python -m verifyr.agent --goal "Skip onboarding and report the first main screen title"
```

### Agent flags

| Flag | Meaning |
|------|---------|
| `--goal` | **(required)** the high-level task |
| `--web-value` | source-of-truth value for the parity verifier |
| `--web-url` | capture the source-of-truth value from this page (Playwright) |
| `--web-selector` | CSS/Playwright selector for the value (omit → page `<title>`) |
| `--web-attribute` | read an attribute instead of inner text (e.g. `value`) |
| `--web-headed` | show the capture browser instead of headless |
| `--quiet` | suppress per-step console logging |
| `--prompt` | path to an alternate prompt.json |

### Examples

Basic:
```bash
python -m verifyr.agent --goal "Open the markets screen and read the BTC price"
```

Parity check against a literal value:
```bash
python -m verifyr.agent --goal "Read the BTC price" --web-value "₹1,099.00"
```

Parity check captured live from a webpage:
```bash
python -m verifyr.agent --goal "Read the product price" \
  --web-url "https://www.amazon.in/.../dp/B0G26LRCSV/" \
  --web-selector ".a-price .a-offscreen"
```

---

## 6. Eval harness

Run every goal N times and print a reliability table (Pass@1, Pass@N, average
steps, average latency, failure tally).

```bash
python -m verifyr.eval --goals goals.json --runs 3
```

`goals.json` entries are a bare string or an object:
```json
{ "goal": "Read the BTC price", "web_url": "https://...", "web_selector": ".price" }
```
The web value (if any) is captured once per goal and reused across runs.

---

## 7. Where results go

```
runs/<timestamp>/
├── run.json        # full trace: observation, reasoning, action, result per step
├── step-01.png     # one screenshot per step
└── ...
```
The eval harness writes `runs/eval-<timestamp>/` with one folder per run plus a
`summary.json`.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Could not reach the Appium server at ...` | Appium (Terminal B) isn't running. Start `appium`. |
| `adb devices` shows nothing or `offline` | Emulator not booted yet — wait for the home screen. |
| `Cannot run: OPENAI_API_KEY ... / APP_PACKAGE ...` | Missing values in `.env`. |
| `Unsupported parameter: 'max_tokens'` | Handled automatically (the client retries with `max_completion_tokens`). |
| Web capture times out | Wrong selector, or the site blocks headless. Try a more specific selector or `--web-headed`. |
| Agent loops on the same screen | Loop-detection stops after 3 unchanged screens and records a `stuck` result. |
| Run ends at 25 steps | Step budget (`max_steps` in `prompt.json`) — raise it there if needed. |

---

## Quick start (services already running)

```bash
cd /Users/kumartanay/Verifyr/backend && source ../.venv/bin/activate
adb shell am force-stop com.empirecrypto.mobile
python -m verifyr.agent --goal "Skip onboarding and report the first main screen title"
```
