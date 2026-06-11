// Supabase client — single source of auth for the app. Configure via env:
//   VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY  (see .env.example)
import { createClient } from "@supabase/supabase-js";

const url = (import.meta as any).env?.VITE_SUPABASE_URL as string | undefined;
const anonKey = (import.meta as any).env?.VITE_SUPABASE_ANON_KEY as string | undefined;

if (!url || !anonKey) {
  // Surface misconfiguration early rather than failing with an opaque network error.
  console.error("Missing VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY — auth will not work.");
}

export const supabase = createClient(url ?? "", anonKey ?? "", {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
  },
});
