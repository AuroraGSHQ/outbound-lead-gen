# Sync (CRM Auto-Maintenance)

Keeps contacts, calls, and emails current automatically — nobody has to
manually move a contact to "current_client" or notice a deal closed. This
is the reconciliation layer: it watches what the other four modules produce
and keeps the CRM honest, instead of contacts quietly going stale.

## Triggers → what it updates

**On any `call_logs` insert/update:**
- `contacts.last_contacted_at = now()`
- `outcome = 'closed'` → `contacts.segment = 'current_client'`
- `outcome = 'callback_requested'` → `contacts.segment = 'callback_requested'`

**On every inbound/outbound email (business + personal inbox, via Gmail API):**
- Logged to `emails`, matched to a `contact_id` by email address
- No match found → still logged, `flagged = true` as a possible new lead
  instead of silently dropped
- Claude classifies whether it needs a reply → flagged emails feed the
  `notifications` module's `missed_message` type

**On contract signed:**
- Confirms `contacts.segment = 'current_client'` — belt-and-suspenders in
  case the call outcome didn't already set it

**Weekly staleness pass:**
- Any `prospect` untouched for 60+ days gets re-enriched via Vibe
  Prospecting (catches a moved business, new decision-maker, closed
  location) instead of going stale silently
- Any contact with missing business fields gets a first enrichment attempt
  on creation, then another pass here if still incomplete

## Reads / writes
Reads `call_logs`, `emails`, `contracts` as they change. Writes to
`contacts` (segment, `last_contacted_at`) and `emails`.

## Needs
- Shared DB access (same database as every other module)
- `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REFRESH_TOKEN` for both
  inboxes you want monitored
- Vibe Prospecting connector — already in use for initial research, reused
  here for re-enrichment
