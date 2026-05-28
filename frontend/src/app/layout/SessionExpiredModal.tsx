import { type FormEvent, useEffect, useState } from "react";
import { login as apiLogin, storeSession } from "@/shared/api";
import { Button, Card, CardContent, CardHeader, CardTitle, Input, toastApi } from "@/shared/components/ui";
import { dispatchSessionChange, useAuthSession } from "./AuthGate";

const SESSION_EXPIRED_EVENT = "bid-vector:session-expired";

export function notifySessionExpired() {
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}

/**
 * In-app re-login modal triggered by 401 errors. Unlike `logoutSession`, it
 * does NOT clear the token immediately — the user keeps their place in the
 * app, and on successful re-auth the modal dismisses without remounting the
 * Shell. The auth store is updated via `storeSession` + the standard
 * session-change event so all `useAuthSession` consumers re-render.
 */
export function SessionExpiredModal() {
  const session = useAuthSession();
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");

  useEffect(() => {
    const onExpired = () => setOpen(true);
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  // Escape key dismiss — without this the modal could only be closed by the
  // "나중에" button, leaving keyboard users stuck if a background fetch
  // re-fired the session-expired event after they last dismissed.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!open || !session?.username) return null;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const response = await apiLogin(session.username as string, password);
      storeSession(response);
      dispatchSessionChange();
      toastApi.success({ title: "다시 로그인 완료", description: "" });
      setOpen(false);
      setPassword("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "다시 로그인에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="session-expired-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(event) => {
        // Backdrop click (outside the Card) dismisses. Without this the rest
        // of the dashboard appears unclickable while the modal is up, which
        // users misread as "clicks are blocked".
        if (event.target === event.currentTarget) {
          setOpen(false);
        }
      }}
    >
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle id="session-expired-title">세션이 만료되었습니다</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <p className="text-xs text-[var(--color-muted)]">
              현재 화면 상태를 잃지 않으려면 다시 로그인하세요.
            </p>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">아이디</span>
              <Input value={session.username} disabled aria-label="아이디" />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">비밀번호</span>
              <Input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoFocus
                autoComplete="current-password"
                aria-label="비밀번호"
              />
            </label>
            {error ? (
              <p role="alert" className="text-xs text-[var(--color-danger)]">
                {error}
              </p>
            ) : null}
            <div className="flex items-center justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setOpen(false)}
                disabled={submitting}
              >
                나중에
              </Button>
              <Button type="submit" size="sm" disabled={submitting || !password}>
                {submitting ? "로그인 중" : "다시 로그인"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
