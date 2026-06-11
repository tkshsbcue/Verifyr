# Verifyr GUI — Dashboard

A web dashboard for the parity engine: manage checks, trigger runs, watch the
agent live, browse history, and schedule checks with regression alerts.

- **Backend:** FastAPI + SQLAlchemy (Supabase Postgres via `DATABASE_URL`),
  APScheduler. Wraps the existing engine (`parity.py` / `agent.py`). Stays the
  gateway between the browser and Supabase.
- **Auth + storage:** [Supabase](https://supabase.com) — Auth (the frontend signs
  in directly), Postgres (all data), and Storage (per-user run screenshots).
  Every check / run / APK is scoped to the signed-in user.
- **Frontend:** React + Vite + TypeScript, with a Supabase-backed `AuthProvider`.
- **Live progress:** the runner executes a check in a worker thread and streams
  events (`step`, `signal`, `verdict`, `done`) to the browser over a WebSocket.

```
Browser (React) ──Supabase Auth──> Supabase  (sign in → access token)
     │                                 ▲
     └─HTTP/WS (Bearer token)──> FastAPI ─verify token─┘
                                   │   └──> runner thread ──> parity.run_check
                                   │              │ (Reporter; screenshots → Storage)
                                   └─ EventBus <──┘
                                   └──> Supabase Postgres (checks, runs, steps)
                                   └──> APScheduler (cron) ──> enqueue run ──> alert
```

## Architecture

| Path | Concern |
|------|---------|
| `server/main.py` | FastAPI app, CORS, built-frontend mount, startup/shutdown |
| `server/db.py`, `server/models.py` | engine/session; `Check`, `Apk`, `Run` tables (each has `user_id`) |
| `server/supabase_client.py`, `server/deps.py` | Supabase token verification + Storage; `current_user` dependency |
| `server/routers/auth.py` | `GET /me` (sign-up / sign-in happen on the client via Supabase) |
| `server/routers/checks.py` | checks CRUD + `POST /{id}/run` (scoped per-user) |
| `server/routers/runs.py` | run list/detail, `POST /{id}/cancel`, `GET /{id}/artifact` (ownership-checked screenshots), `WS /{id}/stream` |
| `server/runner.py` | threaded runner → engine via `CallbackReporter`; persists, streams, mirrors screenshots to Storage |
| `server/scheduler.py` | APScheduler cron jobs from each check's `schedule` |
| `server/seed.py` | import `checks.json` for a Supabase user |
| `reporting.py` | `Reporter` hook the engine emits events through (repo root) |
| `frontend/src/auth.tsx`, `frontend/src/supabase.ts` | `AuthProvider` + Supabase client; keeps the bearer token synced, and on a `401` refreshes the token and replays the request once, bouncing to a "session expired" sign-in only if that fails |

## Run with Docker (easiest)

The whole app runs from one container — built frontend + API. Auth, data, and
screenshots live in Supabase. From the repo root:

```bash
cp .env.example .env        # set OPENAI_API_KEY + SUPABASE_* + DATABASE_URL
docker compose up --build   # or: make up
# open http://localhost:8000  and click "Register" to create an account
```

The `SUPABASE_*` values are pre-filled for the project's Supabase instance; you
still need to supply the **service-role key** and the **database password** (see
the Configuration table below) — they're secret and not committed.

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

# 3) backend API  (run from backend/; reads SUPABASE_* + DATABASE_URL from ../.env)
cd backend
source ../.venv/bin/activate
uvicorn server.main:app --reload --port 8000

# 4) frontend (Vite dev server, proxies /api to :8000; reads frontend/.env)
cd frontend && cp .env.example .env && npm run dev   # open http://localhost:5173
```

Create an account by clicking **Register** in the UI (handled by Supabase Auth).
To pre-load sample checks for that user, grab their id from the Supabase
dashboard (Authentication → Users) and seed from `backend/`:

```bash
cd backend && python -m server.seed --checks checks.json --user-id <supabase-user-uuid>
```

## Running (single-process, production-style)

Build the frontend; the backend then serves it at `/`:

```bash
cd frontend && npm run build && cd ../backend
uvicorn server.main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

## Tests

Backend tests use `pytest` against an isolated SQLite DB with auth stubbed and the
run worker replaced (no device needed):

```bash
cd backend && source ../.venv/bin/activate
pip install -r requirements-server.txt   # includes pytest
python -m pytest
```

Coverage: engine pure logic (version comparison, `json_path` extraction, the
checks schema, parity value-matching) and the API (per-user ownership scoping for
checks/runs, run triggering, queue position, cancellation, and the artifact
proxy's path-traversal/auth guards).

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
- **Cancel / queue**: while a run is queued or running, the live panel shows its
  position in the shared queue (one device = one worker) and a **Cancel** button.
  Cancelling a queued run stops it immediately; a running run stops cleanly at the
  agent's next step (`POST /api/runs/{id}/cancel`).
- **History**: every run per check; click one to replay its steps and verdict.
- **Schedule**: set a cron expression (e.g. `*/30 * * * *`) to run automatically.
- **Alerts**: set an alert email; on a *regression* to a non-`pass` verdict the
  server emails (if SMTP configured) or logs the alert.

## Configuration (env)

| Var | Default | Notes |
|-----|---------|-------|
| `DATABASE_URL` | `sqlite:///./verifyr.db` | set to the Supabase Postgres URL (`postgresql+psycopg://…`); needs the DB password |
| `SUPABASE_URL` | — | project API URL (e.g. `https://<ref>.supabase.co`) |
| `SUPABASE_ANON_KEY` | — | publishable/anon key (public; used to verify tokens) |
| `SUPABASE_SERVICE_ROLE_KEY` | — | **secret** — Storage uploads + signed URLs (server only) |
| `SUPABASE_BUCKET` | `run-artifacts` | private bucket for run screenshots |
| `CORS_ORIGINS` | localhost:5173 | comma-separated allowed origins |
| `SMTP_HOST/PORT/USER/PASSWORD`, `ALERT_FROM` | unset | enable email alerts |

Frontend (`frontend/.env`): `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, and
optional `VITE_API_BASE`.

Plus all the engine vars (`OPENAI_API_KEY`, `APP_PACKAGE`, `IMPERSONATE_KEY`, …).

## Deployment notes (team-hosted)

- `docker compose up` runs the whole app (frontend + API). Auth, data, and
  screenshots are hosted by Supabase, so it's multi-user out of the box —
  point `DATABASE_URL` + `SUPABASE_*` at your project.
- RLS policies on `checks` / `apks` / `runs` and the `run-artifacts` bucket scope
  every row/object to its owner; the backend additionally filters by `user_id`.
- Put it behind HTTPS (a reverse proxy) before exposing it, and keep
  `SUPABASE_SERVICE_ROLE_KEY` server-side only.
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

- Serve over HTTPS.
- Keep `SUPABASE_SERVICE_ROLE_KEY` (and `IMPERSONATE_KEY`, the Supabase
  service-role key used by the app-under-test login flow) in the server
  environment only — never in the DB, the prompt, or the frontend bundle.
- Leave RLS enabled on all tables and the storage bucket (defense-in-depth even
  though the backend uses the service role).
- To restrict sign-ups, disable open registration in the Supabase dashboard
  (Authentication → Providers → Email → "Allow new users to sign up") and invite
  users instead.
