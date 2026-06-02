import { useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import Login from "./components/Login";
import CheckEditor from "./components/CheckEditor";
import QuickTest from "./components/QuickTest";
import RunLive, { verdictClass } from "./components/RunLive";

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");

  useEffect(() => {
    if (!getToken()) return setAuthed(false);
    api.me().then((u) => { setEmail(u.email); setAuthed(true); }).catch(() => setAuthed(false));
  }, []);

  if (authed === null) return <div className="center muted">Loading…</div>;
  if (!authed) return <Login onAuthed={() => window.location.reload()} />;

  return <Dashboard email={email} onLogout={() => { setToken(null); window.location.reload(); }} />;
}

function Dashboard({ email, onLogout }: { email: string; onLogout: () => void }) {
  const [tab, setTab] = useState<"quick" | "checks">("quick");
  const [checks, setChecks] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editing, setEditing] = useState<null | "new" | number>(null);

  async function refresh() {
    const cs = await api.listChecks();
    setChecks(cs);
    if (selectedId == null && cs.length) setSelectedId(cs[0].id);
  }
  useEffect(() => { if (tab === "checks") refresh(); /* eslint-disable-next-line */ }, [tab]);

  const selected = checks.find((c) => c.id === selectedId) || null;

  return (
    <>
      <div className="topbar">
        <div className="row" style={{ gap: 20 }}>
          <div className="brand">Verif<span>yr</span></div>
          <div className="row" style={{ gap: 4 }}>
            <button className={tab === "quick" ? "primary" : ""} onClick={() => setTab("quick")}>Quick test</button>
            <button className={tab === "checks" ? "primary" : ""} onClick={() => setTab("checks")}>Saved checks</button>
          </div>
        </div>
        <div className="row" style={{ gap: 12 }}>
          <span className="muted">{email}</span>
          <button onClick={onLogout}>Sign out</button>
        </div>
      </div>

      {tab === "quick" && <div className="main"><QuickTest /></div>}

      {tab === "checks" && (
      <div className="layout">
        <div className="sidebar">
          <button className="primary" style={{ width: "100%", marginBottom: 12 }} onClick={() => setEditing("new")}>
            + New check
          </button>
          {checks.length === 0 && <div className="muted" style={{ padding: 8 }}>No checks yet.</div>}
          {checks.map((c) => (
            <div
              key={c.id}
              className={"check-item" + (c.id === selectedId && editing === null ? " active" : "")}
              onClick={() => { setSelectedId(c.id); setEditing(null); }}
            >
              <div className="row spread">
                <span className="name">{c.name}</span>
                <span className={"badge " + verdictClass(c.last_verdict)}>{c.last_verdict || "—"}</span>
              </div>
              <div className="meta">{c.schedule ? `cron ${c.schedule}` : "manual"}{c.enabled ? "" : " · disabled"}</div>
            </div>
          ))}
        </div>
        <div className="main">
          {editing === "new" && <CheckEditor existing={null} onSaved={() => { setEditing(null); refresh(); }} onCancel={() => setEditing(null)} />}
          {typeof editing === "number" && (
            <CheckEditor existing={checks.find((c) => c.id === editing)} onSaved={() => { setEditing(null); refresh(); }} onCancel={() => setEditing(null)} />
          )}
          {editing === null && selected && (
            <CheckDetail check={selected} onEdit={() => setEditing(selected.id)} onChanged={refresh} />
          )}
          {editing === null && !selected && <div className="empty">Select or create a check to begin.</div>}
        </div>
      </div>
      )}
    </>
  );
}

function CheckDetail({ check, onEdit, onChanged }: { check: any; onEdit: () => void; onChanged: () => void }) {
  const [runs, setRuns] = useState<any[]>([]);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadRuns() {
    setRuns(await api.listRuns(check.id));
  }
  useEffect(() => { setActiveRunId(null); loadRuns(); /* eslint-disable-next-line */ }, [check.id]);

  async function runNow() {
    setBusy(true);
    try {
      const run = await api.runCheck(check.id);
      setActiveRunId(run.id);
      setTimeout(loadRuns, 500);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm(`Delete check "${check.name}"?`)) return;
    await api.deleteCheck(check.id);
    onChanged();
  }

  const t = check.config?.app_targets?.[0] || {};
  return (
    <div>
      <div className="row spread">
        <h1>{check.name}</h1>
        <div className="row" style={{ gap: 8 }}>
          <button className="primary" onClick={runNow} disabled={busy}>{busy ? "Starting…" : "▶ Run now"}</button>
          <button onClick={onEdit}>Edit</button>
          <button className="danger" onClick={remove}>Delete</button>
        </div>
      </div>

      <div className="card">
        <div className="signals">
          <div className="k">Web</div><div className="mono">{check.config?.web?.url || "—"} {check.config?.web?.selector ? `(${check.config.web.selector})` : ""}</div>
          <div className="k">API</div><div className="mono">{check.config?.api?.endpoint || "—"} {check.config?.api?.json_path ? `→ ${check.config.api.json_path}` : ""}</div>
          <div className="k">Goal</div><div>{t.goal || "—"}</div>
          <div className="k">Package</div><div className="mono">{t.package || "—"}</div>
          <div className="k">Schedule</div><div className="mono">{check.schedule || "manual"}</div>
          <div className="k">Alert</div><div className="mono">{check.alert_email || "—"}</div>
        </div>
      </div>

      {activeRunId && <RunLive runId={activeRunId} />}

      <h2>History</h2>
      {runs.length === 0 && <div className="muted">No runs yet.</div>}
      {runs.length > 0 && (
        <table>
          <thead><tr><th>#</th><th>Verdict</th><th>Status</th><th>Trigger</th><th>Started</th></tr></thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => setActiveRunId(r.id)}>
                <td>{r.id}</td>
                <td><span className={"badge " + verdictClass(r.verdict)}>{r.verdict || "—"}</span></td>
                <td>{r.status}</td>
                <td>{r.trigger}</td>
                <td className="muted">{new Date(r.started_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
