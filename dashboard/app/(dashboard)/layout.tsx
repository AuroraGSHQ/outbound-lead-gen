import { AskJarvisBar } from "@/components/AskJarvisBar";
import { LogoutButton } from "@/components/LogoutButton";
import { NavLinks } from "@/components/NavLinks";

/**
 * Shell for every authenticated page (everything except /login).
 * proxy.ts already redirects unauthenticated requests away before this
 * ever renders, but each API route under app/api/** re-checks
 * hasValidSession() itself — see lib/auth.ts's file comment.
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-zinc-50 dark:bg-zinc-950">
      <header className="sticky top-0 z-20 border-b border-zinc-200 bg-white/95 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 pt-3">
          <span className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Jarvis
          </span>
          <LogoutButton />
        </div>
        <div className="mx-auto max-w-5xl px-4 pt-2">
          <NavLinks />
        </div>
        <div className="mx-auto max-w-5xl px-4 py-3">
          <AskJarvisBar />
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
