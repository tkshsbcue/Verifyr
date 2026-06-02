# Verifyr GUI — Dashboard

A web dashboard for the parity engine: manage checks, trigger runs, watch the
agent live, browse history, and schedule checks with regression alerts.

- **Backend:** FastAPI + SQLAlchemy (SQLite by default, any DB via `DATABASE_URL`),
  JWT auth, APScheduler. Wraps the existing engine (`parity.py` / `agent.py`).
- **Frontend:** React + Vite + TypeScript.
- **Live progress:** the runner executes a check in a worker thread and streams
  events (`step`, `signal`, `verdict`, `done`) to the browser over a WebSocket.

```
Browser (React)  ──HTTP/WS──>  FastAPI  ──>  runner thread  ──>  parity.run_check
     ▲                            │                                  │ (Reporter)
     └──────── WebSocket ─────────┴────────── EventBus <─────────────┘
                                  └──> SQLite/Postgres (checks, runs, steps)
                                  └──> APScheduler (cron) ──> enqueue run ──> alert
```

## Architecture

| Path | Concern |
|------|---------|
| `server/main.py` | FastAPI app, CORS, static (artifacts + built frontend), startup/shutdown |
| `server/db.py`, `server/models.py` | engine/session; `User`, `Check`, `Run` tables |
| `server/security.py`, `server/deps.py` | bcrypt hashing, JWT, `current_user` dependency |
| `server/routers/auth.py` | register / login / me |
| `server/routers/checks.py` | checks CRUD + `POST /{id}/run` |
| `server/routers/runs.py` | run list/detail + `WS /{id}/stream` |
| `server/runner.py` | threaded runner → engine via `CallbackReporter`, persists + streams |
| `server/scheduler.py` | APScheduler cron jobs from each check's `schedule` |
| `server/seed.py` | import `checks.json`, create an initial user |
| `reporting.py` | `Reporter` hook the engine emits events through (repo root) |
| `web/` | React dashboard (Vite) |

## Run with Docker (easiest)

The whole app runs from one container — built frontend, API, and a SQLite
database on a named volume. From the repo root:

```bash
cp .env.example .env        # set OPENAI_API_KEY (+ JWT_SECRET, ADMIN_EMAIL/PASSWORD)
docker compose up --build   # or: make up
# open http://localhost:8000  and log in with ADMIN_EMAIL / ADMIN_PASSWORD
```

That gives you the full dashboard with persistence. To actually drive an app you
still need Appium + an Android emulator reachable from the container:

- **macOS / Windows** — run the emulator + Appium on your host (see
  [RUNNING_phase_0.md](RUNNING_phase_0.md)). The default
  `APPIUM_SERVER_URL=http://host.docker.internal:4723` reaches it.
- **Linux with `/dev/kvm`** — bring up the bundled emulator too:
  ```bash
  docker compose --profile emulator up --build      # or: make up-emulator
  ```
  Set `APPIUM_SERVER_URL=http://emulator:4723` in `.env`, and watch the screen at
  `http://localhost:6080` (noVNC).

`make` shortcuts: `make up`, `make logs`, `make seed`, `make down`, `make shell`.

Data (DB, uploaded APKs, run artifacts) persists in the `verifyr-data` volume.

## Prerequisites (local, without Docker)

Everything from [RUNNING_phase_0.md](RUNNING_phase_0.md) (emulator + Appium + `.env`), plus:

```bash
source .venv/bin/activate
pip install -r backend/requirements-server.txt        # backend deps
cd frontend && npm install && cd ..                 # frontend deps (Node 18+)
```

## Running (development)

Four processes. The emulator + Appium are the same as the CLI.

```bash
# 1) emulator         (see RUNNING_phase_0.md)
# 2) appium

# 3) backend API  (run from backend/)
cd backend
source ../.venv/bin/activate
export JWT_SECRET="$(python -c 'import secrets;print(secrets.token_hex(32))')"
uvicorn server.main:app --reload --port 8000

# 4) frontend (Vite dev server, proxies /api + /artifacts to :8000)
cd frontend && npm run dev          # open http://localhost:5173
```

First time, create a user (either click **Register** in the UI, or seed from `backend/`):

```bash
cd backend && python -m server.seed --checks checks.json --email you@co.com --password secret123
```

## Running (single-process, production-style)

Build the frontend; the backend then serves it at `/`:

```bash
cd frontend && npm run build && cd ../backend
uvicorn server.main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

## Using it

### Quick test (primary flow)
The **Quick test** tab is the fastest path: **drag-and-drop an APK** (parsed for
package/version/label and installed by Appium at run time), **type the test as a
plain-language prompt**, optionally add an expected value / source-of-truth URL,
and hit **Run test**. The agent drives the uploaded app live; steps, screenshots,
and the verdict stream in, and the run is saved under "Recent quick tests".

- `POST /api/apks` (multipart) → `{id, package, version, label}`
- `POST /api/runs/quick` `{apk_id, goal, web_value?, web_url?, web_selector?}`
- The verdict is the agent outcome (`pass` when the goal screen was reached; if a
  source-of-truth was given, the verifier gates it).

### Saved checks (parity)
- **Checks** (left): create/edit/delete; each shows its last verdict.
- **Run now**: triggers a run; the **live panel** streams agent steps + screenshots,
  the parity signals (web / api / app / verifier), and the final verdict.
- **History**: every run per check; click one to replay its steps and verdict.
- **Schedule**: set a cron expression (e.g. `*/30 * * * *`) to run automatically.
- **Alerts**: set an alert email; on a *regression* to a non-`pass` verdict the
  server emails (if SMTP configured) or logs the alert.

## Configuration (env)

| Var | Default | Notes |
|-----|---------|-------|
| `DATABASE_URL` | `sqlite:///./verifyr.db` | use `postgresql+psycopg://…` for hosting |
| `JWT_SECRET` | dev placeholder | **set a 32+ byte random value in production** |
| `JWT_EXPIRE_MINUTES` | `1440` | token lifetime |
| `CORS_ORIGINS` | localhost:5173 | comma-separated allowed origins |
| `SMTP_HOST/PORT/USER/PASSWORD`, `ALERT_FROM` | unset | enable email alerts |

Plus all the engine vars (`OPENAI_API_KEY`, `APP_PACKAGE`, `IMPERSONATE_KEY`, …).

## Deployment notes (team-hosted)

- `docker compose up` runs the whole app (frontend + API + SQLite volume). For a
  shared/hosted deployment, switch to Postgres by setting `DATABASE_URL`
  (e.g. `postgresql+psycopg://user:pass@host/db`) in `.env` and adding a Postgres
  service — `psycopg` is already installed in the image.
- Set a strong `JWT_SECRET` and put it behind HTTPS (a reverse proxy) before
  exposing it.
- **The emulator is the hard part.** The `app` container drives apps over Appium
  but does not contain a device. The bundled `emulator` profile
  (`budtmo/docker-android`) needs a **Linux host with `/dev/kvm`** (bare-metal or
  nested-virt). On macOS/Windows, Docker can't pass through KVM, so run the
  emulator + Appium on the host and point `APPIUM_SERVER_URL` at
  `host.docker.internal`. This device infrastructure is the main work for true
  multi-user hosting.
- Runs serialize through a single worker because there's one device. For parallel
  throughput you'd run one worker per connected device (future work).

## Security checklist before exposing it

- Set a strong `JWT_SECRET`; serve over HTTPS.
- `IMPERSONATE_KEY` (Supabase service-role) is a powerful secret — keep it in the
  server environment only, never in the DB or the prompt. (It already is.)
- Consider restricting `/api/auth/register` (currently open) to invite-only.
