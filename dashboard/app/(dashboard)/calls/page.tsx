import Link from "next/link";
import { getDb } from "@/lib/db";
import { formatDateTime } from "@/lib/format";
import type { CallLog } from "@/lib/types";
import { Badge, Card, EmptyState, SectionTitle } from "@/components/ui";

export const dynamic = "force-dynamic";

const CALL_TYPE_LABELS: Record<string, string> = {
  ai_outbound: "AI outbound",
  zoom: "Zoom",
  phone_manual: "Manual phone",
};

interface SearchParams {
  contact?: string;
  date?: string;
  outcome?: string;
}

export default async function CallsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const contact = (params.contact || "").trim();
  const date = (params.date || "").trim();
  const outcome = params.outcome || "";

  const db = getDb();

  const conditions: string[] = [];
  const values: unknown[] = [];

  if (contact) {
    conditions.push("contacts.name LIKE ?");
    values.push(`%${contact}%`);
  }
  if (date) {
    conditions.push("date(call_logs.created_at) = ?");
    values.push(date);
  }
  if (outcome) {
    conditions.push("call_logs.outcome = ?");
    values.push(outcome);
  }
  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

  const calls = db
    .prepare(
      `SELECT call_logs.*, contacts.name AS contact_name
       FROM call_logs
       LEFT JOIN contacts ON contacts.id = call_logs.contact_id
       ${where}
       ORDER BY call_logs.created_at DESC
       LIMIT 200`
    )
    .all(...values) as CallLog[];

  const outcomes = db
    .prepare(
      "SELECT DISTINCT outcome FROM call_logs WHERE outcome != '' ORDER BY outcome"
    )
    .all() as { outcome: string }[];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Calls
        </h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          {calls.length} shown (max 200)
        </p>
      </div>

      <form className="flex flex-wrap gap-2" method="get">
        <input
          type="text"
          name="contact"
          defaultValue={contact}
          placeholder="Contact name"
          className="min-w-[160px] flex-1 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
        />
        <input
          type="date"
          name="date"
          defaultValue={date}
          className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
        />
        <select
          name="outcome"
          defaultValue={outcome}
          className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
        >
          <option value="">All outcomes</option>
          {outcomes.map((o) => (
            <option key={o.outcome} value={o.outcome}>
              {o.outcome}
            </option>
          ))}
        </select>
        <button
          type="submit"
          className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          Filter
        </button>
        {(contact || date || outcome) && (
          <Link
            href="/calls"
            className="rounded-lg border border-zinc-300 px-4 py-2 text-sm text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Clear
          </Link>
        )}
      </form>

      {calls.length === 0 ? (
        <EmptyState>No calls match these filters.</EmptyState>
      ) : (
        <>
          <SectionTitle>{calls.length} call{calls.length === 1 ? "" : "s"}</SectionTitle>
          <ul className="space-y-2">
            {calls.map((call) => (
              <li key={call.id}>
                <Card>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-zinc-900 dark:text-zinc-100">
                        {call.contact_name || "(unknown contact)"}
                      </span>
                      <Badge tone="neutral">{CALL_TYPE_LABELS[call.call_type] || call.call_type}</Badge>
                      {call.outcome && <Badge tone="neutral">{call.outcome}</Badge>}
                      {!call.processed_at && <Badge tone="warn">Not yet summarized</Badge>}
                    </div>
                    <span className="text-xs text-zinc-400">
                      {formatDateTime(call.started_at || call.created_at)}
                    </span>
                  </div>
                  {call.summary && (
                    <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">{call.summary}</p>
                  )}
                  {call.deadline_mentioned && (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                      Deadline mentioned: {call.deadline_mentioned}
                    </p>
                  )}
                  {call.recording_url && (
                    <a
                      href={call.recording_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2 inline-block text-xs font-medium text-zinc-500 underline hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
                    >
                      Recording
                    </a>
                  )}
                </Card>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
