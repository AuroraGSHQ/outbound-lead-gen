# Setup guide

## 1. Anthropic API key (drafting + reply classification)

Get a key from [console.anthropic.com](https://console.anthropic.com), set
`ANTHROPIC_API_KEY` in `.env`. `ANTHROPIC_MODEL` defaults to
`claude-sonnet-5` — leave it unless you have a reason to change it.

## 2. Apollo.io (lead sourcing)

Sign up at [apollo.io](https://www.apollo.io), grab an API key from
Settings → Integrations → API, set `APOLLO_API_KEY`. Their free tier has
limited credits/month, which is plenty to start — the bot respects
`DAILY_OUTREACH_CAP` so it won't burn through your quota unexpectedly.

## 3. Gmail (sending + reading replies)

The bot sends *as your real Gmail/Workspace address* via OAuth — recipients
see a normal email from you, not a third-party sending domain.

1. Go to [Google Cloud Console](https://console.cloud.google.com), create a
   project (or reuse one).
2. **APIs & Services → Library** → enable the **Gmail API**.
3. **APIs & Services → OAuth consent screen** → set it up as "Internal" if
   you're on Workspace, or "External" + add yourself as a test user if
   personal Gmail.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   → Application type: **Desktop app**.
5. Download the JSON, save it as `data/credentials.json`.
6. Run `python scripts/gmail_auth.py` — it opens a browser, you sign in and
   approve, and it writes `data/token.json`. That's the file the running
   bot actually uses; the OAuth client JSON is only needed for this one-time
   step (and again if you ever delete the token).
7. Set `GMAIL_SENDER_EMAIL` in `.env` to the address you authorized.

If you outgrow polling (10-minute default) and want near-instant reply
detection, Gmail supports push notifications via Cloud Pub/Sub
(`users.watch`) — not implemented here to keep the setup simple, but
`app/integrations/gmail.py` is the place to add it.

## 4. Calendly (scheduling)

1. Get your booking link (e.g. `https://calendly.com/you/intro-call`), set
   `CALENDLY_BOOKING_LINK`.
2. Create a webhook subscription for the `invitee.created` event pointing at
   `https://<your-deployed-host>/webhooks/calendly` — either via the
   [Calendly API](https://developer.calendly.com/api-docs/) or a Zapier-free
   direct POST to their webhook subscription endpoint. Calendly gives you a
   signing key when you create the subscription — set that as
   `CALENDLY_WEBHOOK_SIGNING_KEY`.
3. The webhook endpoint only works once the app is reachable from the
   internet (see Deployment below) — Calendly can't reach `localhost`.

## 5. Business identity

Fill in `BUSINESS_NAME`, `BUSINESS_PITCH` (one or two sentences — this goes
straight into the cold email prompt, so make it specific), `SENDER_NAME`,
`SENDER_TITLE`, and `BUSINESS_PHYSICAL_ADDRESS` (required by CAN-SPAM in the
footer of every commercial email).

## 6. Your ICP

Copy `config/icp.example.yaml` to `config/icp.yaml` and edit it — this is
the single file that defines who your "best clients" are. See the comments
in that file for what each field does. `min_score_to_contact` is the actual
gate: leads scored below it are stored (so you can see what's out there
without emailing them) but never queued for outreach.

## 7. Owner alerts

Set `OWNER_EMAIL` to where you want the daily digest and meeting-booked
alerts sent (defaults can just be your own address).

## Deployment

This is a normal FastAPI app plus a background scheduler running in the
same process — the simplest deployment is one long-running process:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- **VM / systemd**: run the above under a systemd unit (or `screen`/`tmux`
  for a quick test), put a reverse proxy (Caddy/nginx) with TLS in front of
  it so Calendly's webhook can reach it over HTTPS.
- **Docker**: no Dockerfile is included yet — it's a straightforward
  `python:3.12-slim` + `pip install -r requirements.txt` + the uvicorn
  command above; ask if you want one written.
- **Data**: defaults to SQLite at `data/leadgen.db`. Set `DATABASE_URL` to a
  Postgres URL for anything beyond single-user local testing — the code
  uses SQLAlchemy so no code changes are needed, just the connection string.

## Compliance notes (read this before turning on real sending)

- Every outbound email includes your physical address and a one-line
  unsubscribe instruction (`app/services/approvals.py::compose_footer`) —
  required by CAN-SPAM for commercial email in the US. If you're emailing
  into the EU/UK, GDPR has stricter consent requirements for cold outreach
  than the US does — get advice specific to your situation before scaling
  volume there.
- Unsubscribe requests are detected and honored immediately and
  automatically, bypassing the approval queue on purpose — see
  `app/services/conversation.py`.
- `DAILY_OUTREACH_CAP` exists both for deliverability (a brand-new sending
  address blasting hundreds of cold emails a day gets flagged as spam fast)
  and so a bug can't accidentally email your entire lead list in one run.
