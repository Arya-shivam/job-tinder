import type { ReactNode } from "react";

const glyphs = {
  account: <><circle cx="12" cy="8" r="3.3"/><path d="M5 20v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2"/></>,
  arrow: <><path d="M5 12h14m-6-6 6 6-6 6"/></>,
  back: <path d="M19 12H5m6 6-6-6 6-6"/>,
  calendar: <><rect x="4" y="6" width="16" height="14" rx="2"/><path d="M8 4v4m8-4v4M4 10h16"/></>,
  chat: <path d="M20 15a2 2 0 0 1-2 2H9l-5 4V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2zM8 9h8m-8 4h5"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  close: <path d="m6 6 12 12M6 18 18 6"/>,
  digest: <><rect x="5" y="4" width="14" height="16" rx="2"/><path d="M8 8h8m-8 4h8m-8 4h5"/></>,
  download: <path d="M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4"/>,
  external: <path d="M13 5h6v6m0-6-8 8m8 1v5H5V5h5"/>,
  file: <path d="M13 4H6v16h12V9zm0 0v5h5M9 13h6m-6 3h5"/>,
  menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
  run: <path d="M20 11a8 8 0 1 0-2 6m2-11v5h-5"/>,
  send: <path d="m5 5 15 7-15 7 3-7zm3 7h12"/>,
  upload: <path d="M12 16V4m-4 4 4-4 4 4M5 16v4h14v-4"/>,
};

export function Icon({ name, className }: { name: keyof typeof glyphs; className?: string }) {
  return <svg aria-hidden="true" className={className ? `icon ${className}` : "icon"} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{glyphs[name]}</svg>;
}

export function Brand() {
  return <span className="brand">
    <svg aria-hidden="true" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M7 24H5V6h15v3M10 10h17v17H10zM14 18l3 3 6-7"/></svg>
    Job Match
  </span>;
}

export function PageHeader({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <header className="page-header">
    <div><h1 tabIndex={-1}>{title}</h1>{children ? <div className="page-description">{children}</div> : null}</div>
    {action ? <div className="page-actions">{action}</div> : null}
  </header>;
}

export function Fault({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="fault">
    <p role="alert">{message}</p>
    {retry && <button type="button" className="text-action" onClick={retry}>Try again</button>}
  </div>;
}

export function Loading({ label }: { label: string }) {
  return <div className="loading" role="status">
    <span>{label}</span>
    <span className="loading-line" aria-hidden="true"/><span className="loading-line loading-line-short" aria-hidden="true"/>
  </div>;
}

export function TextList({ values, empty }: { values: string[]; empty: string }) {
  return values.length ? <ul className="text-list">{values.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p className="muted">{empty}</p>;
}

export const count = new Intl.NumberFormat("en-US");
