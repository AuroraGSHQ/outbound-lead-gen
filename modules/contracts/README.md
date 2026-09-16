# Contracts

Template uploaded once. Auto-filled on close. Never sent without your
explicit approval.

## Setup (one-time)
Upload your contract template (docx or PandaDoc/DocuSign template) via the
settings page. Store its ID/reference — that's `contracts.template_id`.

## Trigger
When a contact's deal is marked closed (however "closed" gets set in your
CRM flow — likely a `call_logs.outcome = 'closed'` or a manual status flip),
create a `contracts` row:
- Pull fields from the `contacts` row + deal specifics (price, scope,
  deadline) into `filled_data`
- Fill the template (docxtpl if self-hosted merge, or PandaDoc/DocuSign API
  if using their template-fill endpoint)
- Set `status = 'pending_approval'`
- Fire a `contract_needs_approval` notification

## Your approval step
You see the filled doc in the dashboard. Two outcomes:
- Approve → `status = 'approved'` → send via PandaDoc/DocuSign e-sign →
  `status = 'sent'` (later `'signed'` on their webhook callback)
- Reject → `status = 'rejected'`, logged, nothing sent

No path in this module sends a contract without that explicit approve step.
