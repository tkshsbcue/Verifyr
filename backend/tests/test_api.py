"""API-level tests: ownership scoping, runs, cancellation, artifact safety."""

from __future__ import annotations

from conftest import USER_A, USER_B


def _make_check(client, name="BTC price"):
    payload = {
        "name": name,
        "config": {
            "web": {"url": "https://example.com", "selector": ".price"},
            "api": {"endpoint": "https://api.example", "json_path": "bitcoin.usd"},
            "app_targets": [{"platform": "android", "package": "com.x", "goal": "open markets", "label": "BTC"}],
        },
        "schedule": None,
        "alert_email": None,
        "enabled": True,
    }
    r = client.post("/api/checks", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ---- checks CRUD ----
def test_check_crud_roundtrip(client):
    created = _make_check(client)
    cid = created["id"]
    assert created["name"] == "BTC price"
    assert created["last_verdict"] is None

    assert client.get("/api/checks").json()[0]["id"] == cid
    assert client.get(f"/api/checks/{cid}").json()["name"] == "BTC price"

    upd = client.put(f"/api/checks/{cid}", json={"name": "renamed", "schedule": "*/30 * * * *"})
    assert upd.status_code == 200
    assert upd.json()["name"] == "renamed"
    assert upd.json()["schedule"] == "*/30 * * * *"

    assert client.delete(f"/api/checks/{cid}").status_code == 204
    assert client.get(f"/api/checks/{cid}").status_code == 404


# ---- ownership scoping ----
def test_checks_are_scoped_per_user(client, as_user):
    as_user(USER_A)
    a_check = _make_check(client, "A's check")

    as_user(USER_B)
    assert client.get("/api/checks").json() == []          # B sees nothing
    assert client.get(f"/api/checks/{a_check['id']}").status_code == 404  # and can't fetch A's
    assert client.delete(f"/api/checks/{a_check['id']}").status_code == 404


# ---- triggering a run ----
def test_run_now_enqueues(client):
    check = _make_check(client)
    r = client.post(f"/api/checks/{check['id']}/run")
    assert r.status_code == 202
    run = r.json()
    assert run["status"] == "queued"
    assert run["trigger"] == "manual"

    runs = client.get(f"/api/runs?check_id={check['id']}").json()
    assert [x["id"] for x in runs] == [run["id"]]


def test_run_detail_includes_queue_position(client):
    check = _make_check(client)
    run = client.post(f"/api/checks/{check['id']}/run").json()
    detail = client.get(f"/api/runs/{run['id']}").json()
    # Only one run, so it's next up.
    assert detail["queue_position"] == 0


def test_runs_scoped_per_user(client, as_user):
    as_user(USER_A)
    check = _make_check(client)
    run = client.post(f"/api/checks/{check['id']}/run").json()

    as_user(USER_B)
    assert client.get("/api/runs").json() == []
    assert client.get(f"/api/runs/{run['id']}").status_code == 404


# ---- cancellation ----
def test_cancel_queued_run(client):
    check = _make_check(client)
    run = client.post(f"/api/checks/{check['id']}/run").json()

    r = client.post(f"/api/runs/{run['id']}/cancel")
    assert r.status_code == 200
    body = r.json()
    # Worker is stubbed with an un-run Future -> it cancels immediately.
    assert body["result"] == "cancelled"
    assert body["status"] == "cancelled"

    # Cancelling again is a no-op.
    again = client.post(f"/api/runs/{run['id']}/cancel").json()
    assert again["result"] == "noop"

    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["status"] == "cancelled"
    assert detail["queue_position"] is None  # no longer queued


def test_cancel_other_users_run_is_404(client, as_user):
    as_user(USER_A)
    check = _make_check(client)
    run = client.post(f"/api/checks/{check['id']}/run").json()

    as_user(USER_B)
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 404


# ---- artifact proxy safety ----
def test_artifact_rejects_path_traversal(client, monkeypatch):
    import server.routers.runs as runs_mod

    monkeypatch.setattr(runs_mod, "verify_token", lambda token: USER_A)
    check = _make_check(client)
    run = client.post(f"/api/checks/{check['id']}/run").json()

    r = client.get(f"/api/runs/{run['id']}/artifact", params={"file": "../../etc/passwd", "token": "t"})
    assert r.status_code == 400


def test_artifact_unauthenticated(client, monkeypatch):
    import server.routers.runs as runs_mod

    monkeypatch.setattr(runs_mod, "verify_token", lambda token: None)
    r = client.get("/api/runs/1/artifact", params={"file": "step-01.png", "token": "bad"})
    assert r.status_code == 401


def test_quick_run_requires_existing_apk(client):
    r = client.post("/api/runs/quick", json={"apk_id": 999, "goal": "do a thing"})
    assert r.status_code == 404
