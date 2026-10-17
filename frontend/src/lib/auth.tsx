import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Navigate, useLocation } from "react-router-dom";

import { api, configureApi } from "@/lib/api";
import type { Role, TokenResponse, User } from "@/lib/types";

export const STAFF_ROLES: Role[] = ["agent", "supervisor", "admin"];
export const DECIDER_ROLES: Role[] = ["supervisor", "admin"];

/** Renew a little before the access token expires, never more often than this. */
const MIN_RENEW_MS = 10_000;
const RENEW_AT = 0.8;

type AuthStatus = "loading" | "anonymous" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  user: User | null;
  token: string | null;
  login: (email: string, password: string) => Promise<User>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function homePath(role: Role): string {
  return role === "customer" ? "/chat" : "/console";
}

/**
 * Sessions: the short-lived access token stays in memory only (never in storage scripts can
 * read). An HttpOnly refresh cookie restores the session on reload and renews the token.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const tokenRef = useRef<string | null>(null);
  const renewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = useCallback(() => {
    if (renewTimer.current) clearTimeout(renewTimer.current);
    tokenRef.current = null;
    setToken(null);
    setUser(null);
    setStatus("anonymous");
    queryClient.clear();
  }, [queryClient]);

  // renew and apply need each other (apply schedules the next renew); a ref breaks the cycle.
  const applyRef = useRef<(session: TokenResponse) => string>(() => "");

  const renew = useCallback(async (): Promise<string | null> => {
    try {
      return applyRef.current(await api.refresh());
    } catch {
      return null;
    }
  }, []);

  const apply = useCallback(
    (session: TokenResponse): string => {
      tokenRef.current = session.access_token;
      setToken(session.access_token);
      setUser(session.user);
      setStatus("authenticated");
      if (renewTimer.current) clearTimeout(renewTimer.current);
      renewTimer.current = setTimeout(
        () => void renew(),
        Math.max(MIN_RENEW_MS, session.expires_in * 1000 * RENEW_AT),
      );
      return session.access_token;
    },
    [renew],
  );
  applyRef.current = apply;

  useEffect(() => {
    configureApi({ getToken: () => tokenRef.current, refresh: renew, onUnauthorized: clear });
  }, [renew, clear]);

  // On load, try to restore the session from the refresh cookie.
  useEffect(() => {
    let cancelled = false;
    api
      .refresh()
      .then((session) => {
        if (!cancelled) apply(session);
      })
      .catch(() => {
        if (!cancelled) setStatus("anonymous");
      });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  useEffect(() => () => {
    if (renewTimer.current) clearTimeout(renewTimer.current);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const session = await api.login(email, password);
      apply(session);
      return session.user;
    },
    [apply],
  );

  const logout = useCallback(() => {
    void api.logout().catch(() => undefined);
    clear();
  }, [clear]);

  const value = useMemo(
    () => ({ status, user, token, login, logout }),
    [status, user, token, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}

/** Render children only for signed-in users with one of `roles`; redirect everyone else. */
export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { status, user } = useAuth();
  const location = useLocation();
  if (status === "loading") {
    return (
      <div className="grid h-full place-items-center text-subtle" role="status">
        Loading…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (!roles.includes(user.role)) return <Navigate to={homePath(user.role)} replace />;
  return <>{children}</>;
}
