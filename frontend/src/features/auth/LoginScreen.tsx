import { type FormEvent, useState } from "react";

export interface LoginScreenProps {
  onLogin: (username: string, password: string) => Promise<void>;
  onPasswordReset: (username: string, resetToken: string, newPassword: string) => Promise<void>;
}

export function LoginScreen({ onLogin, onPasswordReset }: LoginScreenProps) {
  const [mode, setMode] = useState<"login" | "reset">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [resetToken, setResetToken] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      if (mode === "reset") {
        if (newPassword !== confirmPassword) {
          setError("새 비밀번호가 일치하지 않습니다.");
          return;
        }
        await onPasswordReset(username, resetToken, newPassword);
      } else {
        await onLogin(username, password);
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : mode === "reset"
            ? "비밀번호 초기화에 실패했습니다."
            : "로그인에 실패했습니다."
      );
    } finally {
      setLoading(false);
    }
  };

  const switchMode = () => {
    setMode((current) => (current === "login" ? "reset" : "login"));
    setError(null);
    setPassword("");
    setResetToken("");
    setNewPassword("");
    setConfirmPassword("");
  };

  const submitDisabled =
    loading ||
    !username ||
    (mode === "login" ? !password : !resetToken || !newPassword || !confirmPassword);

  return (
    <main className="login-screen">
      <form className="login-panel" onSubmit={submit}>
        <div className="login-heading">
          <span className="brand-mark">BV</span>
          <div>
            <h1>입찰 대시보드</h1>
            <p>{mode === "login" ? "운영자 로그인" : "비밀번호 초기화"}</p>
          </div>
        </div>
        <label>
          아이디
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
          />
        </label>
        {mode === "login" ? (
          <label>
            비밀번호
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
            />
          </label>
        ) : (
          <>
            <label>
              초기화 토큰
              <input
                value={resetToken}
                onChange={(event) => setResetToken(event.target.value)}
                type="password"
                autoComplete="one-time-code"
              />
            </label>
            <label>
              새 비밀번호
              <input
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                type="password"
                autoComplete="new-password"
              />
            </label>
            <label>
              새 비밀번호 확인
              <input
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                type="password"
                autoComplete="new-password"
              />
            </label>
          </>
        )}
        {error ? (
          <div className="inline-notice critical" role="alert">
            {error}
          </div>
        ) : null}
        <div className="login-actions">
          <button className="primary-button" type="submit" disabled={submitDisabled}>
            {loading ? "처리 중" : mode === "login" ? "로그인" : "비밀번호 초기화"}
          </button>
          <button className="secondary-button" type="button" onClick={switchMode} disabled={loading}>
            {mode === "login" ? "비밀번호 초기화" : "로그인으로 돌아가기"}
          </button>
        </div>
      </form>
    </main>
  );
}
