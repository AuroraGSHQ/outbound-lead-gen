import Link from "next/link";
import { getDb } from "@/lib/db";
import { formatDate } from "@/lib/format";
import type { Contact } from "@/lib/types";
import { Badge, Card, EmptyState, SectionTitle } from "@/components/ui";
import { DncToggle } from "@/components/DncToggle";

export const dynamic = "force-dynamic";

const SEGMENTS = ["prospect", "current_client", "past_client", "callback_requested"] as const;

const SEGMENT_LABELS: Record<string, string> = {
  prospect: "Prospect",
  current_client: "Current client",
  past_client: "Past client",
  callback_requested: "Callback requested",
};

const SEGMENT_TONE: Record<string, "ok" | "warn" | "danger" | "neutral"> = {
  prospect: "neutral",
  current_client: "ok",
  past_client: "neutral",
  callback_requested: "warn",
};

interface SearchParams {
  q?: string;
  segment?: string;
  service_line?: string;
}

export default async function ContactsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const q = (params.q || "").trim();
  const segment = params.segment || "";
  const serviceLine = (params.service_line || "").trim();

  const conditions: string[] = [];
  const values: unknown[] = [];

  if (segment) {
    conditions.push("segment = ?");
    values.push(segment);
  }
  if (serviceLine) {
    conditions.push("service_line LIKE ?");
    values.push(`%${serviceLine}%`);
  }
  if (q) {
    conditions.push("(name LIKE ? OR phone LIKE ? OR email LIKE ? OR company_name LIKE ?)");
    const like = `%${q}%`;
    values.push(like, like, like, like);
  }

  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  const db = getDb();
  const contacts = db
    .prepare(`SELECT * FROM contacts ${where} ORDER BY updated_at DESC LIMIT 200`)
    .all(...values) as Contact[];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Contacts
        </h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          {contacts.length} shown (max 200)
        </p>
      </div>

      <form className="flex flex-wrap gap-2" method="get">
        <input
          type="text"
          name="q"
          defaultValue={q}
          placeholder="Search name, phone, email, company..."
          className="min-w-[200px] flex-1 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
        />
        <select
          name="segment"
          defaultValue={segment}
          className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
        >
          <option value="">All segments</option>
          {SEGMENTS.map((s) => (
            <option key={s} value={s}>
              {SEGMENT_LABELS[s]}
            </option>
          ))}
        </select>
        <input
          type="text"
          name="service_line"
          defaultValue={serviceLine}
          placeholder="Service line"
          className="w-40 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
        />
        <button
          type="submit"
          className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          Filter
        </button>
        {(q || segment || serviceLine) && (
          <Link
            href="/contacts"
            className="rounded-lg border border-zinc-300 px-4 py-2 text-sm text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Clear
          </Link>
        )}
      </form>

      {contacts.length === 0 ? (
        <EmptyState>No contacts match these filters.</EmptyState>
      ) : (
        <SectionTitle>{contacts.length} contact{contacts.length === 1 ? "" : "s"}</SectionTitle>
      )}

      <ul className="space-y-2">
        {contacts.map((c) => (
          <li key={c.id}>
            <Card className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-zinc-900 dark:text-zinc-100">
                    {c.name || "(no name)"}
                  </span>
                  <Badge tone={SEGMENT_TONE[c.segment] ?? "neutral"}>
                    {SEGMENT_LABELS[c.segment] ?? c.segment}
                  </Badge>
                  {c.service_line && <Badge tone="neutral">{c.service_line}</Badge>}
                </div>
                <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                  {[c.company_name, c.phone, c.email].filter(Boolean).join(" · ") || "—"}
                </p>
                <p className="mt-0.5 text-xs text-zinc-400">
                  Last contacted {formatDate(c.last_contacted_at)}
                </p>
              </div>
              <DncToggle contactId={c.id} initial={c.do_not_call === 1} />
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}
