import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authApi } from '../../shared/api/endpoints';
import { ApiClientError, setUnauthorizedHandler } from '../../shared/api/client';
import {
  clearTokenPair,
  getCachedUserJson,
  getTokenPair,
  saveCachedUserJson,
  saveTokenPair,
} from '../../shared/storage/secureTokens';
import { clearAllCache } from '../../shared/storage/localDb';
import type { UserProfile } from '../../shared/types/domain';
import { getUserCapabilities, type RoleCapabilities } from '../../navigation/roleConfig';

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated';

export interface AuthContextValue {
  status: AuthStatus;
  user: UserProfile | null;
  capabilities: RoleCapabilities;
  isOfflineSession: boolean;
  pendingDeepLink: string | null;
  setPendingDeepLink: (path: string | null) => void;
  consumePendingDeepLink: () => string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<UserProfile | null>(null);
  const [isOfflineSession, setIsOfflineSession] = useState(false);
  const [pendingDeepLink, setPendingDeepLink] = useState<string | null>(null);

  const handleForcedLogout = useCallback(() => {
    clearAllCache().catch(() => undefined);
    setUser(null);
    setIsOfflineSession(false);
    setStatus('unauthenticated');
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(handleForcedLogout);
    return () => setUnauthorizedHandler(null);
  }, [handleForcedLogout]);

  const hydrateSession = useCallback(async () => {
    setStatus('loading');
    try {
      const { accessToken, refreshToken } = await getTokenPair();
      if (!accessToken && !refreshToken) {
        setUser(null);
        setStatus('unauthenticated');
        return;
      }

      try {
        const profile = await authApi.me();
        await saveCachedUserJson(JSON.stringify(profile));
        setUser(profile);
        setIsOfflineSession(false);
        setStatus('authenticated');
      } catch (err) {
        // Jika gagal karena jaringan (offline), izinkan masuk mode offline terbatas memakai profil ter-cache
        if (err instanceof ApiClientError && err.isNetworkError) {
          const cachedJson = await getCachedUserJson();
          if (cachedJson) {
            const cachedProfile = JSON.parse(cachedJson) as UserProfile;
            setUser(cachedProfile);
            setIsOfflineSession(true);
            setStatus('authenticated');
            return;
          }
        }
        await clearTokenPair();
        setUser(null);
        setIsOfflineSession(false);
        setStatus('unauthenticated');
      }
    } catch {
      setUser(null);
      setStatus('unauthenticated');
    }
  }, []);

  useEffect(() => {
    hydrateSession();
  }, [hydrateSession]);

  const login = useCallback(async (username: string, password: string) => {
    const res = await authApi.login(username.trim(), password);
    await saveTokenPair(res.access_token, res.refresh_token);
    await saveCachedUserJson(JSON.stringify(res.user));
    setUser(res.user);
    setIsOfflineSession(false);
    setStatus('authenticated');
  }, []);

  const logout = useCallback(async () => {
    try {
      const { refreshToken } = await getTokenPair();
      await authApi.logout(refreshToken);
    } catch {
      // Best effort: tetap bersihkan sesi lokal walau offline
    }
    await clearTokenPair();
    try {
      await clearAllCache();
    } catch {
      // Abaikan jika SQLite belum diinisialisasi
    }
    setUser(null);
    setIsOfflineSession(false);
    setPendingDeepLink(null);
    setStatus('unauthenticated');
  }, []);

  const refreshProfile = useCallback(async () => {
    try {
      const profile = await authApi.me();
      await saveCachedUserJson(JSON.stringify(profile));
      setUser(profile);
      setIsOfflineSession(false);
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 401) {
        await clearTokenPair();
        handleForcedLogout();
      }
    }
  }, [handleForcedLogout]);

  const consumePendingDeepLink = useCallback(() => {
    const current = pendingDeepLink;
    if (current) {
      setPendingDeepLink(null);
    }
    return current;
  }, [pendingDeepLink]);

  const capabilities = useMemo(() => getUserCapabilities(user), [user]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      capabilities,
      isOfflineSession,
      pendingDeepLink,
      setPendingDeepLink,
      consumePendingDeepLink,
      login,
      logout,
      refreshProfile,
    }),
    [
      status,
      user,
      capabilities,
      isOfflineSession,
      pendingDeepLink,
      consumePendingDeepLink,
      login,
      logout,
      refreshProfile,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth harus dipanggil di dalam AuthProvider.');
  }
  return ctx;
}
