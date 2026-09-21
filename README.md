# Aurora Growth OS

A small team's operations platform, built to run a home-services growth
agency (or the business behind it) with about ten people instead of fifty.
Eight always-on agents — sourcing, a broken-funnel scanner, outbound +
conversation, client intake, referrals, ads planning, measurement, and
notifications — do the repeatable work; a person approves anything that
actually leaves the building. One dashboard tracks all of it, and installs
to your phone and computer like an app (see **Install it like an app**
below).

At its core is the same outbound engine this repo started as: it finds
prospects matching your Ideal Customer Profile, drafts personalized cold
emails, carries the reply conversation, gets calls booked on your calendar,
and keeps the team in the loop the whole way — with every outbound message
queued for approval by default.

The default config (`config/icp.example.yaml`) is tuned for a post-construction
cleanup business targeting general contractors, builders, remodelers,
developers, property managers, and real estate agents — the B2B relationships
that actually generate repeat work in that trade. This targets businesses,
not homeowners: Apollo (the lead-sourcing data source) is a B2B contact
database, so it structurally can't reach individual homeowners, and cold
emailing them wouldn't be the right channel even if it could — that's Google
Business Profile / Local Services Ads / Yelp territory, a different system
than this one.

## What it actually does

1. **Sources leads** — searches Apollo.io on a schedule using the filters in
   `config/icp.yaml` (industry, title, company size, location), scores each
   candidate against your ICP, and stores the ones worth contacting.
2. **Drafts outreach** — Claude writes a short, specific first-touch email
   per lead (no template-y filler), which lands in a review queue.
3. **You approve** — a small web UI lists every pending draft; you can edit
   the body inline before it sends, or reject it. (Flip `APPROVAL_MODE` to
   `autonomous` in `.env` if you want it to send without review — not the
   default, and not recommended until you trust the drafts.)
4. **Holds the conversation** — polls Gmail for replies, classifies intent
   (interested / objection / not interested / wants info / out-of-office /
   unsubscribe), and drafts the next reply — including your Calendly link
   once someone's interested. Unsubscribe requests are honored immediately,
   always, regardless of approval mode.
5. **Gets meetings on the calendar** — a Calendly webhook marks the lead as
   `meeting_booked` the moment they book.
6. **Keeps you posted** — a daily email digest (new hot leads, drafts
   waiting on you, replies that need your eyes, meetings booked), plus an
   immediate ping the moment a meeting gets booked.

## Why not fully autonomous by default

Cold-emailing real people and having an LLM freelance the conversation with
zero review is how you end up with an embarrassing reply thread or a
CAN-SPAM complaint. Draft-and-approve keeps a human in the loop on every
send until you've seen enough drafts to trust it — then it's a one-line
config change to loosen that per-conversation (`autopilot` flag) or globally
(`APPROVAL_MODE=autonomous`).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                      # fill in real values
cp config/icp.example.yaml config/icp.yaml # define who your best clients are

python scripts/gmail_auth.py              # one-time Gmail OAuth (see docs/SETUP.md)
python scripts/run_sourcing_once.py       # sanity-check Apollo + ICP config
python scripts/run_digest_once.py         # sanity-check Gmail sending

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000`. On first run, log in with
`OWNER_UI_USERNAME` / `OWNER_UI_PASSWORD` from `.env` — that seeds one real
`owner` account. From **Users** (owner-only), create an account for each of
the other nine people with the role that matches what they do (`sales`,
`marketing`, or `ops`; `owner` sees everything). Those two env vars aren't
read again after that first account exists.

Full setup (Google Cloud OAuth client, Apollo key, Calendly webhook,
Anthropic key, and deployment options) is in **[docs/SETUP.md](docs/SETUP.md)**.

## Install it like an app

The dashboard is a installable web app (PWA) — no app-store account needed:

- **Phone (iOS/Android):** open the dashboard URL in the browser, then
  Share → "Add to Home Screen" (iOS Safari) or the browser menu → "Install
  app" (Android Chrome). It launches full-screen with its own icon.
- **Desktop (Chrome/Edge):** an install icon appears in the address bar, or
  use the browser menu → "Install Aurora Growth OS."

This requires the app to be served over HTTPS from a real domain (see
Deployment in docs/SETUP.md) — `localhost` works for testing the install
prompt but a phone needs a reachable HTTPS URL.

## The AI team

Eight agents, each backed by a real scheduled job (`app/scheduler.py`) — see
them and their cadence on the **Team** page once the app is running:

| Agent | Does | Manual section |
|---|---|---|
| **Scout** | Sources leads from Apollo against your ICP daily; Vibe Prospecting sourcing is a person-run request/import loop (see below) | — |
| **Scanner** | Passive checks on prospect sites (speed, mobile, tracking, click-to-call); flags what a human still has to verify | §8 |
| **Scribe** | Drafts first-touch/follow-up emails, polls replies, classifies intent | §9 |
| **Concierge** | Turns discovery-call notes into a client record + action plan | new |
| **Connector** | Watches for 90-day reviews and drafts the referral ask | §11 |
| **Promoter** | Generates the monthly ad campaign brief from the budget calendar | §4/§6/§14 |
| **Analyst** | Computes the six KPIs daily; drafts the quarterly benchmark extract | §12/§15 |
| **Courier** | Daily digest + immediate pings (meeting booked, etc.) | — |

Every agent that could produce something client-facing (an email, an ad,
outreach copy) queues it in **Approvals** or the **Actions** board rather
than sending/publishing it — see Architecture below.

## Sourcing with Vibe Prospecting

Apollo sourcing (Scout) runs on its own every morning, no one has to touch
it. Vibe Prospecting (Explorium data — intent topics, tech stack, job-change
events, richer than Apollo's filters) can't run that way: its own rules
require a human to see the cost in credits and explicitly confirm before
every export, on purpose, so nothing spends money unattended. So it's a
request/fulfill loop instead of a cron job:

1. On the **Sourcing** page, describe who you're looking for (industry, job
   titles, location, company size/revenue, keywords) — this creates a
   sourcing request and a ready-to-paste prompt.
2. Paste that prompt into a Claude session that has the Vibe Prospecting
   connector (Claude Code, claude.ai, wherever it's enabled for your
   account). Review the cost estimate it shows you, confirm, and it hands
   you a CSV download link.
3. Back on the request's page, upload that CSV. You'll map its columns to
   lead fields (a best-effort mapping is pre-selected — check it) and
   import. Imported leads are scored against `config/icp.yaml` exactly like
   Apollo leads and enter the same pipeline from there — drafts, approval
   queue, replies, the works.

## Architecture

```
config/icp.yaml                 → who counts as a "best client" and how they're scored
config/ads_budget_calendar.yaml → the quarterly ad spend/allocation plan (manual §14)
config/ad_targeting.yaml        → audience segments + creative angles (manual §5/§6)
app/integrations/               → thin clients: Apollo, Gmail, Calendly, Claude, site checks, ad platforms
app/services/                   → the agents: sourcing, outreach, conversation, approvals,
                                   intake, actions, scanner, referrals, ads, metrics, content, notify
app/web/auth.py                 → session login + role-based access
app/scheduler.py                → cron-style jobs that run every agent, plus the team roster
app/main.py                     → the whole dashboard (FastAPI) + Calendly webhook
```

Every outbound send — first touch, reply, follow-up, or one drafted by the
Concierge/Scanner agents — funnels through
`app/services/approvals.py::send_message`, which is the one place that talks
to Gmail's send API and updates lead/message state. That's also the one spot
to look if you ever need to audit "what did this actually send."

Every other agent-proposed next step (schedule a reminder, run a scan, ask
for referrals, review an ad brief) is an `ActionItem`
(`app/services/actions.py`) — auto-run when there's a safe handler, a
checklist item on the **Actions** board otherwise. `auto_executable` only
means "the system knows how," never "it ships without review": anything
client-facing still lands in the approval queue above.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover the ICP scoring logic and Calendly webhook signature
verification — the two places where a subtle bug would be expensive (wrong
scoring = wasting your best-lead budget on bad targets; a broken signature
check = an open webhook endpoint).
