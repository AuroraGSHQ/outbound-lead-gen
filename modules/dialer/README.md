# Dialer

Segmented outbound AI voice calling. Not 24/7 — runs on a scheduled window
(e.g. business hours) at whatever concurrency your Twilio/Vapi plan supports.

## Reads
- `contacts` where `do_not_call = false` AND `last_contacted_at` is outside
  your cooldown window, filtered by `segment` and `service_line`.

## Per-segment behavior
Each segment needs its own call objective/script, not one generic pitch:
- `prospect` — discovery + pitch, picks marketing or junk_removal (or both)
  framing based on `service_line`
- `current_client` — check-in / upsell
- `past_client` — re-engagement
- `callback_requested` — the specific ask they said to follow up on (pull
  from their last `call_logs.summary`)

## Writes
- New row in `call_logs` (`call_type = 'ai_outbound'`, transcript, outcome,
  recording_url)
- Updates `contacts.last_contacted_at`

## Core logic to build
1. Query eligible contacts per segment per run window
2. Place call via Twilio, hand the leg to Vapi/Retell with the segment's
   script/prompt
3. On call end, log outcome + transcript
4. No-answer → retry logic (e.g. 3 attempts, spaced out) before moving on
5. **Check `do_not_call` immediately before dialing, not just at query time**
   — a contact can flip that flag between query and dial in a long run
