import { useState } from "react";
import { supabase } from "../supabase";

export default function Login({ expired = false }: { expired?: boolean }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setInfo("");
    setBusy(true);
    try {
      if (mode === "login") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
        // On success, the AuthProvider's onAuthStateChange swaps in the dashboard.
      } else {
        const { data, error } = await supabase.auth.signUp({ email, password });
        if (error) throw error;
        // If email confirmation is enabled, there's no active session yet.
        if (!data.session) {
          setInfo("Account created. Check your email to confirm, then sign in.");
          setMode("login");
        }
      }
    } catch (err: any) {
      setError(err?.message || "Something went wrong");
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
        {expired && !error && (
          <div className="error" style={{ marginBottom: 12 }}>
            Your session expired. Please sign in again.
          </div>
        )}
        <label>Email</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <label>Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={6} />
        {error && <div className="error">{error}</div>}
        {info && <div className="muted" style={{ marginTop: 8 }}>{info}</div>}
        <button className="primary" style={{ width: "100%", marginTop: 16 }} disabled={busy}>
          {busy ? "..." : mode === "login" ? "Sign in" : "Create account"}
        </button>
        <div className="muted" style={{ marginTop: 12, textAlign: "center" }}>
          {mode === "login" ? "No account?" : "Have an account?"}{" "}
          <a onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); setInfo(""); }} style={{ cursor: "pointer" }}>
            {mode === "login" ? "Register" : "Sign in"}
          </a>
        </div>
      </form>
    </div>
  );
}
