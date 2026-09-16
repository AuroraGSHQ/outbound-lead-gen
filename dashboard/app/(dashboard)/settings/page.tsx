import { checkCredentials } from "@/lib/credentials";
import { Badge, Card, SectionTitle } from "@/components/ui";

export const dynamic = "force-dynamic";

export default function SettingsPage() {
  const groups = checkCredentials();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Settings
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-zinc-500 dark:text-zinc-400">
          Read-only status. Keys are configured via <code>.env</code> locally or your hosting
          platform&apos;s environment variable UI in production (Vercel for this dashboard,
          Railway/Render/Fly for the backend) — not through this page. No secret values are
          ever shown here, only whether a variable looks set.
        </p>
      </div>

      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
        Contract template upload is out of scope for this page — templates are managed via
        <code className="mx-1">CONTRACT_TEMPLATE_ID</code> in the backend&apos;s own config.
      </div>

      <div className="space-y-4">
        {groups.map((group) => (
          <Card key={group.name}>
            <SectionTitle>{group.name}</SectionTitle>
            <ul className="space-y-1.5">
              {group.items.map((item) => (
                <li key={item.envVar} className="flex items-center justify-between gap-2 text-sm">
                  <span className="text-zinc-700 dark:text-zinc-300">
                    {item.label}{" "}
                    <code className="text-xs text-zinc-400">{item.envVar}</code>
                  </span>
                  {item.set ? <Badge tone="ok">Set</Badge> : <Badge tone="danger">Missing</Badge>}
                </li>
              ))}
            </ul>
            {group.note && (
              <p className="mt-3 text-xs text-zinc-400 dark:text-zinc-500">{group.note}</p>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
