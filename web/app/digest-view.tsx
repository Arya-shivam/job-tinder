"use client";

import { useEffect, useState } from "react";
import { api, type Account, type Digest, type ExclusionPage, type Fit, type Match, ruleLabels, workplaceLabels } from "./client-api";
import { count, Fault, Icon, Loading, PageHeader } from "./ui";

type Failure = (error: unknown, fallback: string) => string;
export type Proposal = { job: Match; editCount: number };

const fitLabels: Record<Fit, string> = { strong_fit: "Strong fit", fit: "Fit", stretch: "Stretch" };
const dayFormat = new Intl.DateTimeFormat("en-US", { dateStyle: "long", timeZone: "UTC" });
const pageSize = 25;

function employerUrl(value: string) {
  try {
    const url = new URL(value);
    if (url.protocol === "https:" || url.protocol === "http:") return url.href;
  } catch {
    // Missing or malformed source links aren't actionable.
  }
  return null;
}

export function DigestView({ account, exclusions, fail, onProposal }: {
  account: Account; exclusions: boolean; fail: Failure; onProposal: (proposal: Proposal) => void;
}) {
  const [digest, setDigest] = useState<Digest | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [runError, setRunError] = useState("");
  const [running, setRunning] = useState(false);
  const [reload, setReload] = useState(0);
  const [fit, setFit] = useState<Fit | "all">("all");
  const [proposing, setProposing] = useState<number | null>(null);
  const [proposalError, setProposalError] = useState<{ jobId: number; message: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api.digest(controller.signal).then((data) => {
      if (!controller.signal.aborted) setDigest(data);
    }).catch((error) => {
      if (!controller.signal.aborted) setLoadError(fail(error, "Couldn’t load your digest."));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [fail, reload]);

  async function run() {
    setRunning(true);
    setRunError("");
    try {
      setDigest(await api.run());
      setLoadError("");
    } catch (error) {
      setRunError(fail(error, "Couldn’t run a new search. Your previous digest is unchanged."));
    } finally {
      setRunning(false);
    }
  }

  async function propose(job: Match) {
    setProposing(job.job_id);
    setProposalError(null);
    try {
      const { edits } = await api.propose(job.job_id);
      onProposal({ job, editCount: edits.length });
    } catch (error) {
      setProposalError({ jobId: job.job_id, message: fail(error, "Couldn’t propose resume edits for this job.") });
    } finally {
      setProposing(null);
    }
  }

  if (exclusions) return <Exclusions fail={fail}/>;

  const allMatches = digest?.matches ?? [];
  const matches = fit === "all" ? allMatches : allMatches.filter((job) => job.fit === fit);
  const excluded = digest?.excluded_count;
  const day = digest?.day;
  const canRun = !!account.profile && !loading && !running;

  return <>
    <PageHeader title="Your job digest" action={account.profile ? <button className="button primary" type="button" onClick={run} disabled={!canRun}>
      <Icon name="run"/>{running ? "Searching…" : "Run now"}
    </button> : null}>
      <p className="digest-date"><Icon name="calendar"/>{day ? <>Digest for <time dateTime={day}>{dayFormat.format(new Date(`${day}T00:00:00Z`))}</time></> : loading ? "Checking latest digest…" : loadError ? "Digest unavailable" : "No digest yet"}</p>
    </PageHeader>
    <div className="app-grid digest-grid">
      <section className="digest-list" aria-labelledby="shortlist-title" aria-busy={loading || running}>
        <header className="section-bar">
          <div><h2 id="shortlist-title">Shortlist</h2><p className="muted section-meta" aria-live="polite">
            {digest?.day ? `${count.format(matches.length)}${fit === "all" ? "" : ` of ${count.format(allMatches.length)}`} ${fit === "all" && matches.length === 1 ? "match" : "matches"}` : "Roles worth a closer look"}
          </p></div>
          {allMatches.length > 0 && <label className="compact-filter" htmlFor="fit-filter">Fit <select id="fit-filter" value={fit} onChange={(event) => setFit(event.target.value as Fit | "all")}>
            <option value="all">All fits</option>
            {Object.entries(fitLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>}
        </header>
        {runError && <Fault message={runError} retry={canRun ? run : undefined}/>}
        {running && <p className="working-note" role="status">Checking job postings against your profile and search preferences…</p>}
        {loadError && <Fault message={loadError} retry={() => { setLoadError(""); setLoading(true); setReload((value) => value + 1); }}/>}
        {!account.profile ? <div className="setup-panel">
          <Icon name="file"/>
          <h3>Start with your resume</h3>
          <p>Upload a PDF so Job Match can compare your experience with what employers ask for.</p>
          <a className="button primary" href="#resume">Set up profile <Icon name="arrow"/></a>
        </div> : loading ? <Loading label="Loading your shortlist…"/> : loadError && !digest ? null : !day ? <div className="empty-panel">
          <h3>Your first digest is ready to run</h3>
          <p>Run a search to see which roles fit and what the listings say.</p>
        </div> : allMatches.length === 0 ? <div className="empty-panel">
          <h3>No matches in this digest</h3>
          <p>{excluded ? "You can review which jobs your dealbreakers put aside, or update your search preferences." : "No roles landed on the shortlist. Try changing your search preferences or running another search later."}</p>
          <a href={excluded ? "#set-aside" : "#preferences"} className="text-action">{excluded ? "Review set-aside jobs" : "Edit search preferences"} <Icon name="arrow"/></a>
        </div> : matches.length === 0 ? <div className="empty-panel">
          <h3>No jobs at this fit level</h3>
          <p>The current digest contains jobs in other fit categories.</p>
          <button className="text-action" type="button" onClick={() => setFit("all")}>Show all fits</button>
        </div> : <ul className="match-list">
          {matches.map((job) => <li key={job.job_id}><MatchProof job={job} canPropose={!!account.resume} proposing={proposing} propose={() => propose(job)} error={proposalError?.jobId === job.job_id ? proposalError.message : null}/></li>)}
        </ul>}
      </section>
      <aside className="digest-context" aria-label="Search context">
        <section className="panel context-panel set-aside-panel" aria-labelledby="aside-title">
          <h2 id="aside-title">Set-aside jobs</h2>
          <p className="aside-count num">{excluded === undefined || !day ? <span className="muted">—</span> : count.format(excluded)}</p>
          <p className="muted">Jobs left out by your saved dealbreakers.</p>
          <a href="#set-aside" className="text-action">Review exclusions <Icon name="arrow"/></a>
        </section>
        <section className="panel context-panel" aria-labelledby="criteria-title">
          <h2 id="criteria-title">Search criteria</h2>
          <dl className="criteria-values">
            <div><dt>Target roles</dt><dd>{account.preferences.target_roles.join(", ") || "Not set"}</dd></div>
            <div><dt>Workplace</dt><dd>{workplaceLabels[account.preferences.remote_preference]}</dd></div>
          </dl>
          <a href="#preferences" className="text-action">Open preferences <Icon name="arrow"/></a>
        </section>
      </aside>
    </div>
  </>;
}

function MatchProof({ job, canPropose, proposing, propose, error }: {
  job: Match; canPropose: boolean; proposing: number | null; propose: () => void; error: string | null;
}) {
  const url = employerUrl(job.url);
  return <article className="match-proof">
    <header className="match-header"><div className="min-w-0"><h3>{job.title}</h3>
      <p className="job-meta"><span>{job.company}</span><span>{job.location || "Location not listed"}</span></p></div>
      <span className={`status fit-${job.fit}`}>{fitLabels[job.fit]}</span>
    </header>
    <div className="proof-grid">
      <section aria-label="Matching evidence">
        <h4>Why it matched</h4>
        {job.reasons.length ? <ul className="evidence-list">{job.reasons.map((reason, index) => <li key={index}>
          <p>{reason.text}</p><blockquote>{reason.quote}</blockquote>
        </li>)}</ul> : <p className="muted">No evidence excerpt was returned.</p>}
      </section>
      <section className="gap-column" aria-label="Match gaps">
        <h4>Gaps to consider</h4>
        {job.gaps.length ? <ul className="text-list">{job.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul> : <p className="muted">No gaps identified.</p>}
      </section>
    </div>
    {error && <Fault message={error}/>}
    <footer className="match-actions">
      {url ? <a href={url} rel="noopener noreferrer" target="_blank" className="button secondary">Employer posting <Icon name="external"/><span className="sr-only"> (opens in a new tab)</span></a> : <span className="muted">Employer link unavailable</span>}
      {canPropose ? <button className="text-action" type="button" onClick={propose} disabled={proposing !== null}>{proposing === job.job_id ? "Proposing edits…" : "Propose resume edits"} <Icon name="arrow"/></button> : <a href="#resume" className="text-action">Upload a resume to propose edits <Icon name="arrow"/></a>}
    </footer>
  </article>;
}

function Exclusions({ fail }: { fail: Failure }) {
  const [data, setData] = useState<ExclusionPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api.exclusions(offset, pageSize, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      if (offset > 0 && offset >= page.total) {
        setOffset(page.total ? Math.floor((page.total - 1) / pageSize) * pageSize : 0);
        return;
      }
      setData(page);
      setLoading(false);
    }).catch((requestError) => {
      if (!controller.signal.aborted) {
        setError(fail(requestError, "Couldn’t load the jobs set aside for this digest."));
        setLoading(false);
      }
    });
    return () => controller.abort();
  }, [fail, offset, reload]);

  function changePage(next: number) {
    setError("");
    setData(null);
    setLoading(true);
    setOffset(next);
  }

  return <>
    <PageHeader title="Set-aside jobs" action={<a className="button secondary" href="#digest"><Icon name="back"/> Back to digest</a>}>
      <p>Review which of your saved dealbreakers kept a job out of the latest digest.</p>
    </PageHeader>
    <section className="stack" aria-labelledby="exclusions-title" aria-busy={loading}>
      <div className="section-bar"><div><h2 id="exclusions-title">Exclusion reasons</h2>
        {data && <p className="muted section-meta">{count.format(data.total)} {data.total === 1 ? "job" : "jobs"} set aside</p>}
      </div></div>
      {loading ? <Loading label="Loading exclusion reasons…"/> : error ? <Fault message={error} retry={() => { setError(""); setLoading(true); setReload((value) => value + 1); }}/> : data && data.exclusions.length ? <>
        <ul className="exclusion-list">{data.exclusions.map((job) => <li key={job.job_id} className="exclusion-row">
          <div><h3>{job.title}</h3><p className="muted">{job.company}</p></div>
          <ul className="rule-list">{job.rules.map(({ rule, reason }, index) => <li key={index}>
            <strong>{Object.hasOwn(ruleLabels, rule) ? ruleLabels[rule as keyof typeof ruleLabels] : rule}</strong><p>{reason}</p>
          </li>)}</ul>
        </li>)}</ul>
        <nav className="pagination" aria-label="Exclusions pages">
          <p className="muted num" role="status">{count.format(offset + 1)}–{count.format(offset + data.exclusions.length)} of {count.format(data.total)}</p>
          <div className="button-row"><button type="button" className="button secondary" disabled={offset === 0} onClick={() => changePage(Math.max(0, offset - pageSize))}>Previous</button>
            <button type="button" className="button secondary" disabled={offset + pageSize >= data.total} onClick={() => changePage(offset + pageSize)}>Next</button></div>
        </nav>
      </> : <div className="empty-panel"><h3>No jobs set aside</h3><p>No job exclusions are recorded for your latest digest.</p><a className="text-action" href="#preferences">Review search preferences <Icon name="arrow"/></a></div>}
    </section>
  </>;
}
