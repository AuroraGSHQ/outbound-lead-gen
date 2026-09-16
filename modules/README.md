# JARVIS — Aurora Outbound & Client-Ops System

This is not a separate app from the Aurora marketing agent site — it's four
modules that plug into it. All four read and write the same tables (`schema.sql`),
so they can be built **in parallel**, by different Claude Code sessions or
different devs, without stepping on each other. That's the whole point of this
scaffold: it's the shared substrate that makes "all at once" actually work,
instead of four modules quietly assuming different things about what a
"contact" or a "call" is.

If you already have a database for the Aurora agent site, add these tables to
it rather than standing up a second database. Same for the API-key settings
page you already planned — every credential below (`.env.example`) should be
entered there, not hardcoded.

## The four modules

| Module | Does | Talks to |
|---|---|---|
| `dialer` | Segmented AI outbound calling (prospects, current/past clients, callbacks) | Twilio + Vapi/Retell |
| `call_intelligence` | Summarizes any call (AI dialer, Zoom, or your manual phone calls), extracts deadlines, nags you when they're overdue | Anthropic API, Zoom API, Twilio recording |
| `contracts` | Auto-fills your uploaded template on close, holds for your approval, sends on your yes | PandaDoc or DocuSign API |
| `notifications` | One channel for everything that needs your attention: payments, overdue deadlines, contracts awaiting approval, flagged messages | Stripe webhook, Twilio SMS |

## Build order, honestly

You can build all four in parallel starting today. The only hard dependency
is `schema.sql` existing first — that's a 10-minute step, not a phase. Once
it's applied, nothing blocks anything else.

## Where this lives

Inside the Aurora marketing agent repo/site (this repo), as a new set of
backend routes + a scheduled worker (for the dialer's call windows and the
daily deadline check). Not a separate app, not a separate login — same
single-user dashboard (`app/main.py` + `app/scheduler.py`).

## Compliance note (read once, build in, forget about it)

Add a `do_not_call` boolean check to every outbound dial *before* the call
is placed — see `contacts.do_not_call` in the schema. Costs nothing to build
in now; costs a lot to retrofit after a complaint.

## How the four modules plug in (integration contract)

`schema.sql` (repo root) is already applied automatically on startup by
`app/db.py::init_db()`. Settings for all four modules already exist in
`app/config.py` / `.env.example` (Twilio, Vapi/Retell, Zoom, PandaDoc/
DocuSign, Stripe, owner phone). Don't add new top-level settings without
checking there first — most of what you need is already named.

Each module is a self-contained package: `modules/<name>/`. To keep four
people building at once from stepping on the same shared files
(`app/main.py`, `app/scheduler.py`), each module exposes exactly two things,
and nothing outside `modules/<name>/` gets edited by that module's build:

- `modules/<name>/router.py` — a `fastapi.APIRouter` named `router`, already
  given its own `prefix` (e.g. `/dialer`, `/contracts`) and `tags`. Mounted
  into `app/main.py` via `app.include_router(router)` — one line, added
  centrally, not by each module.
- `modules/<name>/jobs.py` — a function `register_jobs(scheduler: BackgroundScheduler) -> None`
  that calls `scheduler.add_job(...)` for whatever recurring work that module
  needs (the dialer's call window, call-intelligence's daily deadline sweep,
  etc.). Wired centrally into `app/scheduler.py::start_scheduler()` with one
  call per module.

Use `modules/common/db.py::db_conn()` for raw SQL against the schema.sql
tables (contacts, call_logs, deadlines, contracts, notifications) — it reuses
the same SQLAlchemy engine/connection as the rest of the app, same database
file. Use `modules/common/notifications.py::create_notification(...)` to
raise a notification instead of writing your own INSERT — every module
(dialer, call_intelligence, contracts) should call this; only the
`notifications` module owns delivery.

Do not import from another `modules/<name>/` package — if you need something
another module produces, read it off the shared tables, not from their code.
