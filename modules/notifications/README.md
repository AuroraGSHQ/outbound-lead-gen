# Notifications

The one channel everything else feeds into. This is what makes the system
feel like "Jarvis" instead of four separate tools you have to check
individually.

## Triggers → notification type
- Stripe webhook (`payment_intent.succeeded`) → `payment_received`
- Call-intelligence daily job → `deadline_due`
- Contracts module, on fill → `contract_needs_approval`
- Call-intelligence, on any call processed → `call_summary_ready`
- Inbox/text monitor (if/when built) flags something → `missed_message`

## Writes
New row in `notifications` for every trigger above.

## Delivery
Simplest: Twilio SMS to your phone, one message per notification (or
batched, your call on how noisy you want it). If the Aurora agent site adds
push notifications later, swap the channel without touching anything
upstream — every module above only needs to write a `notifications` row,
not know how it gets delivered.
