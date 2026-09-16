import { getDb } from "@/lib/db";
import { formatDateTime, parseJsonLoose } from "@/lib/format";
import type { Contract } from "@/lib/types";
import { Badge, Card, EmptyState, SectionTitle } from "@/components/ui";
import { ContractActions } from "@/components/ContractActions";

export const dynamic = "force-dynamic";

function FilledData({ raw }: { raw: string }) {
  const parsed = parseJsonLoose(raw);
  if (!parsed || typeof parsed !== "object") {
    return <p className="text-sm text-zinc-500">{raw || "(empty)"}</p>;
  }
  const entries = Object.entries(parsed as Record<string, unknown>);
  if (entries.length === 0) {
    return <p className="text-sm text-zinc-500">(empty)</p>;
  }
  return (
    <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="flex justify-between gap-2 sm:block">
          <dt className="text-xs uppercase tracking-wide text-zinc-400">{key}</dt>
          <dd className="text-zinc-800 dark:text-zinc-200">
            {typeof value === "object" ? JSON.stringify(value) : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function ContractsPage() {
  const db = getDb();
  const contracts = db
    .prepare(
      `SELECT contracts.*, contacts.name AS contact_name, contacts.email AS contact_email,
              contacts.phone AS contact_phone
       FROM contracts
       LEFT JOIN contacts ON contacts.id = contracts.contact_id
       WHERE contracts.status = 'pending_approval'
       ORDER BY contracts.created_at ASC`
    )
    .all() as Contract[];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Contracts
        </h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          Approval queue — {contracts.length} awaiting your decision
        </p>
      </div>

      {contracts.length === 0 ? (
        <EmptyState>Nothing waiting on approval.</EmptyState>
      ) : (
        <ul className="space-y-3">
          {contracts.map((contract) => (
            <li key={contract.id}>
              <Card>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-zinc-900 dark:text-zinc-100">
                        {contract.contact_name || "(no contact)"}
                      </span>
                      <Badge tone="neutral">{contract.provider}</Badge>
                      <Badge tone="warn">Pending approval</Badge>
                    </div>
                    <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
                      {[contract.contact_email, contract.contact_phone].filter(Boolean).join(" · ")}
                    </p>
                    <p className="mt-0.5 text-xs text-zinc-400">
                      Created {formatDateTime(contract.created_at)}
                    </p>
                  </div>
                  <ContractActions contractId={contract.id} />
                </div>
                <div className="mt-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
                  <SectionTitle>Filled data</SectionTitle>
                  <FilledData raw={contract.filled_data} />
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
