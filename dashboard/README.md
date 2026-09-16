# Dashboard (the "terminal")

This is where you actually see and control the whole system from your
phone or computer — a responsive web app, not a native app and not a
literal command line.

## Setup

```bash
cd dashboard
npm install
cp .env.example .env.local   # fill in real values
npm run dev
```

Requires `data/leadgen.db` (repo root) to already exist — run the Python
backend once (`python -m app.db` equivalent, or just start `uvicorn
app.main:app`, which calls `init_db()` on startup) so `schema.sql` has been
applied before starting the dashboard.

Open `http://localhost:3000/login`.

## Stack
Next.js (App Router, TypeScript) — dashboard pages and API routes live in
one deployable app, same repo as everything else. `better-sqlite3` reads/
writes the exact same database file the Python backend (`../app`,
`../modules`) uses; see `lib/db.ts`.

**Next.js 16 note**: this scaffold uses `proxy.ts`, not `middleware.ts` —
the latter was renamed and is deprecated in this version. See `proxy.ts`'s
file comment before touching auth gating.

## Pages
- `/login` — single username/password gate, this is a one-person system
- `/` — home: notifications feed, quick stats (open deadlines, contracts
  awaiting approval, calls today)
- `/contacts` — browse/search/filter the CRM by segment and service_line
- `/calls` — call log with summaries, filterable by contact/date/outcome
- `/deadlines` — upcoming + overdue, mark complete
- `/contracts` — approval queue: view filled contract, approve or reject
- `/settings` — API keys (already planned) + contract template upload

## "Ask Jarvis" command bar
One input field, always visible. Whatever you type goes to an API route
that hands it to Claude along with read access to the DB: it either
answers directly ("how many prospects are in the marketing segment") or
triggers an action ("call the Smith account now", "mark the Turner
deadline done"). This is where text/voice control lives on the dashboard
side — the outbound phone calls themselves are handled by the `dialer`
module, not this command bar.

## Making it feel like an app on your phone
Add a `manifest.json` + icon set + minimal service worker (PWA). Then
"Add to Home Screen" on iOS/Android gives you a real app icon and
full-screen launch — no App Store, no review process, no separate
iOS/Android codebases. Updates ship the instant you deploy.

## Auth
A single env-var-based username/password check plus a simple signed
session cookie (`lib/auth.ts`, `proxy.ts`) is genuinely enough here. This
is a one-person system — don't build multi-tenant auth for it.

## Deployment split
- Dashboard pages + API routes → Vercel or similar. Pure request/response,
  no long-running process needed.
- Background workers (dialer's call scheduler, daily deadline check,
  weekly staleness sweep from `sync`) → need to keep running when nobody
  has the dashboard open. Serverless platforms spin down between requests,
  so these run on a small persistent host instead — Railway, Render, or
  Fly.io (this is the existing Python `app/` + `modules/`, unchanged).
- Both sides connect to the same database. Today that's the SQLite file at
  `../data/leadgen.db` (dev default on both sides); moving to a shared
  Postgres in production means adapting `schema.sql` (already flagged
  SQLite-flavored in its own header) and swapping `lib/db.ts`'s
  `better-sqlite3` client for a Postgres one — nothing else in this app
  should need to change if the query layer stays isolated to `lib/db.ts`
  and `app/api/**`.

Infra cost here lands around $30-50/month total. The real spend is
usage-based: Twilio minutes, Vapi/Retell minutes, Claude API calls.
