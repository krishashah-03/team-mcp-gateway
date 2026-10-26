import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, onUnauthorized, tokenStore, type User } from "./lib/api";

interface AuthState {
  user: User | null;
  /** True while the stored session is being checked on first load. */
  loading: boolean;
  /** Set when a signed-in session ended unexpectedly. */
  notice: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(() => tokenStore.get() !== null);
  const [notice, setNotice] = useState<string | null>(null);

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  useEffect(() => {
    onUnauthorized(() => {
      tokenStore.clear();
      setUser(null);
      setNotice("Your session ended. Please sign in again.");
    });
    return () => onUnauthorized(null);
  }, []);

  useEffect(() => {
    if (tokenStore.get() === null) return;
    let cancelled = false;
    api
      .me()
      .then((me) => !cancelled && setUser(me))
      .catch(() => !cancelled && tokenStore.clear())
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.login(email, password);
    tokenStore.set(result.access_token);
    setNotice(null);
    setUser(result.user);
  }, []);

  const value = useMemo(() => ({ user, loading, notice, login, logout }), [user, loading, notice, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
