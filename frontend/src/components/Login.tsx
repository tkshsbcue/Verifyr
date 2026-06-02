import { useState } from "react";
import { api, setToken } from "../api";

export default function Login({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = mode === "login" ? await api.login(email, password) : await api.register(email, password);
      setToken(res.access_token);
      onAuthed();
    } catch (err: any) {
      setError(err.message || "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="card auth-box" onSubmit={submit}>
        <div className="brand" style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>
          Verif<span style={{ color: "var(--accent)" }}>yr</span>
        </div>
        <div className="muted" style={{ marginBottom: 14 }}>Web-to-mobile parity dashboard</div>
        <label>Email</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <label>Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {error && <div className="error">{error}</div>}
        <button className="primary" style={{ width: "100%", marginTop: 16 }} disabled={busy}>
          {busy ? "..." : mode === "login" ? "Sign in" : "Create account"}
        </button>
        <div className="muted" style={{ marginTop: 12, textAlign: "center" }}>
          {mode === "login" ? "No account?" : "Have an account?"}{" "}
          <a onClick={() => setMode(mode === "login" ? "register" : "login")} style={{ cursor: "pointer" }}>
            {mode === "login" ? "Register" : "Sign in"}
          </a>
        </div>
      </form>
    </div>
  );
}
