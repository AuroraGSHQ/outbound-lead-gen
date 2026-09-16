import { getDb } from "@/lib/db";
import { formatDate, isOverdue } from "@/lib/format";
import type { Deadline } from "@/lib/types";
import { Badge, Card, EmptyState, SectionTitle } from "@/components/ui";
import { CompleteDeadlineButton } from "@/components/CompleteDeadlineButton";

export const dynamic = "force-dynamic";

function DeadlineRow({ deadline }: { deadline: Deadline }) {
  const overdue = !deadline.completed && isOverdue(deadline.due_date);
  return (
    <Card className={overdue ? "border-red-300 dark:border-red-800" : ""}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-zinc-900 dark:text-zinc-100">
              {deadline.description || "(no description)"}
            </span>
            {overdue && <Badge tone="danger">Overdue</Badge>}
            {deadline.completed === 1 && <Badge tone="ok">Done</Badge>}
          </div>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            {deadline.contact_name || "(no contact)"} &middot; due {formatDate(deadline.due_date)}
          </p>
          {deadline.reminded_count > 0 && (
            <p className="mt-0.5 text-xs text-zinc-400">
              Reminded {deadline.reminded_count}x, last {formatDate(deadline.last_reminded_at)}
            </p>
          )}
        </div>
        {deadline.completed === 0 && <CompleteDeadlineButton deadlineId={deadline.id} />}
      </div>
    </Card>
  );
}

export default function DeadlinesPage() {
  const db = getDb();
  const deadlines = db
    .prepare(
      `SELECT deadlines.*, contacts.name AS contact_name
       FROM deadlines
       LEFT JOIN contacts ON contacts.id = deadlines.contact_id
       ORDER BY deadlines.due_date ASC
       LIMIT 500`
    )
    .all() as Deadline[];

  const open = deadlines.filter((d) => d.completed === 0);
  const overdue = open.filter((d) => isOverdue(d.due_date));
  const upcoming = open.filter((d) => !isOverdue(d.due_date));
  const completed = deadlines.filter((d) => d.completed === 1).slice(0, 20);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Deadlines
        </h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          {overdue.length} overdue, {upcoming.length} upcoming
        </p>
      </div>

      <section>
        <SectionTitle>Overdue</SectionTitle>
        {overdue.length === 0 ? (
          <EmptyState>Nothing overdue.</EmptyState>
        ) : (
          <ul className="space-y-2">
            {overdue.map((d) => (
              <li key={d.id}>
                <DeadlineRow deadline={d} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionTitle>Upcoming</SectionTitle>
        {upcoming.length === 0 ? (
          <EmptyState>Nothing upcoming.</EmptyState>
        ) : (
          <ul className="space-y-2">
            {upcoming.map((d) => (
              <li key={d.id}>
                <DeadlineRow deadline={d} />
              </li>
            ))}
          </ul>
        )}
      </section>

      {completed.length > 0 && (
        <section>
          <SectionTitle>Recently completed</SectionTitle>
          <ul className="space-y-2">
            {completed.map((d) => (
              <li key={d.id}>
                <DeadlineRow deadline={d} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
