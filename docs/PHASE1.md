# Phase 1 — Web-to-Mobile Parity

Phase 1 turns the Phase 0 agent into a **parity checker** that answers not just
"does the app show the right value?" but **"why is it wrong, and what should I do?"**

It is driven by `idea/prompt1.json` (which supersedes and carries everything from
`prompt0.json`).

## The idea

For one value (e.g. "BTC price"), Phase 1 collects up to three signals and has an
LLM classify the outcome:

| Signal | Source | Answers |
|--------|--------|---------|
| `web_value` | website (CSS selector or VLM extraction) | the source of truth |
| `api_value` | the backend API the app uses | did the change reach the backend? |
| `app_ui_value` | what the agent reads on the app screen | what does the user actually see? |

The **classifier** combines them into one verdict:

| Verdict | Meaning |
|---------|---------|
| `pass` | app matches the source of truth |
| `stale_client_cache` | backend is correct, app shows old data (client cache) |
| `not_propagated_to_backend` | the change never reached the API the app uses (deploy/CDN/config) |
| `needs_app_release` | value requires a newer app build than installed |
| `rendering_issue` | value is present but visually broken |
| `inconclusive` | signals missing / can't tell |

## Flow (orchestrated by `parity.py`)

1. **Web value** — `web.selector` via Playwright, else VLM extraction from page text.
2. **API check** (cheap, first) — GET `api.endpoint`, read `api.json_path`.
3. **Route** —
   - API differs from web → backend is stale → **skip the device** (`not_propagated_to_backend`).
   - API matches web, or no API → **drive the device** to see what the user gets.
4. **Device** — the Phase 0 agent navigates (`app_targets[].goal`), asserts the
   on-screen value; the verifier compares web vs app (and flags broken rendering).
   On a stale-looking mismatch, runs `stale_retry_actions` (relaunch / pull-to-refresh) once and re-reads.
5. **Classify** all signals → verdict + plain-language summary + recommended action.
6. **Store** the full result under `runs/parity-<timestamp>/`.

## Files added in Phase 1

| File | Concern |
|------|---------|
| `checks.py` | the checks store (JSON) + schema dataclasses |
| `api_check.py` | backend API fetch + `json_path` extraction (stdlib + certifi) |
| `web_extractor.py` | page-text capture + VLM value extraction (no-selector fallback) |
| `buildinfo.py` | installed app version (adb) + version comparison |
| `classifier.py` | the propagation classifier |
| `parity.py` | the orchestrator + CLI |
| `checks.json` | sample checks |

(Plus Phase 0 updates: `config.py` loads the new prompt fields and strips
`CARRIED/NEW` annotation markers; `verifier.py` parses `rendering_broken`;
`agent.py` exposes the asserted value/screenshot; `device.py` adds
`relaunch_app` / `pull_to_refresh`.)

## Defining checks

Edit `checks.json`. Each check:

```json
{
  "name": "BTC price",
  "web": { "url": "https://...", "selector": ".price", "target_description": "current BTC price USD" },
  "api": { "endpoint": "https://api.../price", "json_path": "bitcoin.usd", "headers": {} },
  "app_targets": [
    { "platform": "android", "package": "com.empirecrypto.mobile",
      "goal": "Open the markets screen and read the BTC price",
      "label": "BTC price", "requires_build": null }
  ]
}
```

- `web.selector` is preferred; omit it to let the VLM extract by `target_description`.
- `api` is optional — without it, the device UI is the only signal.
- `requires_build` (e.g. `"1.6.0"`) triggers `needs_app_release` when the installed build is older.

## Running

Prerequisites are the same as Phase 0 (emulator + Appium running, `.env` set —
see [RUNNING_phase_0.md](RUNNING_phase_0.md)).

```bash
cd backend && source ../.venv/bin/activate
python -m verifyr.parity --check "BTC price (demo: public API)"   # one check
python -m verifyr.parity --all                                    # every check in checks.json
python -m verifyr.parity --all --checks mychecks.json --quiet
```

Output: a verdict table on the console and per-check JSON + agent traces under
`runs/parity-<timestamp>/`.

## Notes & limits

- The classifier is the authority on the final verdict; code only does a loose
  normalized compare (currency/whitespace/case-insensitive) for the device-or-not
  routing decision.
- SQLite was specified for the store; we use JSON to match the "local files only"
  scope. The `checks.py` API can be swapped to SQLite later without touching callers.
- `app_targets` supports multiple platforms in the schema, but only the Android
  target is executed in Phase 1.
- Verifier (web vs app) only runs when a `web_value` was resolved; otherwise the
  classifier works from API + UI signals alone.
