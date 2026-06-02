// Thin API client for the Verifyr backend. Token stored in localStorage.

const BASE: string = (import.meta as any).env?.VITE_API_BASE ?? "";

let token: string | null = localStorage.getItem("verifyr_token");

export function getToken() {
  return token;
}
export function setToken(t: string | null) {
  token = t;
  if (t) localStorage.setItem("verifyr_token", t);
  else localStorage.removeItem("verifyr_token");
}

async function req(path: string, opts: RequestInit = {}): Promise<any> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((opts.headers as Record<string, string>) || {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(BASE + path, { ...opts, headers });
  if (res.status === 401) {
    setToken(null);
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  register: (email: string, password: string) =>
    req("/api/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: async (email: string, password: string) => {
    const body = new URLSearchParams({ username: email, password });
    const res = await fetch(BASE + "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!res.ok) throw new Error("Incorrect email or password");
    return res.json();
  },
  me: () => req("/api/auth/me"),
  listChecks: () => req("/api/checks"),
  getCheck: (id: number) => req(`/api/checks/${id}`),
  createCheck: (c: any) => req("/api/checks", { method: "POST", body: JSON.stringify(c) }),
  updateCheck: (id: number, c: any) => req(`/api/checks/${id}`, { method: "PUT", body: JSON.stringify(c) }),
  deleteCheck: (id: number) => req(`/api/checks/${id}`, { method: "DELETE" }),
  runCheck: (id: number) => req(`/api/checks/${id}/run`, { method: "POST" }),
  listRuns: (checkId?: number) => req("/api/runs" + (checkId ? `?check_id=${checkId}` : "")),
  getRun: (id: number) => req(`/api/runs/${id}`),
  // APK + quick (ad-hoc) test
  uploadApk: async (file: File, onProgress?: (pct: number) => void) => {
    // XHR so we can report upload progress for large APKs.
    return new Promise<any>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", BASE + "/api/apks");
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
        else {
          let d = xhr.statusText;
          try { d = JSON.parse(xhr.responseText).detail; } catch {}
          reject(new Error(d));
        }
      };
      xhr.onerror = () => reject(new Error("upload failed"));
      const fd = new FormData();
      fd.append("file", file);
      xhr.send(fd);
    });
  },
  listApks: () => req("/api/apks"),
  quickRun: (payload: any) => req("/api/runs/quick", { method: "POST", body: JSON.stringify(payload) }),
  listQuickRuns: () => req("/api/runs"),
};

export function streamUrl(runId: number): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const httpBase = BASE || `${location.protocol}//${location.host}`;
  const wsBase = httpBase.replace(/^http/, proto === "wss" ? "wss" : "ws");
  return `${wsBase}/api/runs/${runId}/stream?token=${encodeURIComponent(token || "")}`;
}

export function artifact(url: string | null): string | null {
  if (!url) return null;
  return BASE + url;
}
