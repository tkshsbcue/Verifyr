import { useEffect, useRef, useState } from "react";
import { api, artifact, streamUrl } from "../api";

export function verdictClass(v?: string | null) {
  if (!v) return "muted";
  if (v === "pass") return "pass";
  if (v === "inconclusive") return "warn";
  return "fail";
}

type Step = any;

export default function RunLive({ runId }: { runId: number }) {
  const [status, setStatus] = useState("connecting");
  const [steps, setSteps] = useState<Step[]>([]);
  const [signals, setSignals] = useState<any>(null);
  const [verdict, setVerdict] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    setSteps([]);
    setSignals(null);
    setVerdict(null);
    setError(null);
    setStatus("connecting");

    // Seed from the stored run (fills signals/summary for completed runs).
    api.getRun(runId).then((r) => {
      setStatus(r.status);
      if (r.steps?.length) setSteps(r.steps);
      if (r.signals) setSignals(r.signals);
      if (r.verdict) setVerdict({ verdict: r.verdict, summary: r.summary, recommended_action: r.recommended_action });
      if (r.error) setError(r.error);
    }).catch(() => {});

    const ws = new WebSocket(streamUrl(runId));
    wsRef.current = ws;
    ws.onopen = () => setStatus("running");
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      switch (m.type) {
        case "snapshot":
          setStatus(m.status);
          if (m.steps?.length) setSteps(m.steps);
          if (m.verdict) setVerdict({ verdict: m.verdict });
          break;
        case "status":
          setStatus(m.status);
          break;
        case "step":
          setSteps((s) => [...s, m]);
          break;
        case "signal":
          setSignals((sig: any) => ({ ...(sig || {}), [m.name]: m.value }));
          break;
        case "signals_final":
          setSignals(m);
          break;
        case "verdict":
          setVerdict(m);
          break;
        case "done":
          setStatus(m.status);
          if (m.error) setError(m.error);
          break;
      }
    };
    ws.onerror = () => setStatus("disconnected");
    return () => ws.close();
  }, [runId]);

  const running = status === "running" || status === "connecting" || status === "queued";

  return (
    <div>
      <div className="card">
        <div className="row spread">
          <h2 style={{ margin: 0 }}>Live run #{runId}</h2>
          <span className={running ? "badge muted pulse" : "badge muted"}>{status}</span>
        </div>
        {signals && (
          <div className="signals" style={{ marginTop: 12 }}>
            {["web_value", "api_value", "app_ui_value", "verifier_result", "installed_build"].map((k) =>
              signals[k] !== undefined ? (
                <>
                  <div className="k">{k}</div>
                  <div className="mono">{String(signals[k] ?? "—")}</div>
                </>
              ) : null
            )}
          </div>
        )}
        {verdict && (
          <div style={{ marginTop: 14 }}>
            <span className={"badge " + verdictClass(verdict.verdict)}>{verdict.verdict}</span>
            {verdict.summary && <p style={{ marginBottom: 4 }}>{verdict.summary}</p>}
            {verdict.recommended_action && (
              <p className="muted" style={{ margin: 0 }}>→ {verdict.recommended_action}</p>
            )}
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>

      <h2>Agent steps ({steps.length})</h2>
      {steps.length === 0 && <div className="muted">Waiting for the agent…</div>}
      {steps.map((s, i) => (
        <div className="step" key={i}>
          <div className="head">
            <span className="muted">#{s.step}</span>
            <span className="action">{s.action?.type}</span>
            <span className="muted">{s.action?.target || s.action?.reason || s.action?.found_value || ""}</span>
          </div>
          {s.observation && <div className="obs">{s.observation}</div>}
          {s.screenshot_url && <img src={artifact(s.screenshot_url) || ""} alt={`step ${s.step}`} />}
        </div>
      ))}
    </div>
  );
}
