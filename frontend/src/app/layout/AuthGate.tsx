import { type PropsWithChildren, useCallback, useEffect, useState } from "react";
import { LoginScreen } from "@/features/auth/LoginScreen";
import {
  clearSession,
  getStoredToken,
  getStoredUsername,
  login as apiLogin,
  resetPassword as apiResetPassword,
  storeSession
} from "@/shared/api";

const TOKEN_EVENT = "bid-vector:session-change";

export function dispatchSessionChange() {
  window.dispatchEvent(new Event(TOKEN_EVENT));
}

export interface AuthSession {
  token: string;
  username: string | null;
}

export interface AuthContextValue {
  session: AuthSession | null;
  logout: () => void;
}

export function useUnauthorizedHandler(): () => void {
  return useCallback(() => {
    clearSession();
    dispatchSessionChange();
  }, []);
}

export function AuthGate({ children }: PropsWithChildren) {
  const [session, setSession] = useState<AuthSession | null>(() => readSession());

  useEffect(() => {
    const sync = () => setSession(readSession());
    window.addEventListener(TOKEN_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(TOKEN_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const handleLogin = useCallback(async (username: string, password: string) => {
    const response = await apiLogin(username, password);
    storeSession(response);
    dispatchSessionChange();
    setSession({ token: response.access_token, username: response.username ?? username });
  }, []);

  const handlePasswordReset = useCallback(
    async (username: string, resetToken: string, newPassword: string) => {
      const response = await apiResetPassword(username, resetToken, newPassword);
      storeSession(response);
      dispatchSessionChange();
      setSession({ token: response.access_token, username: response.username ?? username });
    },
    []
  );

  if (!session) {
    return <LoginScreen onLogin={handleLogin} onPasswordReset={handlePasswordReset} />;
  }

  return <>{children}</>;
}

export function logoutSession(): void {
  clearSession();
  dispatchSessionChange();
}

function readSession(): AuthSession | null {
  const token = getStoredToken();
  if (!token) return null;
  return { token, username: getStoredUsername() };
}

export function useAuthSession(): AuthSession | null {
  const [session, setSession] = useState<AuthSession | null>(() => readSession());
  useEffect(() => {
    const sync = () => setSession(readSession());
    window.addEventListener(TOKEN_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(TOKEN_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return session;
}
