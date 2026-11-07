import { useState } from "react";
import { ApiError, api, type Me } from "../lib/api";
import { Button, inputCls } from "../lib/ui";

export default function Login({ onLogin, notice }: { onLogin: (me: Me) => void; notice?: string }) {
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [pin, setPin] = useState(false);
  const [err, setErr] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const r = await api.post<{ user: Me }>("/api/auth/login", { username: username.trim(), [pin ? "pin" : "password"]: secret });
      onLogin(r.user);
    } catch (x) {
      setSecret("");
      setErr(x instanceof ApiError && x.status === 429 ? x.message : `That username or ${pin ? "PIN" : "password"} is not right.`);
    }
  };
  return (
    <div className="min-h-screen grid place-items-center">
      <form onSubmit={submit} className="card p-7 w-80 max-w-[calc(100vw-32px)] flex flex-col gap-3">
        <div className="flex items-center gap-2 text-lg font-bold tracking-wide mb-1">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--color-accent)" strokeWidth="1.8" aria-hidden><path d="M9 2h6v5c2 1 3 3 3 5v9a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-9c0-2 1-4 3-5z" /></svg>
          Bar stock
        </div>
        {notice && <div className="text-sm text-muted">{notice}</div>}
        <input className={inputCls} placeholder="Username" aria-label="Username" autoComplete="username" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        <input className={inputCls} type="password" placeholder={pin ? "PIN" : "Password"} aria-label={pin ? "PIN" : "Password"} inputMode={pin ? "numeric" : undefined}
          autoComplete={pin ? "off" : "current-password"} value={secret} onChange={(e) => setSecret(e.target.value)} />
        {err && <div className="text-danger text-sm">{err}</div>}
        <Button type="submit" disabled={!username.trim() || !secret}>Log in</Button>
        <button type="button" className="text-xs text-muted hover:text-ink underline" onClick={() => { setPin((v) => !v); setSecret(""); setErr(""); }}>
          {pin ? "Use my password" : "Use a PIN (counts and deliveries only)"}
        </button>
      </form>
    </div>
  );
}
