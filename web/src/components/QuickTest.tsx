import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import RunLive, { verdictClass } from "./RunLive";

// Drag-and-drop an APK, type the test as a prompt, run it.
export default function QuickTest() {
  const [apk, setApk] = useState<any>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const [goal, setGoal] = useState("");
  const [showTruth, setShowTruth] = useState(false);
  const [webValue, setWebValue] = useState("");
  const [webUrl, setWebUrl] = useState("");
  const [webSelector, setWebSelector] = useState("");
  const [error, setError] = useState("");
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [starting, setStarting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function loadHistory() {
    const runs = await api.listQuickRuns();
    setHistory(runs.filter((r: any) => r.trigger === "quick"));
  }
  useEffect(() => { loadHistory(); }, []);

  async function handleFile(file: File) {
    setError("");
    if (!file.name.toLowerCase().endsWith(".apk")) {
      setError("Please drop an .apk file");
      return;
    }
    setUploading(true);
    setProgress(0);
    try {
      const a = await api.uploadApk(file, setProgress);
      setApk(a);
    } catch (err: any) {
      setError(err.message || "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }

  async function runTest() {
    setError("");
    setStarting(true);
    try {
      const run = await api.quickRun({
        apk_id: apk.id,
        goal,
        web_value: webValue || null,
        web_url: webUrl || null,
        web_selector: webSelector || null,
      });
      setActiveRunId(run.id);
      setTimeout(loadHistory, 600);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <div style={{ maxWidth: 820 }}>
      <h1>Quick test</h1>
      <p className="muted" style={{ marginTop: 0 }}>Drop an APK, describe the test in plain language, and run it on the device.</p>

      {/* 1. APK dropzone */}
      <div
        className="card"
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        style={{
          border: `2px dashed ${dragOver ? "var(--accent)" : "var(--border)"}`,
          background: dragOver ? "var(--panel-2)" : "var(--panel)",
          textAlign: "center",
          cursor: "pointer",
          padding: 28,
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".apk"
          style={{ display: "none" }}
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
        />
        {uploading ? (
          <div>
            <div className="pulse">Uploading… {progress}%</div>
            <div style={{ height: 6, background: "var(--bg)", borderRadius: 4, marginTop: 10 }}>
              <div style={{ height: 6, width: `${progress}%`, background: "var(--accent)", borderRadius: 4 }} />
            </div>
          </div>
        ) : apk ? (
          <div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>📦 {apk.label || apk.filename}</div>
            <div className="mono muted" style={{ marginTop: 6 }}>
              {apk.package || "unknown package"} · v{apk.version || "?"}
            </div>
            <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>Click or drop another APK to replace</div>
          </div>
        ) : (
          <div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>⬆ Drag &amp; drop an APK here</div>
            <div className="muted" style={{ marginTop: 6 }}>or click to choose a file</div>
          </div>
        )}
      </div>

      {/* 2. Prompt */}
      <div className="card">
        <label>Test prompt</label>
        <textarea
          rows={3}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="e.g. Skip onboarding, open the markets screen, and read the Bitcoin price"
        />

        <div style={{ marginTop: 10 }}>
          <a style={{ cursor: "pointer", fontSize: 13 }} onClick={() => setShowTruth(!showTruth)}>
            {showTruth ? "− Hide" : "+ Add"} source-of-truth (optional, to verify a value)
          </a>
        </div>
        {showTruth && (
          <div style={{ marginTop: 8 }}>
            <label>Expected value (literal)</label>
            <input value={webValue} onChange={(e) => setWebValue(e.target.value)} placeholder="₹1,099.00" />
            <div className="row" style={{ gap: 14, marginTop: 4 }}>
              <div style={{ flex: 2 }}>
                <label>…or capture from URL</label>
                <input value={webUrl} onChange={(e) => setWebUrl(e.target.value)} placeholder="https://…" />
              </div>
              <div style={{ flex: 1 }}>
                <label>CSS selector</label>
                <input value={webSelector} onChange={(e) => setWebSelector(e.target.value)} placeholder=".price" />
              </div>
            </div>
          </div>
        )}

        {error && <div className="error">{error}</div>}
        <button
          className="primary"
          style={{ marginTop: 16 }}
          disabled={!apk || !goal.trim() || starting || uploading}
          onClick={runTest}
        >
          {starting ? "Starting…" : "▶ Run test"}
        </button>
      </div>

      {activeRunId && <RunLive runId={activeRunId} />}

      <h2>Recent quick tests</h2>
      {history.length === 0 && <div className="muted">No quick tests yet.</div>}
      {history.length > 0 && (
        <table>
          <thead><tr><th>#</th><th>Name</th><th>Verdict</th><th>Status</th><th>Started</th></tr></thead>
          <tbody>
            {history.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => setActiveRunId(r.id)}>
                <td>{r.id}</td>
                <td>{r.name || r.goal?.slice(0, 40)}</td>
                <td><span className={"badge " + verdictClass(r.verdict)}>{r.verdict || "—"}</span></td>
                <td>{r.status}</td>
                <td className="muted">{new Date(r.started_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
