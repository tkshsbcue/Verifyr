# Verifyr

Verifyr is an autonomous mobile QA agent. It drives a real Android app on an
emulator using a vision-language model: it reads each screen (a screenshot plus
the accessibility tree), decides one action at a time, executes it through
Appium, and repeats until the goal is reached or a budget is exhausted. It can
also verify what the app displays against a source-of-truth value — for example,
confirming the price shown in the app matches the price on your website or API.

It started as an experiment to measure how reliably an agent can complete real
mobile QA tasks, so structured logging, per-step artifacts, and an evaluation
harness are first-class. The agent's behavior is defined entirely by a prompt
JSON file (`idea/prompt0.json`, `idea/prompt1.json`) whose schema is the contract
between the engine and the model.

## What you can do with it

Verifyr has three surfaces, all built on the same engine:

1. **CLI agent (Phase 0)** — give it a natural-language goal and it navigates the
   app to complete it, optionally asserting an on-screen value against a known
   value. Good for one-off checks and scripting.
2. **Parity checker (Phase 1)** — an end-to-end "is the app showing the right
   thing" check that reconciles three sources: a web page (source of truth), an
   optional API endpoint, and the live app UI. It decides when to escalate to the
   device, runs a retry on stale-looking mismatches, and classifies the outcome.
3. **Web dashboard (GUI)** — a FastAPI + React app to create checks, trigger
   runs, watch the agent work step-by-step over a live WebSocket, browse history,
   and schedule recurring checks with email alerts on regression. Auth, data, and
   screenshots are backed by Supabase.

## How the agent loop works

Each step of a run does the same four things:

1. Capture the current screen: a screenshot (base64 PNG) and a compact
   accessibility tree from the Appium page source.
2. Render the prompt (goal, step number, recent actions, the tree) and send it
   with the system prompt and the screenshot to the vision-language model.
3. Parse the model's JSON action (`tap`, `type_text`, `scroll`, `swipe`,
   `press_back`, `wait`, `assert`, `finish`) and execute it on the device.
4. Record the action and result, check the reliability backstops, and continue.

Reliability backstops live in code, not just in the prompt:

- **Loop detection:** the accessibility tree is hashed each step; if the screen
  is unchanged for 3 consecutive steps the run stops with a `stuck` result.
- **Error tolerance:** every step is wrapped; after 3 consecutive errors the run
  ends as `error` instead of crashing.
- **Step budget:** taken from `config.max_steps` in the prompt JSON (default 25).
- **Verifier gating:** a `pass` verdict that carries a verifier result only stands
  if the verifier returned `match`; otherwise it is recorded as a
  `verifier-mismatch` failure.

## Architecture

The engine is an importable Python package under `backend/verifyr/`. The server
imports the engine; the frontend talks to the server.

```
backend/
  verifyr/                 the engine (importable package)
    config.py              load prompt JSON + env settings, build Appium caps
    vlm.py                 provider-agnostic VLM client (OpenAI default, Anthropic alt)
    device.py             Appium/uiautomator2 executor: connect, screenshot, a11y tree, actions
    agent.py              the agent loop + CLI, artifacts, loop/error backstops
    verifier.py           web-to-mobile value verification (+ Playwright capture)
    parity.py             Phase 1 orchestrator: web + API + app reconciliation
    classifier.py         turns the collected signals into a verdict
    eval.py               evaluation harness (Pass@1 / Pass@N, latency, failure tally)
    login.py             deterministic dev-impersonate login pre-step
    web_extractor.py      live web source-of-truth capture
  server/                  FastAPI app (imports verifyr)
    main.py                app, CORS, serves the built frontend
    runner.py             threaded runner: executes a run, streams events, persists
    routers/               auth, checks, runs, apks
    scheduler.py           APScheduler cron jobs for scheduled checks
    models.py, db.py       SQLAlchemy models and session
    supabase_client.py     Supabase token verification + Storage
  requirements.txt         engine deps
  requirements-server.txt  + server deps (install on top of the engine deps)
  checks.json, goals.json  sample checks and eval goals
frontend/                  React + Vite + TypeScript dashboard
docs/                      RUNNING_phase_0.md, PHASE1.md, GUI.md
idea/                      prompt JSON (the engine contract)
Dockerfile, docker-compose.yml, Makefile, .env.example
```

Because the engine is a package, the CLI runs as modules from the `backend/`
directory (for example `cd backend && python -m verifyr.agent ...`).

## Prerequisites

To run anything that drives a device you need an Android emulator and Appium.
To run only the dashboard with persistence you additionally need a Supabase
project. Pick what applies to you.

1. **Python 3.10+** (tested on 3.12).
2. **Node.js** — required by Appium, and by the frontend if you run it locally.
3. **Android SDK + an emulator (AVD).** `adb` must be on your PATH. Create an AVD
   in Android Studio, then:
   ```bash
   emulator -list-avds
   emulator -avd Pixel_7_API_34         # or let Appium boot it via ANDROID_AVD
   adb devices                           # confirm a device is online
   ```
4. **Appium 2 + the UiAutomator2 driver:**
   ```bash
   npm install -g appium
   appium driver install uiautomator2
   appium                                # starts the server on http://127.0.0.1:4723
   ```
5. **An app to test** — either installed on the emulator (know its package and
   launch activity) or an `.apk` file. To find a running app's package/activity:
   ```bash
   adb shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'
   ```
6. **A vision-capable model API key** — an OpenAI key (default) or an Anthropic
   key. The model must support image input.
7. **(Dashboard only) a Supabase project** — for Auth, Postgres, and Storage.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
playwright install chromium             # for live web source-of-truth capture

cp .env.example .env                     # then edit .env (kept at the repo root)
```

For the dashboard, also install the server dependencies:

```bash
pip install -r backend/requirements-server.txt
```

Sanity-check your configuration without a device (from `backend/`):

```bash
cd backend && python -m verifyr.config
```

## Running everything

### 1. CLI agent — run a single goal

```bash
cd backend
python -m verifyr.agent \
  --goal "Open the product 'Summer Tote' and read its displayed price" \
  --web-value "49.00"
```

- `--web-value` is the source of truth. When the agent emits an `assert`, the
  verifier judges the on-screen value against it. Omit it to skip verification.
- Capture the source of truth live from a web page instead, with Playwright:
  ```bash
  python -m verifyr.agent \
    --goal "Open the product 'Summer Tote' and read its displayed price" \
    --web-url "https://shop.example.com/products/summer-tote" \
    --web-selector ".product .price"
  ```
  `--web-selector` is a CSS/Playwright selector (omit it to use the page title);
  `--web-attribute value` reads an attribute (such as an input's `value`) instead
  of inner text; `--web-headed` shows the browser. An explicit `--web-value`
  always takes precedence over `--web-url`.
- Artifacts land in `runs/<timestamp>/`: a screenshot per step and `run.json`
  (the full trace — observation, reasoning, action, and result per step).

### 2. Evaluation harness — measure reliability

```bash
cd backend
python -m verifyr.eval --goals goals.json --runs 3
```

Each goal runs N times. The output is a summary table — **Pass@1**, **Pass@N**,
average step count, average latency — plus a failure tally across
`wrong-tap / loop / element-not-found / verifier-mismatch`. Per-run artifacts and
a `summary.json` are written under `runs/eval-<timestamp>/`.

`goals.json` entries are either a bare string or an object. For the source of
truth, give a literal `web_value`, or capture it live with `web_url` (plus an
optional `web_selector` / `web_attribute`) — captured once per goal and reused
across runs:

```json
{ "goal": "...", "web_url": "https://shop.example.com/p/tote", "web_selector": ".price" }
```

YAML is also supported (`--goals goals.yaml`).

### 3. Parity checker — web vs API vs app

```bash
cd backend
python -m verifyr.parity --check "Summer Tote price"     # one check by name
python -m verifyr.parity --all                            # every check in checks.json
```

The parity checker resolves the web value, optionally hits a cheap API endpoint
first, decides whether the app needs to be driven at all, runs the agent, retries
once on a stale-looking mismatch, and classifies the result. See
[docs/PHASE1.md](docs/PHASE1.md) for the full routing logic and the `checks.json`
schema.

### 4. Web dashboard — local development

Run the API and the Vite dev server in two terminals:

```bash
# Terminal 1 — API (from the repo root)
make dev-backend
# equivalent to: cd backend && uvicorn server.main:app --reload --port 8000

# Terminal 2 — frontend
make dev-frontend
# equivalent to: cd frontend && npm install && npm run dev
```

The dashboard needs a Supabase project for auth and data; set the `SUPABASE_*`
and `DATABASE_URL` values in `.env` (see Configuration below and
[docs/GUI.md](docs/GUI.md)). Open the Vite dev URL it prints, register an account,
and create a check.

### 5. Web dashboard — Docker (one command)

The whole app (built frontend plus API) runs from a single container. From the
repo root:

```bash
cp .env.example .env          # set OPENAI_API_KEY + SUPABASE_* (+ DATABASE_URL)
docker compose up --build     # or: make up
# open http://localhost:8000 and register an account
```

To actually drive an app the container still needs Appium plus an Android
emulator:

- **macOS / Windows:** run the emulator and Appium on your host. The default
  `APPIUM_SERVER_URL=http://host.docker.internal:4723` reaches them.
- **Linux with `/dev/kvm`:** bring up the bundled emulator container too, and set
  `APPIUM_SERVER_URL=http://emulator:4723` in `.env`:
  ```bash
  docker compose --profile emulator up --build    # or: make up-emulator
  # watch the emulator screen at http://localhost:6080
  ```

Useful Make targets (run `make help` for the full list):

| Target | What it does |
|--------|--------------|
| `make up` | Build and run the app on http://localhost:8000 |
| `make up-emulator` | Run the app plus the bundled Android emulator (Linux + `/dev/kvm`) |
| `make down` | Stop and remove containers |
| `make logs` | Tail the app logs |
| `make seed` | Import sample checks for a Supabase user (`USER_ID=<uuid>`) |
| `make dev-backend` / `make dev-frontend` | Local (non-Docker) dev servers |

## Configuration

All configuration is via environment variables in `.env` (loaded automatically
when `python-dotenv` is installed). Start from `.env.example`.

**Model / VLM**

| Variable | Default | Notes |
|----------|---------|-------|
| `OPENAI_API_KEY` | — | Required when `VLM_PROVIDER=openai` |
| `ANTHROPIC_API_KEY` | — | Required when `VLM_PROVIDER=anthropic` |
| `VLM_PROVIDER` | `openai` | `openai` or `anthropic` |
| `VLM_MODEL` | `gpt-4o` | Must be a vision-capable model |
| `VLM_TEMPERATURE` | `0` | Deterministic by default |
| `VLM_MAX_TOKENS` | `1024` | Per-call output cap |

**Device / Appium**

| Variable | Default | Notes |
|----------|---------|-------|
| `APPIUM_SERVER_URL` | `http://127.0.0.1:4723` | Appium endpoint |
| `ANDROID_AVD` | — | AVD name so Appium can boot the emulator |
| `ANDROID_DEVICE_NAME` | `Android Emulator` | |
| `ANDROID_PLATFORM_VERSION`, `ANDROID_UDID` | — | Optional |
| `APPIUM_NO_RESET` | `true` | Keep app state between sessions |
| `AUTO_START_EMULATOR`, `AUTO_START_APPIUM` | `true` | Auto-launch an AVD / a local Appium server |

**App under test (set ONE option)**

| Variable | Notes |
|----------|-------|
| `APP_PACKAGE` + `APP_ACTIVITY` | Launch an already-installed app |
| `APP_PATH` | Absolute path to an `.apk` to install and launch |

**Dashboard (Supabase + server)**

| Variable | Notes |
|----------|-------|
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Public client values (baked into the frontend at build time) |
| `SUPABASE_SERVICE_ROLE_KEY` | Secret; server-side token verification and Storage |
| `SUPABASE_BUCKET` | Storage bucket for run screenshots (default `run-artifacts`) |
| `DATABASE_URL` | Postgres connection string (Supabase) |
| `CORS_ORIGINS` | Allowed browser origins for the API |

## Outputs and artifacts

- **Per run:** `runs/<timestamp>/` (CLI) or `runs/server-run-<id>/` (dashboard)
  contains one screenshot per step and a `run.json` with the full trace.
- **Eval runs:** `runs/eval-<timestamp>/` adds a `summary.json` with the metrics
  table.
- **Dashboard:** runs, steps, and verdicts are persisted in Supabase Postgres and
  streamed live to the browser; screenshots are mirrored to Supabase Storage.

## Documentation

| Doc | What it covers |
|-----|----------------|
| [docs/RUNNING_phase_0.md](docs/RUNNING_phase_0.md) | The CLI agent and eval harness, end to end |
| [docs/PHASE1.md](docs/PHASE1.md) | The parity checker (web, API, app, and classifier) |
| [docs/GUI.md](docs/GUI.md) | The web dashboard architecture and deployment |

## Notes and limits

- Coordinate taps from prose ("top-right corner") are not interpretable
  deterministically. `tap` resolves by text / resource-id / content-desc / xpath
  and only falls back to coordinates when the target encodes `x,y` or `x%,y%`.
- The source-of-truth value can be injected (`--web-value` or a goal's
  `web_value`) or captured live from a URL with Playwright. Run
  `playwright install chromium` once before using URL capture.
- VLM providers are swappable: the agent loop and the verifier are
  provider-agnostic. Set `VLM_PROVIDER` and `VLM_MODEL`, or add a new provider in
  `vlm.py`.
- Runs serialize on a single worker because there is one emulator; the dashboard
  shows a run's position in the queue.

## License

This project is open source. See the `LICENSE` file for terms; if none is present
yet, treat the code as all-rights-reserved until one is added.
