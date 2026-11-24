export type Analysis = {
  category: string;
  summary: string;
  intent: string;
  importance: number;
  urgency: number;
  reply_required: boolean;
  vip: boolean;
  priority_score: number;
  evidence: string[];
  tasks: { title: string; due_at: string | null; date_text: string | null }[];
  recommended_actions: string[];
  follow_up_at: string | null;
};
export type Draft = {
  id: string;
  subject: string;
  body: string;
  version: number;
  status: string;
};
export type Mail = {
  id: string;
  sender: string;
  subject: string;
  received_at: string;
  unread: boolean;
  preview: string;
  score: number | null;
  priority: string | null;
  priority_override: number | null;
  analysis: Analysis | null;
};
export type Detail = Mail & {
  body: string;
  reply_to: string | null;
  draft: Draft | null;
  runs: {
    id: string;
    status: string;
    started_at: string;
    model: string;
    error_code: string | null;
  }[];
};
export type Thread = {
  id: string;
  sender: string;
  subject: string;
  body: string;
  received_at: string;
};
export type Audit = {
  id: string;
  action: string;
  outcome: string;
  created_at: string;
  metadata: Record<string, string | number | null>;
};
export type Dashboard = {
  total: number;
  analyzed: number;
  urgent: number;
  awaiting_reply: number;
  pending: number;
  open_tasks: number;
  priority_inbox: Mail[];
  activity: Audit[];
};
export type Approval = {
  id: string;
  email_id: string;
  status: string;
  action_type: string;
  draft_id: string | null;
  payload_hash: string;
  error_code: string | null;
  payload: {
    from: string;
    to?: string;
    subject: string;
    body?: string;
    version?: number;
  };
};
export type Task = {
  id: string;
  email_id: string;
  subject: string;
  title: string;
  due_at: string | null;
  original_date_text: string | null;
  follow_up_at: string | null;
  status: string;
};
export type Profile = {
  email: string;
  mode: "demo" | "gmail";
  status: string;
  csrf: string;
  scopes: string[];
  llm_provider: string;
  gmail_available: boolean;
};
export type Preferences = {
  timezone: string;
  tone: string;
  vip_senders: string[];
  follow_up_days: number;
  ai_consent: boolean;
};
export type Items<T> = { items: T[] };
export type Inbox = Items<Mail> & { total: number; page: number };

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  method = "GET",
  data?: unknown,
  csrf?: string,
): Promise<T> {
  const response = await fetch("/api/v1" + path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      ...(data !== undefined && { "Content-Type": "application/json" }),
      ...(csrf && { "X-CSRF-Token": csrf }),
    },
    ...(data !== undefined && { body: JSON.stringify(data) }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      payload?.message || "Could not reach InboxPilot. Try again.",
      response.status,
    );
  return payload as T;
}
