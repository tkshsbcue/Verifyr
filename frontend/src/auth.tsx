// Unified auth state. Wraps the app, tracks the Supabase session, and keeps the
// API client's bearer token in sync so the rest of the app stays declarative.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "./supabase";
import { setAccessToken, setSessionHandlers } from "./api";

type AuthState = {
  session: Session | null;
  email: string | null;
  loading: boolean;
  // Set when an API call found the session unrecoverable (refresh token gone),
  // so the sign-in screen can explain why the user was bounced.
  expired: boolean;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState>({
  session: null,
  email: null,
  loading: true,
  expired: false,
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    let active = true;

    // Let the API client recover from an expired access token (refresh + retry)
    // and tell us when the session is truly gone.
    setSessionHandlers({
      refresh: async () => {
        const { data } = await supabase.auth.getSession();
        const token = data.session?.access_token ?? null;
        setAccessToken(token);
        return token;
      },
      onExpired: () => {
        setExpired(true);
        supabase.auth.signOut();
      },
    });

    supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      setAccessToken(data.session?.access_token ?? null);
      setLoading(false);
    });

    // Fires on sign-in, sign-out, and silent token refreshes — the single place
    // the bearer token is updated.
    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
      setAccessToken(next?.access_token ?? null);
      if (next) setExpired(false); // a fresh session clears the expiry notice
    });

    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  const value: AuthState = {
    session,
    email: session?.user?.email ?? null,
    loading,
    expired,
    signOut: async () => {
      await supabase.auth.signOut();
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
