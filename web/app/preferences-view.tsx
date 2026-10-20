"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import { api, type Preferences, ruleLabels, workplaceLabels } from "./client-api";
import { Fault, Icon, PageHeader, TextList } from "./ui";

type Turn = { role: "you" | "assistant"; text: string };

export function PreferencesView({ preferences, visible, save, fail }: {
  preferences: Preferences;
  visible: boolean;
  save: (preferences: Preferences) => void;
  fail: (error: unknown, fallback: string) => string;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [message, setMessage] = useState("");
  const [pendingMessage, setPendingMessage] = useState("");
  const [error, setError] = useState("");
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (visible && log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [visible, turns, pendingMessage]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = message.trim();
    if (pendingMessage || !text) return;
    setPendingMessage(text);
    setError("");
    try {
      const { reply, preferences: updated } = await api.chat(text);
      save(updated);
      setTurns((current) => [...current, { role: "you", text }, { role: "assistant", text: reply }]);
      setMessage("");
    } catch (requestError) {
      setError(fail(requestError, "Couldn’t send your message. Your draft hasn’t been lost."));
    } finally {
      setPendingMessage("");
    }
  }

  return <>
    <PageHeader title="Search preferences"><p>Tell Job Match what you’re looking for and see what it remembers.</p></PageHeader>
    <div className="app-grid preferences-grid">
      <section className="panel chat-panel" aria-labelledby="chat-title">
        <h2 id="chat-title">Preference chat</h2>
        <p className="muted">Describe a change in your own words. Check Search memory for the values that were actually saved.</p>
        <div className="chat-log" ref={log} role="log" aria-label="Preference conversation" aria-relevant="additions text" tabIndex={turns.length > 0 ? 0 : undefined}>
          {turns.length || pendingMessage ? <ol>
            {[...turns, ...(pendingMessage ? [{ role: "you" as const, text: pendingMessage }] : [])].map((turn, index) => <li key={index} className={`turn turn-${turn.role}`}>
              <span className="turn-label">{turn.role === "you" ? "You" : "Job Match"}</span><p>{turn.text}</p>
            </li>)}
          </ol> : <div className="chat-intro"><Icon name="chat"/><p>Tell Job Match which roles, locations, or work arrangements to look for. Mention anything that should rule a job out.</p></div>}
        </div>
        {pendingMessage && <p className="muted" role="status">Job Match is replying…</p>}
        {error && <Fault message={error}/>}
        <form className="chat-form" onSubmit={send}>
          <label htmlFor="chat-message">Message</label>
          <textarea id="chat-message" value={message} onChange={(event) => setMessage(event.target.value)} rows={3} disabled={!!pendingMessage} required/>
          <div className="chat-submit"><button type="submit" className="button primary" disabled={!!pendingMessage || !message.trim()}><Icon name="send"/>{pendingMessage ? "Sending…" : "Send message"}</button></div>
        </form>
      </section>
      <section className="panel memory-panel" aria-labelledby="memory-title">
        <h2 id="memory-title">Search memory</h2>
        <p className="muted">These saved preferences shape your next digest.</p>
        <dl className="memory-values" aria-live="polite">
          <div><dt>Target roles</dt><dd><TextList values={preferences.target_roles} empty="No target roles set"/></dd></div>
          <div><dt>Locations</dt><dd><TextList values={preferences.locations} empty="No locations set"/></dd></div>
          <div><dt>Workplace</dt><dd>{workplaceLabels[preferences.remote_preference]}</dd></div>
          <div><dt>Dealbreakers</dt><dd>{preferences.dealbreakers.length ? <ul className="memory-rules">{preferences.dealbreakers.map(({ kind, value }, index) => <li key={index}><span>{ruleLabels[kind]}</span><strong>{value}</strong></li>)}</ul> : <p className="muted">No dealbreakers set</p>}</dd></div>
        </dl>
      </section>
    </div>
  </>;
}
