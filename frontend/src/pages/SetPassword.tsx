import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Button, Field, inputCls } from "../lib/ui";
import { AuthShell } from "./Login";

type Link = { kind: "invite" | "reset"; username: string; display_name: string };

/** Opened from an invite or reset email: /set-password#token. The token is after the # so it never reaches the server's logs. */
export default function SetPassword() {
  const [token] = useState(() => window.location.hash.slice(1));
  const [link, setLink] = useState<Link | null>(null);
  const [dead, setDead] = useState(!token);
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) return;
    window.history.replaceState(null, "", window.location.pathname);  // out of the address bar and history
    api.post<Link>("/api/auth/link", { token }).then((l) => { setLink(l); setName(l.display_name); }).catch(() => setDead(true));
  }, [token]);

  const invite = link?.kind === "invite";
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post("/api/auth/set-password", { token, password: pw, ...(invite ? { display_name: name.trim() } : {}) });
      setDone(true);
    } catch (x) { setErr((x as Error).message); }
  };

  const toLogin = <a className="text-sm underline self-start" href="/">Go to login</a>;
  if (dead) return <AuthShell><p className="text-sm">This link has expired or was already used. Ask an admin for a new invite, or use “Forgot your password?” on the login page.</p>{toLogin}</AuthShell>;
  if (done) return <AuthShell><p className="text-sm">{invite ? "You’re all set." : "Your password is changed."} Log in as <span className="mono">{link?.username}</span>.</p>{toLogin}</AuthShell>;
  if (!link) return null;
  return (
    <AuthShell>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <h2 className="font-semibold">{invite ? "Finish setting up your login" : "Choose a new password"}</h2>
        <p className="text-sm text-muted">Username: <span className="mono text-ink">{link.username}</span></p>
        {invite && <Field label="Your name"><input className={inputCls} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} /></Field>}
        <Field label="Password" hint="at least 8 characters, not a common password, not your name"><input className={inputCls} type="password" autoComplete="new-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
        <Field label="Password again"><input className={inputCls} type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} /></Field>
        {again && pw !== again && <div className="text-danger text-sm">The two passwords differ.</div>}
        {err && <div className="text-danger text-sm">{err}</div>}
        <Button type="submit" disabled={pw.length < 8 || pw !== again || (invite && !name.trim())}>{invite ? "Finish" : "Change password"}</Button>
      </form>
    </AuthShell>
  );
}
