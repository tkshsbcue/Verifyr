import { useState } from "react";
import { api } from "../api";

// Create/edit a check. `existing` is null for create.
export default function CheckEditor({
  existing,
  onSaved,
  onCancel,
}: {
  existing: any | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const cfg = existing?.config || {};
  const t0 = (cfg.app_targets && cfg.app_targets[0]) || {};
  const [name, setName] = useState(existing?.name || "");
  const [webUrl, setWebUrl] = useState(cfg.web?.url || "");
  const [webSelector, setWebSelector] = useState(cfg.web?.selector || "");
  const [targetDesc, setTargetDesc] = useState(cfg.web?.target_description || "");
  const [apiEndpoint, setApiEndpoint] = useState(cfg.api?.endpoint || "");
  const [jsonPath, setJsonPath] = useState(cfg.api?.json_path || "");
  const [pkg, setPkg] = useState(t0.package || "com.empirecrypto.mobile");
  const [goal, setGoal] = useState(t0.goal || "");
  const [labelV, setLabelV] = useState(t0.label || "");
  const [requiresBuild, setRequiresBuild] = useState(t0.requires_build || "");
  const [schedule, setSchedule] = useState(existing?.schedule || "");
  const [alertEmail, setAlertEmail] = useState(existing?.alert_email || "");
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setError("");
    setBusy(true);
    const payload = {
      name,
      schedule: schedule || null,
      alert_email: alertEmail || null,
      enabled,
      config: {
        web: { url: webUrl, selector: webSelector || null, target_description: targetDesc || null },
        api: { endpoint: apiEndpoint || null, json_path: jsonPath || null, headers: {} },
        app_targets: [
          {
            platform: "android",
            package: pkg,
            goal,
            label: labelV,
            requires_build: requiresBuild || null,
          },
        ],
      },
    };
    try {
      if (existing) await api.updateCheck(existing.id, payload);
      else await api.createCheck(payload);
      onSaved();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 720 }}>
      <h1>{existing ? "Edit check" : "New check"}</h1>
      <label>Name</label>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="BTC price" />

      <h2 style={{ marginTop: 18 }}>Source of truth (web)</h2>
      <label>URL</label>
      <input value={webUrl} onChange={(e) => setWebUrl(e.target.value)} placeholder="https://…" />
      <div className="row" style={{ gap: 14 }}>
        <div style={{ flex: 1 }}>
          <label>CSS selector (preferred)</label>
          <input value={webSelector} onChange={(e) => setWebSelector(e.target.value)} placeholder=".a-price .a-offscreen" />
        </div>
        <div style={{ flex: 1 }}>
          <label>or describe the value (VLM extract)</label>
          <input value={targetDesc} onChange={(e) => setTargetDesc(e.target.value)} placeholder="current BTC price USD" />
        </div>
      </div>

      <h2 style={{ marginTop: 18 }}>Backend API (optional)</h2>
      <div className="row" style={{ gap: 14 }}>
        <div style={{ flex: 2 }}>
          <label>Endpoint</label>
          <input value={apiEndpoint} onChange={(e) => setApiEndpoint(e.target.value)} placeholder="https://api…" />
        </div>
        <div style={{ flex: 1 }}>
          <label>JSON path</label>
          <input value={jsonPath} onChange={(e) => setJsonPath(e.target.value)} placeholder="data.price" />
        </div>
      </div>

      <h2 style={{ marginTop: 18 }}>App target (Android)</h2>
      <div className="row" style={{ gap: 14 }}>
        <div style={{ flex: 2 }}>
          <label>Package</label>
          <input value={pkg} onChange={(e) => setPkg(e.target.value)} />
        </div>
        <div style={{ flex: 1 }}>
          <label>Requires build (optional)</label>
          <input value={requiresBuild} onChange={(e) => setRequiresBuild(e.target.value)} placeholder="1.6.0" />
        </div>
      </div>
      <label>Goal (agent navigation instruction)</label>
      <textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={2} placeholder="Open the markets screen and read the BTC price" />
      <label>Label (value being checked)</label>
      <input value={labelV} onChange={(e) => setLabelV(e.target.value)} placeholder="BTC price" />

      <h2 style={{ marginTop: 18 }}>Schedule & alerts</h2>
      <div className="row" style={{ gap: 14 }}>
        <div style={{ flex: 1 }}>
          <label>Cron (optional, e.g. */30 * * * *)</label>
          <input value={schedule} onChange={(e) => setSchedule(e.target.value)} placeholder="*/30 * * * *" />
        </div>
        <div style={{ flex: 1 }}>
          <label>Alert email (on regression)</label>
          <input value={alertEmail} onChange={(e) => setAlertEmail(e.target.value)} />
        </div>
      </div>
      <label className="row" style={{ gap: 8, marginTop: 12 }}>
        <input type="checkbox" style={{ width: "auto" }} checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <span style={{ color: "var(--text)" }}>Enabled</span>
      </label>

      {error && <div className="error">{error}</div>}
      <div className="row" style={{ marginTop: 18, gap: 10 }}>
        <button className="primary" onClick={save} disabled={busy || !name}>{busy ? "Saving…" : "Save"}</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
