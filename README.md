# Verifyr

An autonomous mobile QA agent that drives a single Android app on a local
emulator using a vision-language model. It reads each screen (screenshot +
accessibility tree), decides one action at a time, and can verify on-screen
values against a source-of-truth ("web") value. It's an experiment to measure
agent reliability, so logging and the eval harness are first-class.

The agent's behavior is defined by the prompt JSON in `idea/` — its schema is the
contract.

## Documentation

| Doc | What |
|-----|------|
| [docs/RUNNING_phase_0.md](docs/RUNNING_phase_0.md) | run the CLI agent + eval harness end-to-end |
| [docs/PHASE1.md](docs/PHASE1.md) | the parity checker (web ↔ API ↔ app + classifier) |
| [docs/GUI.md](docs/GUI.md) | the web dashboard (FastAPI + React): manage, run, schedule, history |

## Project structure

```
backend/                 # all Python — run commands start with `cd backend`
  verifyr/               # the engine (importable package): agent, parity, vlm, device, …
  server/                # FastAPI app (imports verifyr): auth, checks, runs, runner, scheduler
  requirements.txt       # engine deps        requirements-server.txt  # + server deps
  checks.json  goals.json
frontend/                # React + Vite dashboard
docs/                    # RUNNING_phase_0.md, PHASE1.md, GUI.md
idea/                    # prompt JSON (the engine contract)
Dockerfile  docker-compose.yml  .env.example
```

The engine is a package, so the CLI runs as modules **from `backend/`**:

**CLI**: `cd backend && python -m verifyr.agent --goal "..."`, `python -m verifyr.parity --check "..."`.
**Dashboard**: `cd backend && uvicorn server.main:app` + `cd frontend && npm run dev` — see docs/GUI.md.

---

## Phase 0 — the agent loop

## Architecture

Engine modules live under `backend/verifyr/`.

| File | Concern |
|------|---------|
| `config.py` | Load `prompt.json` (joins line-arrays), read env settings, build Appium caps |
| `vlm.py` | Provider-agnostic VLM client (OpenAI default, Anthropic alt); parses JSON, retries once |
| `device.py` | Appium/uiautomator2 executor: connect, screenshot, a11y tree, one fn per action |
| `agent.py` | The agent loop + CLI, artifacts, loop-detection & error backstops |
| `verifier.py` | Web-to-mobile parity check (+ Playwright stub for later) |
| `eval.py` | Eval harness: Pass@1 / Pass@N, avg steps/latency, failure tally |
| `goals.json` | Sample goals for the harness |

## Prerequisites

1. **Python 3.10+** (tested on 3.12).
2. **Android SDK + an emulator (AVD).** `adb` must be on PATH. Create an AVD via
   Android Studio, then list/boot it:
   ```bash
   emulator -list-avds
   emulator -avd Pixel_7_API_34        # or let Appium boot it via ANDROID_AVD
   adb devices                          # confirm a device is online
   ```
3. **Appium 2 + the UiAutomator2 driver** (Node.js required):
   ```bash
   npm install -g appium
   appium driver install uiautomator2
   appium                               # starts the server on http://127.0.0.1:4723
   ```
4. **An app to test** — either installed on the emulator (know its package +
   launch activity) or an `.apk` file.

   Find a running app's package/activity:
   ```bash
   adb shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'
   ```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
playwright install chromium      # for live web source-of-truth capture

cp .env.example .env      # then edit .env (kept at the repo root)
```

### Required environment variables

**VLM (default OpenAI/GPT):**
- `OPENAI_API_KEY` — your key (required)
- `VLM_PROVIDER` — `openai` (default) or `anthropic`
- `VLM_MODEL` — default `gpt-4o` (must be a **vision-capable** model)

**Device / Appium:**
- `APPIUM_SERVER_URL` — default `http://127.0.0.1:4723`
- `ANDROID_AVD` — AVD name so Appium can boot the emulator (e.g. `Pixel_7_API_34`)
- `ANDROID_DEVICE_NAME` — default `Android Emulator`
- `ANDROID_PLATFORM_VERSION`, `ANDROID_UDID` — optional
- `APPIUM_NO_RESET` — default `true`

**App under test (set ONE of):**
- `APP_PACKAGE` + `APP_ACTIVITY` — launch an installed app, **or**
- `APP_PATH` — absolute path to an `.apk`

Sanity-check your config without a device (from `backend/`):
```bash
cd backend && python -m verifyr.config
```

## Run a single goal

```bash
cd backend
python -m verifyr.agent --goal "Open the product 'Summer Tote' and read its displayed price" \
                        --web-value "49.00"
```

- `--web-value` is the source of truth; when the agent emits an `assert`, the
  verifier judges the on-screen value against it. Omit it to skip verification.
- Or capture the source of truth live from a webpage with Playwright:
  ```bash
  python -m verifyr.agent --goal "Open the product 'Summer Tote' and read its displayed price" \
                          --web-url "https://shop.example.com/products/summer-tote" \
                          --web-selector ".product .price"
  ```
  `--web-selector` is a CSS/Playwright selector (omit it to use the page title);
  `--web-attribute value` reads an attribute (e.g. an `<input>`'s value) instead
  of inner text; `--web-headed` shows the browser. An explicit `--web-value`
  always takes precedence over `--web-url`.
- Artifacts land in `runs/<timestamp>/`: a screenshot per step and `run.json`
  (full trace: observation, reasoning, action, result per step).

## Run the eval harness

```bash
python -m verifyr.eval --goals goals.json --runs 3
```

Each goal runs N times. Output is a summary table — **Pass@1**, **Pass@N**,
average step count, average latency — plus a failure tally across
`wrong-tap / loop / element-not-found / verifier-mismatch`. Per-run artifacts and
a `summary.json` are written under `runs/eval-<timestamp>/`.

`goals.json` entries are either a bare string or an object. For the source of
truth, give a literal `web_value`, or capture it live with `web_url` (+ optional
`web_selector` / `web_attribute`) — captured once per goal, reused across runs:
```json
{ "goal": "...", "web_url": "https://shop.example.com/p/tote", "web_selector": ".price" }
```
YAML is also supported (`--goals goals.yaml`).

## Reliability backstops (in code, not just the prompt)

- **Loop detection:** the accessibility tree is hashed each step; if the screen
  is unchanged for 3 consecutive steps the run stops with a `stuck` result.
- **Error tolerance:** every step is wrapped; after 3 consecutive errors the run
  ends as `error` instead of crashing.
- **Step budget:** taken from `config.max_steps` in `prompt.json` (default 25).
- **Verifier gating:** a `pass` verdict accompanied by a verifier result only
  stands if the verifier returned `match`; otherwise it's recorded as a
  `verifier-mismatch` failure.

## Notes / limits

- Coordinate taps from prose ("top-right corner") aren't interpretable
  deterministically; `tap` resolves by text/resource-id/content-desc/xpath, and
  only falls back to coordinates when the target encodes `x,y` or `x%,y%`.
- The source-of-truth value can be injected (`--web-value` / goal `web_value`)
  or captured live from a URL with Playwright
  (`verifier.capture_web_value()` / `resolve_web_value()`). Run
  `playwright install chromium` once before using URL capture.
- Swap VLM providers by editing `vlm.py` / setting `VLM_PROVIDER`; the loop and
  verifier are provider-agnostic.
