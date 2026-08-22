# Outbound Lead-Gen Bot

An outbound sales bot: it finds prospects matching your Ideal Customer
Profile, drafts personalized cold emails, carries the reply conversation,
gets calls booked on your calendar, and keeps you in the loop the whole way
— with every outbound message queued for your approval by default.

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

Then open `http://localhost:8000` (basic auth: `OWNER_UI_USERNAME` /
`OWNER_UI_PASSWORD` from `.env`) to see the dashboard and approval queue.

Full setup (Google Cloud OAuth client, Apollo key, Calendly webhook,
Anthropic key, and deployment options) is in **[docs/SETUP.md](docs/SETUP.md)**.

## Architecture

```
config/icp.yaml          → who counts as a "best client" and how they're scored
app/integrations/        → thin clients: Apollo, Gmail, Calendly, Claude
app/services/            → the actual pipeline logic (sourcing, drafting,
                            conversation handling, approvals, owner notifications)
app/scheduler.py         → cron-style jobs that run the pipeline
app/main.py              → approval UI + Calendly webhook (FastAPI)
```

Every outbound send — first touch, reply, follow-up — funnels through
`app/services/approvals.py::send_message`, which is the one place that talks
to Gmail's send API and updates lead/message state. That's also the one spot
to look if you ever need to audit "what did this bot actually send."

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover the ICP scoring logic and Calendly webhook signature
verification — the two places where a subtle bug would be expensive (wrong
scoring = wasting your best-lead budget on bad targets; a broken signature
check = an open webhook endpoint).
