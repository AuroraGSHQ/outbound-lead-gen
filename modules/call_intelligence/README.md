# Call Intelligence

Turns any call — AI-dialer, your Zoom, or your manual phone calls — into a
structured summary and, when relevant, a deadline that gets tracked and
nagged.

## Inputs (three call sources, one pipeline)
- `ai_outbound` — transcript comes free from Vapi/Retell on call end
- `zoom` — Zoom's recording-completed webhook → pull the transcript
  (Zoom's own cloud transcript, or run the recording through the same
  summarizer)
- `phone_manual` — route your outgoing/incoming business calls through a
  Twilio recorded line so there's a transcript to work with; this is the
  assumption baked into this scaffold unless you'd rather handle it
  differently

## Processing
One Claude API call per finished call:
- Input: transcript
- Output (structured): `summary`, `agreed_items` (list), `deadline_mentioned`
  (date or null)

## Writes
- `call_logs` row (if not already created by the dialer) with `summary`,
  `agreed_items`, `deadline_mentioned`
- If a deadline was mentioned: new row in `deadlines`

## Daily job
Cron once a day: `SELECT * FROM deadlines WHERE due_date <= today AND
completed = false` → fires a `deadline_due` notification for each, and
increments `reminded_count`. Stop escalating (or change the message) after
some number of reminders so it doesn't nag into silence.
