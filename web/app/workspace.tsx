"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, type Account, type Preferences, errorText, isSignedOut } from "./client-api";
import { DigestView, type Proposal } from "./digest-view";
import { PreferencesView } from "./preferences-view";
import { ResumeView } from "./resume-view";
import { Brand, Fault, Icon, Loading, PageHeader } from "./ui";

type Session = { kind: "opening" | "anonymous" } | { kind: "signed-in"; account: Account } | { kind: "failed"; error: string };
type View = "digest" | "preferences" | "resume" | "account" | "set-aside";
const navigation = [
  { view: "digest", text: "Digest", icon: "digest" },
  { view: "preferences", text: "Preferences", icon: "chat" },
  { view: "resume", text: "Resume", icon: "file" },
] as const;

function activeView(): View {
  const hash = window.location.hash.slice(1);
  if (hash === "preferences" || hash === "resume" || hash === "account" || hash === "set-aside") return hash;
  return "digest";
}

export function JobMatch() {
  const [session, setSession] = useState<Session>({ kind: "opening" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api.me(controller.signal).then((account) => {
      if (!controller.signal.aborted) setSession({ kind: "signed-in", account });
    }).catch((error) => {
      if (!controller.signal.aborted) setSession(isSignedOut(error) ? { kind: "anonymous" } : {
        kind: "failed", error: errorText(error, "Couldn’t open your workspace. Check your connection and try again."),
      });
    });
    return () => controller.abort();
  }, [attempt]);

  const end = useCallback((source: Account) => {
    setSession((current) => current.kind === "signed-in" && current.account === source ? { kind: "anonymous" } : current);
  }, []);

  function signIn(account: Account) {
    history.replaceState(history.state, "", "#digest");
    setSession({ kind: "signed-in", account });
  }

  if (session.kind === "signed-in") return <Workspace initial={session.account} onEnd={end}/>;
  if (session.kind === "anonymous") return <Credentials onSuccess={signIn}/>;
  return <div className="entry">
    <header className="entry-header"><Brand/></header>
    <main className="entry-card"><h1>Your Job Match workspace</h1>
      {session.kind === "failed" ? <Fault message={session.error} retry={() => { setSession({ kind: "opening" }); setAttempt((value) => value + 1); }}/> : <Loading label="Opening your workspace…"/>}
    </main>
  </div>;
}

function Credentials({ onSuccess }: { onSuccess: (account: Account) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const registering = mode === "register";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError("");
    try {
      onSuccess(await api.auth(mode, email.trim(), password));
    } catch (requestError) {
      setError(errorText(requestError, registering ? "Couldn’t create your account. Try again." : "Couldn’t sign in. Try again."));
      setSubmitting(false);
    }
  }

  return <div className="entry">
    <header className="entry-header"><Brand/></header>
    <main className="entry-card">
      <h1>{registering ? "Create your account" : "Sign in to Job Match"}</h1>
      <p className="muted">Your private workspace for finding roles and seeing why they fit.</p>
      <form className="credentials-form" onSubmit={submit}>
        <label htmlFor="email">Email</label>
        <input id="email" name="email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required disabled={submitting}/>
        <label htmlFor="password">Password</label>
        <input id="password" name="password" type="password" autoComplete={registering ? "new-password" : "current-password"} value={password} onChange={(event) => setPassword(event.target.value)} minLength={registering ? 8 : undefined} maxLength={1024} aria-describedby={registering ? "password-hint" : undefined} required disabled={submitting}/>
        {registering && <p id="password-hint" className="muted">Use at least 8 characters.</p>}
        {error && <Fault message={error}/>}
        <button type="submit" className="button primary" disabled={submitting}>{submitting ? registering ? "Creating account…" : "Signing in…" : registering ? "Create account" : "Sign in"}<Icon name="arrow"/></button>
      </form>
      <p className="entry-switch">{registering ? "Already have an account?" : "New to Job Match?"} <button type="button" className="text-action" disabled={submitting} onClick={() => { setMode(registering ? "login" : "register"); setPassword(""); setError(""); }}>{registering ? "Sign in" : "Create an account"}</button></p>
    </main>
  </div>;
}

function Workspace({ initial, onEnd }: { initial: Account; onEnd: (source: Account) => void }) {
  const [account, setAccount] = useState(initial);
  const [view, setView] = useState<View>("digest");
  const [resumeVisited, setResumeVisited] = useState(false);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const menu = useRef<HTMLDialogElement>(null);
  const main = useRef<HTMLElement>(null);

  useEffect(() => {
    function changeView() {
      const next = activeView();
      setView(next);
      if (next === "resume") setResumeVisited(true);
      menu.current?.close();
    }
    changeView();
    window.addEventListener("hashchange", changeView);
    return () => window.removeEventListener("hashchange", changeView);
  }, []);

  useEffect(() => {
    window.scrollTo(0, 0);
    main.current?.querySelector<HTMLElement>("[data-screen]:not([hidden]) h1")?.focus({ preventScroll: true });
  }, [view]);

  const endSession = useCallback(() => onEnd(initial), [initial, onEnd]);
  const fail = useCallback((error: unknown, fallback: string) => {
    if (isSignedOut(error)) {
      endSession();
      return "Your session has expired. Sign in again.";
    }
    return errorText(error, fallback);
  }, [endSession]);
  const savePreferences = (preferences: Preferences) => setAccount((current) => ({ ...current, preferences }));
  const saveResume = ({ profile, resume }: Account) => setAccount((current) => ({ ...current, profile, resume }));
  const reviewProposal = (next: Proposal) => {
    setProposal(next);
    if (activeView() === "digest") window.location.hash = "resume";
  };

  return <div className="workspace">
    <a href="#main-content" className="skip-link" onClick={(event) => { event.preventDefault(); main.current?.focus(); }}>Skip to content</a>
    <aside className="desktop-nav" aria-label="Workspace navigation">
      <a href="#digest" className="brand-link"><Brand/></a>
      <p className="workspace-label">Personal workspace</p>
      <WorkspaceNav view={view} email={account.email}/>
    </aside>
    <header className="mobile-bar">
      <a href="#digest" className="brand-link"><Brand/></a>
      <button className="button secondary mobile-menu" type="button" onClick={() => menu.current?.showModal()} aria-haspopup="dialog"><Icon name="menu"/> Menu</button>
    </header>
    <dialog className="mobile-dialog" ref={menu} aria-labelledby="menu-title">
      <div className="mobile-dialog-head"><h2 id="menu-title">Personal workspace</h2><button type="button" className="button secondary" onClick={() => menu.current?.close()}><Icon name="close"/> Close</button></div>
      <WorkspaceNav view={view} email={account.email} onNavigate={() => menu.current?.close()}/>
    </dialog>
    <main id="main-content" ref={main} tabIndex={-1} className="canvas">
      {(view === "digest" || view === "set-aside") && <div data-screen><DigestView account={account} exclusions={view === "set-aside"} fail={fail} onProposal={reviewProposal}/></div>}
      <div data-screen hidden={view !== "preferences"}><PreferencesView preferences={account.preferences} visible={view === "preferences"} save={savePreferences} fail={fail}/></div>
      {resumeVisited && <div data-screen hidden={view !== "resume"}><ResumeView account={account} proposal={proposal} clearProposal={() => setProposal(null)} save={saveResume} fail={fail}/></div>}
      {view === "account" && <div data-screen><AccountView email={account.email} onEnd={endSession} fail={fail}/></div>}
    </main>
  </div>;
}

function WorkspaceNav({ view, email, onNavigate }: { view: View; email: string; onNavigate?: () => void }) {
  return <nav className="workspace-nav" aria-label="Main navigation">
    <ul>{navigation.map((item) => <li key={item.view}><a href={`#${item.view}`} className="nav-link" data-active={view === item.view || (view === "set-aside" && item.view === "digest")} aria-current={view === item.view ? "page" : undefined} onClick={onNavigate}><Icon name={item.icon}/>{item.text}</a></li>)}</ul>
    <a href="#account" className="nav-link account-link" data-active={view === "account"} aria-current={view === "account" ? "page" : undefined} onClick={onNavigate}>
      <Icon name="account"/><span className="min-w-0"><span>Account</span><small title={email}>{email}</small></span>
    </a>
  </nav>;
}

function AccountView({ email, onEnd, fail }: { email: string; onEnd: () => void; fail: (error: unknown, fallback: string) => string }) {
  const confirm = useRef<HTMLDialogElement>(null);
  const [operation, setOperation] = useState<"logout" | "delete" | null>(null);
  const [logoutError, setLogoutError] = useState("");
  const [deleteError, setDeleteError] = useState("");

  async function signOut() {
    setOperation("logout");
    setLogoutError("");
    try {
      await api.logout();
      onEnd();
    } catch (error) {
      setLogoutError(fail(error, "Couldn’t sign out. Try again."));
      setOperation(null);
    }
  }

  async function removeAccount() {
    setOperation("delete");
    setDeleteError("");
    try {
      await api.deleteAccount();
      onEnd();
    } catch (error) {
      setDeleteError(fail(error, "Couldn’t delete your account. Try again."));
      setOperation(null);
    }
  }

  return <>
    <PageHeader title="Account"><p>Manage your session and the data saved to this workspace.</p></PageHeader>
    <div className="account-settings">
      <section className="app-grid setting-row" aria-labelledby="session-title">
        <div className="setting-label"><h2 id="session-title">Session</h2><p className="muted">Signing out keeps your resume and search preferences saved.</p></div>
        <div className="panel settings-panel"><div><p className="muted">Signed in as</p><strong className="email">{email}</strong></div>
          <button type="button" className="button secondary" onClick={signOut} disabled={!!operation}>{operation === "logout" ? "Signing out…" : "Sign out"}</button>
          {logoutError && <Fault message={logoutError}/>}
        </div>
      </section>
      <section className="app-grid setting-row" aria-labelledby="deletion-title">
        <div className="setting-label"><h2 id="deletion-title">Delete account and data</h2><p className="muted">This can’t be undone.</p></div>
        <div className="panel settings-panel"><p>Permanently delete your account, resume, saved preferences, digest and resume edits.</p>
          <button type="button" className="button danger" disabled={!!operation} aria-haspopup="dialog" onClick={() => { setDeleteError(""); confirm.current?.showModal(); }}>Delete my data</button>
        </div>
      </section>
    </div>
    <dialog className="confirm-dialog" ref={confirm} aria-labelledby="confirm-title" aria-describedby="confirm-description" onCancel={(event) => { if (operation === "delete") event.preventDefault(); }}>
      <h2 id="confirm-title">Delete this account?</h2>
      <p id="confirm-description">You’ll permanently delete <strong className="email">{email}</strong> and its resume, saved preferences, digest and edits.</p>
      {deleteError && <Fault message={deleteError}/>}
      <div className="button-row">
        <button type="button" className="button secondary" disabled={!!operation} onClick={() => confirm.current?.close()}>Cancel</button>
        <button type="button" className="button danger" disabled={!!operation} onClick={removeAccount}>{operation === "delete" ? "Deleting…" : "Delete account permanently"}</button>
      </div>
    </dialog>
  </>;
}
