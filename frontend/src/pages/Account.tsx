import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { can, useMe } from "../lib/auth";
import { Button, Field, Flash, Notice, Page, inputCls } from "../lib/ui";
import { RecoveryCodes } from "./Login";

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
  const [mfaPw, setMfaPw] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [newCodes, setNewCodes] = useState<string[] | null>(null);
  const [codesLeft, setCodesLeft] = useState(me?.recovery_codes_left ?? 0);
  const [mfaOn, setMfaOn] = useState(!!me?.mfa_enabled);
  const [enrollPw, setEnrollPw] = useState("");
  const [enrollInfo, setEnrollInfo] = useState<{ secret: string; qr: string } | null>(null);
  const [enrollCode, setEnrollCode] = useState("");
  const proof = () => ({ current_password: mfaPw, mfa_code: mfaCode });
  const startEnroll = async (e: React.FormEvent) => {
    e.preventDefault();
    try { setEnrollInfo(await api.post<{ secret: string; qr: string }>("/api/auth/mfa/enroll", { current_password: enrollPw })); setEnrollPw(""); setMsg(null); }
    catch (err) { fail(err); }
  };
  const confirmEnroll = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const r = await api.post<{ recovery_codes: string[] }>("/api/auth/mfa/enroll/confirm", { mfa_code: enrollCode });
      setEnrollInfo(null); setEnrollCode(""); setMfaOn(true); setNewCodes(r.recovery_codes); setCodesLeft(r.recovery_codes.length);
      setMsg({ kind: "ok", text: "Two-step login is on. Your other devices are logged out." });
    } catch (err) { setEnrollCode(""); fail(err); }
  };
  const turnOff = async () => {
    if (!confirm("Turn off two-step login? Your password alone will open this login.")) return;
    try {
      await api.post("/api/auth/mfa/disable", proof());
      setMfaOn(false); setMfaPw(""); setMfaCode("");
      setMsg({ kind: "ok", text: "Two-step login is off." });
    } catch (err) { setMfaCode(""); fail(err); }
  };
  const makeCodes = async () => {
    try {
      const r = await api.post<{ recovery_codes: string[] }>("/api/auth/mfa/recovery-codes", proof());
      setNewCodes(r.recovery_codes); setCodesLeft(r.recovery_codes.length); setMfaPw(""); setMfaCode("");
    } catch (err) { setMfaCode(""); fail(err); }
  };
  const movePhone = async () => {
    if (!confirm(me?.mfa_required ? "Remove your authenticator? You are logged out everywhere and set up the new one at your next login."
      : "Remove your authenticator? You are logged out everywhere. Turn two-step login on again under My account to use the new phone.")) return;
    try { await api.post("/api/auth/mfa/reset", proof()); window.location.assign("/"); } catch (err) { setMfaCode(""); fail(err); }
  };
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
      {!viaPin && !mfaOn && <div className="card p-5 w-full max-w-sm flex flex-col gap-3 mb-6">
        <h2 className="font-semibold">Two-step login</h2>
        {!enrollInfo ? <form onSubmit={startEnroll} className="flex flex-col gap-3">
          <p className="text-sm text-muted">Off. Turn it on to ask for a code from an authenticator app, such as Google Authenticator, 1Password or Authy, each time you log in with your password.</p>
          <Field label="Your password"><input className={inputCls} type="password" autoComplete="current-password" value={enrollPw} onChange={(e) => setEnrollPw(e.target.value)} /></Field>
          <div><Button type="submit" disabled={!enrollPw}>Turn on</Button></div>
        </form> : <form onSubmit={confirmEnroll} className="flex flex-col gap-3">
          <p className="text-sm text-muted">Scan this with your authenticator app, then type the code it shows.</p>
          <img src={enrollInfo.qr} alt="QR code for your authenticator app" className="w-48 h-48 rounded-control self-center" />
          <details className="text-sm text-muted"><summary className="cursor-pointer">Can’t scan it?</summary>
            <p className="mt-1">Type this key into the app: <span className="mono break-all text-ink">{enrollInfo.secret}</span></p></details>
          <Field label="Code from the app"><input className={`${inputCls} mono`} autoFocus autoComplete="one-time-code" value={enrollCode} onChange={(e) => setEnrollCode(e.target.value)} /></Field>
          <div className="flex gap-2">
            <Button type="submit" disabled={!enrollCode.trim()}>Turn on</Button>
            <Button type="button" kind="quiet" onClick={() => { setEnrollInfo(null); setEnrollCode(""); }}>Cancel</Button>
          </div>
        </form>}
      </div>}
      {!viaPin && mfaOn && <div className="card p-5 w-full max-w-sm flex flex-col gap-3 mb-6">
        <h2 className="font-semibold">Two-step login</h2>
        {newCodes ? <RecoveryCodes codes={newCodes} onDone={() => setNewCodes(null)} /> : <>
          <p className="text-sm text-muted">On. You have {codesLeft} recovery code{codesLeft === 1 ? "" : "s"} left. New codes replace the old ones.</p>
          <Field label="Your password"><input className={inputCls} type="password" autoComplete="current-password" value={mfaPw} onChange={(e) => setMfaPw(e.target.value)} /></Field>
          <Field label="Code from your app, or a recovery code"><input className={`${inputCls} mono`} autoComplete="one-time-code" value={mfaCode} onChange={(e) => setMfaCode(e.target.value)} /></Field>
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={!mfaPw || !mfaCode.trim()} onClick={makeCodes}>New recovery codes</Button>
            <Button type="button" kind="quiet" disabled={!mfaPw || !mfaCode.trim()} onClick={movePhone}>Move to a new phone</Button>
            {!me?.mfa_required && <Button type="button" kind="quiet" disabled={!mfaPw || !mfaCode.trim()} onClick={turnOff}>Turn off</Button>}
          </div>
        </>}
      </div>}
      {!viaPin && me && !me.email && <Notice>No email on this login, so you can’t reset your password yourself. {me.is_admin ? "Add one under Setup → Users." : "Ask an admin to add one."}</Notice>}
      {me && <p className="text-sm text-muted">{me.is_platform_admin ? "You are a platform admin." : me.is_admin ? "You are an admin." : `You can open: ${Object.keys(me.permissions).length ? Object.entries(me.permissions).map(([k, v]) => `${k.replace("_", " ")} (${v})`).join(", ") : "the overview only"}.`}
        {can(me, "audit") && <> <Link className="underline" to={`/audit?user=${me.id}`}>See my activity</Link>.</>}</p>}
    </Page>
  );
}
