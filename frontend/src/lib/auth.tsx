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
import type { Role, User } from "@/lib/types";
import { safeStorage } from "@/lib/utils";

const TOKEN_KEY = "supportpilot.token";

export const STAFF_ROLES: Role[] = ["agent", "supervisor", "admin"];
export const DECIDER_ROLES: Role[] = ["supervisor", "admin"];

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

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [token, setToken] = useState<string | null>(() => safeStorage.get(TOKEN_KEY));
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>(token ? "loading" : "anonymous");
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const logout = useCallback(() => {
    safeStorage.set(TOKEN_KEY, null);
    setToken(null);
    setUser(null);
    setStatus("anonymous");
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    configureApi({ getToken: () => tokenRef.current, onUnauthorized: logout });
  }, [logout]);

  // Restore a saved session: the token is only trusted once /auth/me accepts it.
  useEffect(() => {
    if (!token || user) return;
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (cancelled) return;
        setUser(me);
        setStatus("authenticated");
      })
      .catch(() => {
        if (!cancelled) logout();
      });
    return () => {
      cancelled = true;
    };
  }, [token, user, logout]);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.login(email, password);
    safeStorage.set(TOKEN_KEY, result.access_token);
    tokenRef.current = result.access_token;
    setToken(result.access_token);
    setUser(result.user);
    setStatus("authenticated");
    return result.user;
  }, []);

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
