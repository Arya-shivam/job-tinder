export type Dealbreaker = {
  kind: "location" | "workplace" | "min_salary" | "company" | "employment_type";
  value: string;
};

export type Preferences = {
  target_roles: string[];
  locations: string[];
  remote_preference: "any" | "remote" | "hybrid" | "onsite";
  dealbreakers: Dealbreaker[];
};

export type Profile = {
  headline: string;
  skills: string[];
  experience: string[];
  credentials: string[];
};

export type Account = {
  email: string;
  profile: Profile | null;
  preferences: Preferences;
  resume: { filename: string } | null;
};

export type Fit = "strong_fit" | "fit" | "stretch";

export type Match = {
  job_id: number;
  title: string;
  company: string;
  location: string | null;
  url: string;
  fit: Fit;
  reasons: { text: string; quote: string }[];
  gaps: string[];
};

export type Digest = {
  day: string | null;
  matches: Match[];
  excluded_count: number;
};

export type ExclusionPage = {
  exclusions: { job_id: number; title: string; company: string; rules: { rule: string; reason: string }[] }[];
  total: number;
};

export type Edit = {
  id: string;
  job_id: number;
  before: string;
  after: string;
  status: "pending" | "approved" | "rejected";
};

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

export const errorText = (error: unknown, fallback: string) =>
  error instanceof ApiError ? error.message : fallback;

export const isSignedOut = (error: unknown) => error instanceof ApiError && error.status === 401;

async function call(path: string, options: RequestInit = {}) {
  const response = await fetch(`/api${path}`, { ...options, cache: "no-store", credentials: "same-origin" });
  if (!response.ok) {
    const body: { detail?: unknown } | null = await response.json().catch(() => null);
    let detail = body?.detail;
    if (Array.isArray(detail)) {
      detail = detail.flatMap((issue) => {
        if (typeof issue?.msg !== "string") return [];
        const field = Array.isArray(issue.loc) ? issue.loc.at(-1) : null;
        return [typeof field === "string" && field !== "body" ? `${field}: ${issue.msg}` : issue.msg];
      }).join("; ");
    }
    throw new ApiError(response.status, typeof detail === "string" && detail ? detail : "The service couldn’t complete this request.");
  }
  return response;
}

function jsonBody(method: "POST" | "PATCH", body: object): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  async me(signal?: AbortSignal): Promise<Account> {
    return (await call("/me", { signal })).json();
  },
  async auth(mode: "login" | "register", email: string, password: string): Promise<Account> {
    return (await call(`/auth/${mode}`, jsonBody("POST", { email, password }))).json();
  },
  async logout() {
    await call("/auth/logout", { method: "POST" });
  },
  async deleteAccount() {
    await call("/me", { method: "DELETE" });
  },
  async resume(file: File): Promise<Account> {
    const data = new FormData();
    data.append("file", file);
    return (await call("/resume", { method: "POST", body: data })).json();
  },
  async chat(message: string): Promise<{ reply: string; preferences: Preferences }> {
    return (await call("/chat", jsonBody("POST", { message }))).json();
  },
  async digest(signal?: AbortSignal): Promise<Digest> {
    return (await call("/digest", { signal })).json();
  },
  async run(): Promise<Digest> {
    return (await call("/digest/run", { method: "POST" })).json();
  },
  async exclusions(offset: number, limit: number, signal: AbortSignal): Promise<ExclusionPage> {
    return (await call(`/digest/exclusions?offset=${offset}&limit=${limit}`, { signal })).json();
  },
  async propose(jobId: number): Promise<{ edits: Edit[] }> {
    return (await call("/resume/edits", jsonBody("POST", { job_id: jobId }))).json();
  },
  async edits(signal?: AbortSignal): Promise<{ edits: Edit[] }> {
    return (await call("/resume/edits", { signal })).json();
  },
  async decide(id: string, decision: "approve" | "reject"): Promise<Edit> {
    return (await call(`/resume/edits/${encodeURIComponent(id)}`, jsonBody("PATCH", { decision }))).json();
  },
  async tailored(): Promise<Blob> {
    return (await call("/resume/tailored")).blob();
  },
};

export const ruleLabels: Record<Dealbreaker["kind"], string> = {
  location: "Location",
  workplace: "Workplace",
  min_salary: "Minimum annual salary",
  company: "Company",
  employment_type: "Employment type",
};

export const workplaceLabels: Record<Preferences["remote_preference"], string> = {
  any: "Any workplace",
  remote: "Remote",
  hybrid: "Hybrid",
  onsite: "On-site",
};
