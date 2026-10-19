"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import type { SystemState, User } from "./types";

interface AuthCtx {
  user: User | null;
  system: SystemState | null;
  loading: boolean;
  can: (perm: string) => boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refreshSystem: () => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [system, setSystem] = useState<SystemState | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshSystem = useCallback(async () => {
    try {
      setSystem(await api.get<SystemState>("/api/system"));
    } catch {
      /* shown by pages */
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api
      .get<User>("/api/auth/me")
      .then((u) => {
        setUser(u);
        return refreshSystem();
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, [refreshSystem]);

  const login = useCallback(
    async (username: string, password: string) => {
      const res = await api.post<{ token: string; user: User }>("/api/auth/login", { username, password });
      setToken(res.token);
      setUser(await api.get<User>("/api/auth/me"));
      await refreshSystem();
    },
    [refreshSystem],
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    window.location.href = "/login";
  }, []);

  const can = useCallback((perm: string) => !!user?.permissions?.includes(perm), [user]);

  return <Ctx.Provider value={{ user, system, loading, can, login, logout, refreshSystem }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}
