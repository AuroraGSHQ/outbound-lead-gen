# Setup guide

## 1. Anthropic API key (drafting + reply classification)

Get a key from [console.anthropic.com](https://console.anthropic.com), set
`ANTHROPIC_API_KEY` in `.env`. `ANTHROPIC_MODEL` defaults to
`claude-sonnet-5` — leave it unless you have a reason to change it.

## 2. Apollo.io (lead sourcing)

Sign up at [apollo.io](https://www.apollo.io), grab an API key from
Settings → Integrations → API, set `APOLLO_API_KEY`.

**API access requires a paid plan.** A free or Professional-Trial account
can use Apollo's own web UI to search for leads, but calling the search API
directly (what this bot does) gets rejected with `403 API_INACCESSIBLE` —
their error message points you at apollo.io/pricing. Confirmed against the
live API while building this: the auth itself (the request needs the key in
an `X-Api-Key` header, not the request body — some older docs/examples show
it the other way) works fine once you're on a plan that includes API access.
Check your current plan's API access before assuming a failure here is a
code bug.

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

## 8. Team accounts

`OWNER_UI_USERNAME`/`OWNER_UI_PASSWORD` seed exactly one `owner` account the
first time the app starts against an empty database — after that, log in
and use **Users** (owner-only) to create a real account for everyone else,
with the role matching what they actually do:

- `owner` — everything, including user management.
- `sales` — leads, intake, clients, outreach approvals.
- `marketing` — scanner, ads, content, metrics.
- `ops` — clients, referrals, the action-item board.

Everyone can view the dashboard, Team, and Metrics pages regardless of role.
There's also a CLI fallback if you'd rather not use the UI:

```bash
python scripts/create_user.py --name "Alex Rivera" --email alex@yourcompany.com --role sales
```

It'll prompt for a password interactively (never pass one on the command
line where it'd land in shell history).

## 9. Scanner agent (optional review-count check)

The Broken-Funnel Scanner (manual §8) runs its cheap/objective checks (page
load, mobile viewport, tracking pixels, click-to-call) against any domain
with no setup. If you also want the review-count/recency check, get a key
from [Google Cloud Console → Places API](https://console.cloud.google.com)
and set `GOOGLE_PLACES_API_KEY` — this integration point exists in
`app/integrations/site_checks.py` but isn't wired to Places yet; without a
key the scanner simply skips that one check and still runs everything else.

## 10. Ads agent (optional, advanced)

By default the Promoter agent generates a full campaign brief every month
(budget split, audience, ad copy) and hands it to you as a ready-to-paste
package — publishing stays a two-minute manual step in Google Ads / Meta Ads
Manager. This is deliberate: both platforms require a developer application
+ review before their APIs can create anything, so brief-only is the honest
default until you've done that.

If you've already completed both:
1. Google Ads: apply for a developer token, set up OAuth credentials, get a
   refresh token, then set `GOOGLE_ADS_DEVELOPER_TOKEN` (plus the
   client id/secret/refresh token/customer id your integration needs), add
   the `google-ads` package to `requirements.txt`, and implement
   `create_paused_campaign()` in `app/integrations/google_ads.py`.
2. Meta: get Marketing API access approved for your app, set
   `META_ACCESS_TOKEN` (plus app id/secret/ad account id), add the
   `facebook-business` package, and implement `create_paused_campaign()` in
   `app/integrations/meta_ads.py`.

Either way, campaigns should always be created **paused** — a person flips
them live, the same draft-and-approve principle as every send in this app.

## Sizing your outreach volume (read this before chasing an aggressive revenue target)

If the goal is a specific revenue number in a specific window, work the math
backwards instead of just cranking `DAILY_OUTREACH_CAP` up:

```
jobs needed           = revenue target / average job value
leads needed to book   = jobs needed / your close rate (booked call → job)
                          (guess 20-30% until you have real data)
calls needed to book   = leads needed to book / your call show/close rate
replies needed         = calls needed / reply-to-call rate (guess 30-50%)
emails needed to send  = replies needed / reply rate (cold B2B typically 2-8%)
```

Plug in your real average job value and close rate once you have a few data
points — the first two weeks of this running are themselves how you find out
those numbers, so don't over-trust a guess here.

**The hard constraint that actually caps how fast you can scale this isn't
Apollo credits or Claude tokens — it's Gmail deliverability.** Sending from a
real Gmail/Workspace address gets you much better reply rates than a bulk
sending platform, but it comes with real limits:

- Gmail personal accounts cap around 500 sends/day; Workspace around
  2,000/day — but you will get flagged as spam (and Google can suspend the
  account) *long* before that ceiling if a previously low-volume address
  suddenly starts sending 100+ cold emails a day.
- Ramp gradually: start at `DAILY_OUTREACH_CAP=15-25`, hold for a week
  watching your reply/bounce rate, then step up by ~20-30% every few days.
  Going from 25/day to 25/day for the first two weeks, then increasing, will
  get you to a sustainably higher volume faster than jumping straight to 100.
- Every bounce or spam complaint hurts sender reputation more than a normal
  send helps it — keep your lead list clean (Apollo email verification
  status, the `exclude_domains` list, sensible ICP filters) rather than
  maximizing raw volume.
- If the math above says you need more daily volume than Gmail can sustain
  even after ramping, the fix is a dedicated sending domain/mailbox (e.g. a
  second Workspace mailbox warmed up over a few weeks) run in parallel — not
  pushing one address past what it can safely carry. Ask if you want that
  built; it's a config change (a second `GMAIL_*` credential set + a second
  scheduler track) more than new code.

## Deployment

This is a normal FastAPI app plus a background scheduler running in the
same process — the simplest deployment is one long-running process:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- **VM / systemd**: run the above under a systemd unit (or `screen`/`tmux`
  for a quick test), put a reverse proxy (Caddy/nginx) with TLS in front of
  it so Calendly's webhook can reach it over HTTPS. This also matters for
  the PWA install: phones only offer "Add to Home Screen"/"Install app" for
  a page served over real HTTPS on a real domain, not for `localhost` or
  plain HTTP.
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
