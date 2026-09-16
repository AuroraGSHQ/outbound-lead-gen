import { getDb } from "@/lib/db";
import { formatDateTime } from "@/lib/format";
import type { Notification } from "@/lib/types";
import { Badge, Card, EmptyState, SectionTitle, StatCard } from "@/components/ui";

export const dynamic = "force-dynamic";

const NOTIFICATION_LABELS: Record<string, string> = {
  payment_received: "Payment received",
  deadline_due: "Deadline due",
  contract_needs_approval: "Contract needs approval",
  call_summary_ready: "Call summary ready",
  missed_message: "Missed message",
};

export default function DashboardHomePage() {
  const db = getDb();

  const { c: openDeadlines } = db
    .prepare("SELECT COUNT(*) AS c FROM deadlines WHERE completed = 0")
    .get() as { c: number };

  const { c: pendingContracts } = db
    .prepare("SELECT COUNT(*) AS c FROM contracts WHERE status = 'pending_approval'")
    .get() as { c: number };

  const { c: callsToday } = db
    .prepare("SELECT COUNT(*) AS c FROM call_logs WHERE date(created_at) = date('now')")
    .get() as { c: number };

  const notifications = db
    .prepare("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 30")
    .all() as Notification[];

  return (
    <div className="space-y-8">
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard
          label="Open deadlines"
          value={openDeadlines}
          href="/deadlines"
          tone={openDeadlines > 0 ? "warn" : "ok"}
        />
        <StatCard
          label="Contracts awaiting approval"
          value={pendingContracts}
          href="/contracts"
          tone={pendingContracts > 0 ? "warn" : "ok"}
        />
        <StatCard label="Calls today" value={callsToday} href="/calls" tone="neutral" />
      </section>

      <section>
        <SectionTitle>Notifications</SectionTitle>
        {notifications.length === 0 ? (
          <EmptyState>No notifications yet.</EmptyState>
        ) : (
          <ul className="space-y-2">
            {notifications.map((n) => (
              <li key={n.id}>
                <Card className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                        {NOTIFICATION_LABELS[n.type] || n.type}
                      </span>
                      {n.delivered ? (
                        <Badge tone="ok">Delivered</Badge>
                      ) : n.delivery_error ? (
                        <Badge tone="danger">Failed</Badge>
                      ) : (
                        <Badge tone="warn">Pending</Badge>
                      )}
                    </div>
                    <p className="mt-1 truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
                      {n.title || "(untitled)"}
                    </p>
                    {n.body && (
                      <p className="mt-0.5 text-sm text-zinc-600 dark:text-zinc-400">{n.body}</p>
                    )}
                    {n.delivery_error && (
                      <p className="mt-0.5 text-xs text-red-600 dark:text-red-400">
                        {n.delivery_error}
                      </p>
                    )}
                  </div>
                  <span className="shrink-0 text-xs text-zinc-400">
                    {formatDateTime(n.created_at)}
                  </span>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
