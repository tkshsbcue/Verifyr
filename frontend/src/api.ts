// Thin API client for the Verifyr backend. The bearer token is the Supabase
// access token, kept in sync by the AuthProvider via setAccessToken().

const BASE: string = (import.meta as any).env?.VITE_API_BASE ?? "";

let accessToken: string | null = null;

export function setAccessToken(t: string | null) {
  accessToken = t;
}
export function getAccessToken() {
  return accessToken;
}

async function req(path: string, opts: RequestInit = {}): Promise<any> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((opts.headers as Record<string, string>) || {}),
  };
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  const res = await fetch(BASE + path, { ...opts, headers });
  if (res.status === 401) {
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
      if (accessToken) xhr.setRequestHeader("Authorization", `Bearer ${accessToken}`);
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
  return `${wsBase}/api/runs/${runId}/stream?token=${encodeURIComponent(accessToken || "")}`;
}

// Screenshots are served by an ownership-checked proxy that takes the token as a
// query param (an <img> can't send an Authorization header).
export function artifact(url: string | null): string | null {
  if (!url) return null;
  const sep = url.includes("?") ? "&" : "?";
  const auth = accessToken ? `${sep}token=${encodeURIComponent(accessToken)}` : "";
  return BASE + url + auth;
}
