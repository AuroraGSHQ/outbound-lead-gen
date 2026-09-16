/**
 * Row shapes for the tables this dashboard reads/writes directly via
 * lib/db.ts. Mirrors schema.sql (repo root) — keep in sync if that file
 * changes. SQLite has no real boolean type, so the 0/1 CHECK columns come
 * back as `number` from better-sqlite3, not `boolean`.
 */

export interface Contact {
  id: number;
  lead_id: number | null;
  name: string;
  phone: string;
  email: string;
  company_name: string;
  segment: "prospect" | "current_client" | "past_client" | "callback_requested";
  service_line: string;
  do_not_call: 0 | 1;
  last_contacted_at: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface CallLog {
  id: number;
  contact_id: number | null;
  call_type: "ai_outbound" | "zoom" | "phone_manual";
  direction: "outbound" | "inbound";
  external_call_id: string;
  transcript: string;
  recording_url: string;
  outcome: string;
  summary: string;
  agreed_items: string;
  deadline_mentioned: string | null;
  processed_at: string | null;
  started_at: string | null;
  created_at: string;
  contact_name?: string | null;
}

export interface Deadline {
  id: number;
  call_log_id: number | null;
  contact_id: number | null;
  description: string;
  due_date: string;
  completed: 0 | 1;
  reminded_count: number;
  last_reminded_at: string | null;
  created_at: string;
  contact_name?: string | null;
}

export interface Contract {
  id: number;
  contact_id: number | null;
  call_log_id: number | null;
  template_id: string;
  provider: "pandadoc" | "docusign";
  external_document_id: string;
  filled_data: string;
  status: "pending_approval" | "approved" | "rejected" | "sent" | "signed";
  rejection_reason: string;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
  signed_at: string | null;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
}

export interface Notification {
  id: number;
  type:
    | "payment_received"
    | "deadline_due"
    | "contract_needs_approval"
    | "call_summary_ready"
    | "missed_message";
  title: string;
  body: string;
  payload: string;
  contact_id: number | null;
  delivered: 0 | 1;
  delivered_at: string | null;
  delivery_error: string;
  created_at: string;
}
