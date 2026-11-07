import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { can, useMe } from "../lib/auth";
import { Button, Field, Flash, Notice, Page, inputCls } from "../lib/ui";

export default function Account() {
  const me = useMe();
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [pinPw, setPinPw] = useState("");
  const [pin, setPin] = useState("");
  const [hasPin, setHasPin] = useState(!!me?.has_pin);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const fail = (err: unknown) => setMsg({ kind: "error", text: (err as Error).message });
  const viaPin = me?.session_mode === "pin";
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post("/api/auth/password", { current_password: cur, new_password: pw });
      setCur(""); setPw(""); setAgain("");
      setMsg({ kind: "ok", text: "Password changed. Your other devices are logged out." });
    } catch (err) { fail(err); }
  };
  const savePin = async (value: string | null) => {
    try {
      const r = await api.post<{ has_pin: boolean }>("/api/auth/pin", { current_password: pinPw, pin: value });
      setHasPin(r.has_pin); setPinPw(""); setPin("");
      setMsg({ kind: "ok", text: value ? "PIN saved." : "PIN removed." });
    } catch (err) { fail(err); }
  };
  return (
    <Page title="My account" sub={me ? `${me.display_name} · ${me.username}` : undefined}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {viaPin && <Notice>You logged in with a PIN. Log in with your password to change your password or PIN.</Notice>}
      {!viaPin && <div className="flex flex-wrap gap-6 mb-6 items-start">
        <form onSubmit={submit} className="card p-5 w-full max-w-sm flex flex-col gap-3">
          <h2 className="font-semibold">Change password</h2>
          <Field label="Current password"><input className={inputCls} type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
          <Field label="New password" hint="at least 8 characters, not a common password, not your name"><input className={inputCls} type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
          <Field label="New password again"><input className={inputCls} type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} /></Field>
          {again && pw !== again && <div className="text-danger text-sm">The two new passwords differ.</div>}
          <div><Button type="submit" disabled={!cur || pw.length < 8 || pw !== again}>Change password</Button></div>
        </form>
        <form onSubmit={(e) => { e.preventDefault(); savePin(pin); }} className="card p-5 w-full max-w-sm flex flex-col gap-3">
          <h2 className="font-semibold">Quick PIN</h2>
          <p className="text-sm text-muted">A short login for the shared bar PC. It only opens counts and deliveries. {hasPin ? "You have one set." : "You have none set."}</p>
          <Field label="Your password"><input className={inputCls} type="password" autoComplete="current-password" value={pinPw} onChange={(e) => setPinPw(e.target.value)} /></Field>
          <Field label="New PIN" hint="4 to 8 digits, not repeated or in sequence"><input className={`${inputCls} mono`} type="password" inputMode="numeric" autoComplete="off" value={pin} onChange={(e) => setPin(e.target.value)} /></Field>
          <div className="flex gap-2">
            <Button type="submit" disabled={!pinPw || pin.length < 4}>Save PIN</Button>
            {hasPin && <Button type="button" kind="quiet" disabled={!pinPw} onClick={() => savePin(null)}>Remove PIN</Button>}
          </div>
        </form>
      </div>}
      {me && <p className="text-sm text-muted">{me.is_admin ? "You are an admin." : `You can open: ${Object.keys(me.permissions).length ? Object.entries(me.permissions).map(([k, v]) => `${k.replace("_", " ")} (${v})`).join(", ") : "the overview only"}.`}
        {can(me, "audit") && <> <Link className="underline" to={`/audit?user=${me.id}`}>See my activity</Link>.</>}</p>}
    </Page>
  );
}
