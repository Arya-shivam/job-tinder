"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import { api, type Account, type Edit, type Profile } from "./client-api";
import type { Proposal } from "./digest-view";
import { count, Fault, Icon, Loading, PageHeader, TextList } from "./ui";

const maxPdfBytes = 5 * 1024 * 1024;
const statusLabels: Record<Edit["status"], string> = { pending: "Needs review", approved: "Approved", rejected: "Rejected" };

export function ResumeView({ account, proposal, clearProposal, save, fail }: {
  account: Account; proposal: Proposal | null; clearProposal: () => void; save: (account: Account) => void;
  fail: (error: unknown, fallback: string) => string;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadNote, setUploadNote] = useState("");
  const [edits, setEdits] = useState<Edit[]>([]);
  const [loading, setLoading] = useState(!!account.resume);
  const [listError, setListError] = useState("");
  const [reload, setReload] = useState(0);
  const [deciding, setDeciding] = useState<{ id: string; decision: "approve" | "reject" } | null>(null);
  const [decisionError, setDecisionError] = useState<{ id: string; message: string } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const [downloadNote, setDownloadNote] = useState("");
  const hasResume = !!account.resume;

  useEffect(() => {
    if (!hasResume) return;
    setLoading(true);
    setListError("");
    const controller = new AbortController();
    api.edits(controller.signal).then(({ edits }) => {
      if (!controller.signal.aborted) setEdits(edits);
    }).catch((error) => {
      if (!controller.signal.aborted) setListError(fail(error, "Couldn’t load your proposed edits."));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [hasResume, fail, reload, proposal]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || uploading) return;
    setUploadError("");
    setUploadNote("");
    if (file.size > maxPdfBytes) {
      setUploadError("Choose a PDF no larger than 5 MiB.");
      return;
    }
    setUploading(true);
    try {
      const updated = await api.resume(file);
      save(updated);
      clearProposal();
      setEdits([]);
      setListError("");
      setDecisionError(null);
      setLoading(true);
      setReload((value) => value + 1);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      setUploadNote("Resume imported. Review your extracted profile below.");
    } catch (error) {
      setUploadError(fail(error, "Couldn’t import your PDF. Your previous resume is unchanged."));
    } finally {
      setUploading(false);
    }
  }

  async function decide(id: string, decision: "approve" | "reject") {
    setDeciding({ id, decision });
    setDecisionError(null);
    try {
      const updated = await api.decide(id, decision);
      setEdits((current) => current.map((edit) => edit.id === id ? updated : edit));
    } catch (error) {
      setDecisionError({ id, message: fail(error, "Couldn’t save your decision for this edit.") });
    } finally {
      setDeciding(null);
    }
  }

  async function download() {
    setDownloading(true);
    setDownloadError("");
    setDownloadNote("");
    try {
      const url = URL.createObjectURL(await api.tailored());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "job-match-tailored-resume.txt";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setDownloadNote("Your download is ready.");
    } catch (error) {
      setDownloadError(fail(error, "Couldn’t download your tailored resume."));
    } finally {
      setDownloading(false);
    }
  }

  const visibleEdits = proposal ? edits.filter((edit) => edit.job_id === proposal.job.job_id) : edits;
  const pending = visibleEdits.filter((edit) => edit.status === "pending").length;
  const orderedEdits = [...visibleEdits.filter((edit) => edit.status === "pending"), ...visibleEdits.filter((edit) => edit.status !== "pending")];

  return <>
    <PageHeader title="Resume" action={hasResume ? <button className="button secondary" type="button" onClick={download} disabled={downloading || uploading || !!deciding}>
      <Icon name="download"/>{downloading ? "Preparing download…" : "Download tailored resume"}
    </button> : undefined}><p>Manage your PDF and review changes before they go into a tailored download.</p></PageHeader>
    {downloadError && <Fault message={downloadError}/>}
    {downloadNote && <p className="working-note" role="status">{downloadNote}</p>}
    <div className="app-grid resume-grid">
      <section className="panel upload-panel" aria-labelledby="pdf-title">
        <h2 id="pdf-title">Resume PDF</h2>
        {account.resume ? <div className="file-record"><Icon name="file"/><span className="min-w-0">{account.resume.filename}</span></div> : <p className="muted">No resume uploaded.</p>}
        <form className="upload-form" onSubmit={upload}>
          <label htmlFor="resume-file">{account.resume ? "Choose a replacement PDF" : "Choose a PDF"}</label>
          <input id="resume-file" name="resume" type="file" accept="application/pdf,.pdf" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setUploadError(""); setUploadNote(""); }} disabled={uploading} ref={fileInput} aria-describedby="file-hint" required/>
          <p id="file-hint" className="muted">Up to 5 MiB and 20 pages. The PDF must contain selectable text.</p>
          {account.resume && <p className="muted">Replacing your PDF clears edits for the previous version, including approved edits.</p>}
          <button type="submit" className="button primary" disabled={!file || uploading || !!deciding || downloading}><Icon name="upload"/>{uploading ? "Importing…" : account.resume ? "Replace resume" : "Upload resume"}</button>
        </form>
        {uploading && <p className="working-note" role="status">Extracting your profile from the PDF…</p>}
        {uploadError && <Fault message={uploadError}/>}
        {uploadNote && <p className="working-note" role="status">{uploadNote}</p>}
      </section>
      <section className="resume-edits" aria-labelledby="edits-title" aria-busy={loading}>
        <header className="section-bar"><div><h2 id="edits-title">Proposed edits</h2><p className="muted section-meta">{loading || listError ? "Review wording before approving changes." : <><span className="num">{count.format(pending)}</span> {pending === 1 ? "edit" : "edits"} to review</>}</p></div>
          {proposal && <button type="button" className="text-action" onClick={clearProposal}>Show all jobs</button>}
        </header>
        {proposal && <p className="working-note" role="status">{proposal.editCount ? "Showing edits for" : "No new edits were returned for"} <strong>{proposal.job.title}</strong> at {proposal.job.company}.</p>}
        {loading ? <Loading label="Loading proposed edits…"/> : listError ? <Fault message={listError} retry={() => { setListError(""); setLoading(true); setReload((value) => value + 1); }}/> : orderedEdits.length ? <>
          <p className="edit-explainer muted">Approved wording is included in the plain-text download. Your original PDF is unchanged.</p>
          <ul className="edit-list">{orderedEdits.map((edit) => <li key={edit.id} className="edit-proof">
            <div className="item-heading"><h3>{proposal?.job.job_id === edit.job_id ? proposal.job.title : `Job #${edit.job_id}`}</h3><span className={`status ${edit.status}`}>{statusLabels[edit.status]}</span></div>
            <dl className="before-after">
              <div><dt>Current text</dt><dd>{edit.before}</dd></div>
              <div><dt>Suggested text</dt><dd>{edit.after}</dd></div>
            </dl>
            {decisionError?.id === edit.id && <Fault message={decisionError.message}/>}
            {edit.status === "pending" && <div className="button-row">
              <button type="button" className="button secondary" disabled={!!deciding || uploading} onClick={() => decide(edit.id, "approve")}><Icon name="check"/>{deciding?.id === edit.id && deciding.decision === "approve" ? "Approving…" : "Approve"}</button>
              <button type="button" className="button secondary" disabled={!!deciding || uploading} onClick={() => decide(edit.id, "reject")}>{deciding?.id === edit.id && deciding.decision === "reject" ? "Rejecting…" : "Reject"}</button>
            </div>}
          </li>)}</ul>
        </> : <div className="empty-panel">
          <h3>{proposal ? "No edits to review for this job" : "No proposed edits yet"}</h3>
          <p>{hasResume ? "Open a match and ask Job Match to suggest changes for that particular posting." : "Upload your PDF before requesting changes for a matched role."}</p>
          {hasResume && <a href="#digest" className="text-action">Open your digest <Icon name="arrow"/></a>}
        </div>}
      </section>
      <section className="panel profile-panel" aria-labelledby="profile-title">
        <h2 id="profile-title">Extracted profile</h2>
        {account.profile ? <ExtractedProfile profile={account.profile}/> : <p className="muted">Your headline, skills, experience and credentials will appear after you import a PDF.</p>}
      </section>
    </div>
  </>;
}

function ExtractedProfile({ profile }: { profile: Profile }) {
  return <dl className="memory-values">
    <div><dt>Headline</dt><dd>{profile.headline || "Not extracted"}</dd></div>
    <div><dt>Skills</dt><dd><TextList values={profile.skills} empty="No skills extracted"/></dd></div>
    <div><dt>Experience</dt><dd><TextList values={profile.experience} empty="No experience extracted"/></dd></div>
    <div><dt>Credentials</dt><dd><TextList values={profile.credentials} empty="No credentials extracted"/></dd></div>
  </dl>;
}
