import { type ReactNode, useEffect, useRef, useState } from "react";
import { ApiError, api, type Me } from "../lib/api";
import { Button, Field, inputCls, useAsync } from "../lib/ui";

type Reply = { ok: boolean; user?: Me; mfa?: "verify" | "setup"; mfa_token?: string; email_recovery?: boolean };
type Step = "login" | "forgot" | "forgot-sent" | "verify" | "setup";
const linkCls = "h-9 inline-flex items-center text-sm text-muted hover:text-ink underline self-start";
const message = (x: unknown) => (x as Error).message;

/** The logo and narrow column shared by everything shown before login. */
export function AuthShell({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="min-h-dvh grid place-items-center px-4 py-8">
      <div className={`w-full ${wide ? "max-w-md" : "max-w-80"} flex flex-col gap-3`}>
        <div className="flex items-center gap-2 text-2xl font-semibold mb-4">
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="var(--color-accent)" strokeWidth="1.6" aria-hidden><path d="M9 2h6v5c2 1 3 3 3 5v9a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-9c0-2 1-4 3-5z" /></svg>
          Back of house
        </div>
        {children}
      </div>
    </div>
  );
}

/** Shown once, right after they're made. */
export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(codes.join("\n")); setCopied(true); } catch { setCopied(false); } };
  return (
    <div className="flex flex-col gap-3">
      <h2 className="font-semibold">Save your recovery codes</h2>
      <p className="text-sm text-muted">Each one logs you in once if you lose your phone. They won’t be shown again, so keep them somewhere safe, away from your phone.</p>
      <ul className="mono text-sm grid grid-cols-2 gap-x-6 gap-y-1 card p-4">{codes.map((c) => <li key={c}>{c}</li>)}</ul>
      <div className="flex gap-2">
        <Button type="button" kind="quiet" onClick={copy}>{copied ? "Copied" : "Copy codes"}</Button>
        <Button type="button" onClick={onDone}>I’ve saved them</Button>
      </div>
    </div>
  );
}

function CodeField({ value, onChange, label, hint }: { value: string; onChange: (v: string) => void; label: string; hint?: string }) {
  return <Field label={label} hint={hint}><input className={`${inputCls} mono`} autoFocus autoComplete="one-time-code" autoCapitalize="characters" value={value} onChange={(e) => onChange(e.target.value)} /></Field>;
}

function Verify({ pending, emailRecovery, onLogin, onSetup, onBack }: { pending: string; emailRecovery: boolean; onLogin: (me: Me) => void; onSetup: (token: string) => void; onBack: () => void }) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [lost, setLost] = useState(false);
  const [emailed, setEmailed] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const r = await api.post<Reply>("/api/auth/mfa/verify", { mfa_token: pending, mfa_code: code });
      if (r.mfa === "setup" && r.mfa_token) onSetup(r.mfa_token); else if (r.user) onLogin(r.user);
    } catch (x) { setCode(""); setErr(message(x)); }
  };
  const sendEmail = async () => {
    try { await api.post("/api/auth/mfa/email", { mfa_token: pending }); setEmailed(true); setErr(""); } catch (x) { setErr(message(x)); }
  };
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <CodeField value={code} onChange={setCode} label={emailed ? "Code from your email" : lost ? "Recovery code" : "Code from your authenticator app"} />
      {emailed && <div className="text-sm text-muted">Check your email. After this code you’ll set up your authenticator again.</div>}
      {err && <div className="text-danger text-sm">{err}</div>}
      <Button type="submit" disabled={!code.trim()}>Continue</Button>
      {!lost && <button type="button" className={linkCls} onClick={() => setLost(true)}>I lost my phone</button>}
      {lost && !emailed && emailRecovery && <button type="button" className={linkCls} onClick={sendEmail}>Email me a code instead</button>}
      {lost && <div className="text-sm text-muted">No codes left? Another admin can reset this under Setup → Users.</div>}
      <button type="button" className={linkCls} onClick={onBack}>Back to login</button>
    </form>
  );
}

function Setup({ pending, onLogin, onBack }: { pending: string; onLogin: (me: Me) => void; onBack: () => void }) {
  const [info, setInfo] = useState<{ secret: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [done, setDone] = useState<{ user: Me; codes: string[] } | null>(null);
  const started = useRef(false);  // each call makes a new secret, so only ever one
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api.post<{ secret: string; qr: string }>("/api/auth/mfa/setup", { mfa_token: pending }).then(setInfo).catch((x) => setErr(message(x)));
  }, [pending]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const r = await api.post<{ user: Me; recovery_codes: string[] }>("/api/auth/mfa/confirm", { mfa_token: pending, mfa_code: code });
      setDone({ user: r.user, codes: r.recovery_codes });
    } catch (x) { setCode(""); setErr(message(x)); }
  };
  if (done) return <RecoveryCodes codes={done.codes} onDone={() => onLogin(done.user)} />;
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <h2 className="font-semibold">Set up two-step login</h2>
      <p className="text-sm text-muted">This login needs a code from an authenticator app, such as Google Authenticator, 1Password or Authy. Scan this with the app.</p>
      {info && <>
        <img src={info.qr} alt="QR code for your authenticator app" className="w-48 h-48 rounded-control self-center" />
        <details className="text-sm text-muted"><summary className="cursor-pointer">Can’t scan it?</summary>
          <p className="mt-1">Type this key into the app: <span className="mono break-all text-ink">{info.secret}</span></p></details>
        <CodeField value={code} onChange={setCode} label="Code from the app" />
      </>}
      {err && <div className="text-danger text-sm">{err}</div>}
      <Button type="submit" disabled={!code.trim()}>Turn on and continue</Button>
      <button type="button" className={linkCls} onClick={onBack}>Back to login</button>
    </form>
  );
}

export default function Login({ onLogin, notice }: { onLogin: (me: Me) => void; notice?: string }) {
  const options = useAsync(() => api.get<{ email_enabled: boolean }>("/api/auth/options"));
  const [step, setStep] = useState<Step>("login");
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [pin, setPin] = useState(false);
  const [err, setErr] = useState("");
  const [pending, setPending] = useState("");
  const [emailRecovery, setEmailRecovery] = useState(false);
  const back = () => { setStep("login"); setPending(""); setSecret(""); setErr(""); };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const r = await api.post<Reply>("/api/auth/login", { username: username.trim(), [pin ? "pin" : "password"]: secret });
      setSecret(""); setErr("");
      if (r.mfa && r.mfa_token) { setPending(r.mfa_token); setEmailRecovery(!!r.email_recovery); setStep(r.mfa); }
      else if (r.user) onLogin(r.user);
    } catch (x) {
      setSecret("");
      setErr(x instanceof ApiError && x.status === 429 ? x.message : `That username or ${pin ? "PIN" : "password"} is not right.`);
    }
  };
  const forgot = async (e: React.FormEvent) => {
    e.preventDefault();
    try { await api.post("/api/auth/forgot", { login: username.trim() }); setErr(""); setStep("forgot-sent"); } catch (x) { setErr(message(x)); }
  };

  if (step === "verify") return <AuthShell><Verify pending={pending} emailRecovery={emailRecovery} onLogin={onLogin} onBack={back} onSetup={(t) => { setPending(t); setStep("setup"); }} /></AuthShell>;
  if (step === "setup") return <AuthShell wide><Setup pending={pending} onLogin={onLogin} onBack={back} /></AuthShell>;
  if (step === "forgot-sent") return (
    <AuthShell>
      <p className="text-sm">If that login has an email address, a reset link is on its way. It works for 1 hour.</p>
      <button type="button" className={linkCls} onClick={back}>Back to login</button>
    </AuthShell>);
  if (step === "forgot") return (
    <AuthShell>
      <form onSubmit={forgot} className="flex flex-col gap-3">
        <p className="text-sm text-muted">We’ll email you a link to choose a new password.</p>
        <input className={inputCls} placeholder="Username or email" aria-label="Username or email" autoComplete="username" autoCapitalize="none" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
        {err && <div className="text-danger text-sm">{err}</div>}
        <Button type="submit" disabled={!username.trim()}>Send reset link</Button>
        <button type="button" className={linkCls} onClick={back}>Back to login</button>
      </form>
    </AuthShell>);
  return (
    <AuthShell>
      <form onSubmit={submit} className="flex flex-col gap-3">
        {notice && <div className="text-sm text-muted">{notice}</div>}
        <input className={inputCls} placeholder="Username" aria-label="Username" autoComplete="username" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} />
        <input className={inputCls} type="password" placeholder={pin ? "PIN" : "Password"} aria-label={pin ? "PIN" : "Password"} inputMode={pin ? "numeric" : undefined}
          autoComplete={pin ? "off" : "current-password"} value={secret} onChange={(e) => setSecret(e.target.value)} />
        {err && <div className="text-danger text-sm">{err}</div>}
        <Button type="submit" disabled={!username.trim() || !secret}>Log in</Button>
        <button type="button" className={linkCls} onClick={() => { setPin((v) => !v); setSecret(""); setErr(""); }}>
          {pin ? "Use my password" : "Use a PIN (counts and deliveries only)"}
        </button>
        {options.data?.email_enabled && <button type="button" className={linkCls} onClick={() => { setStep("forgot"); setErr(""); }}>Forgot your password?</button>}
      </form>
    </AuthShell>
  );
}
