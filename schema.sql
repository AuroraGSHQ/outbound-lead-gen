-- JARVIS module tables: dialer, call-intelligence, contracts, notifications.
--
-- These live in the SAME database as the existing Aurora agent tables
-- (leads/conversations/messages/meetings, defined via SQLAlchemy in
-- app/models.py) — see DATABASE_URL in .env. This file is applied once,
-- automatically, by app/db.py::init_db() after the ORM tables are created,
-- via executescript, so every statement here must be safe to re-run
-- (IF NOT EXISTS everywhere).
--
-- Written SQLite-flavored to match the default `sqlite:///./data/leadgen.db`.
-- If you move to Postgres, adapt AUTOINCREMENT -> IDENTITY/SERIAL and the
-- TEXT-as-JSON columns to JSONB before applying.

-- ---------------------------------------------------------------------
-- contacts: the dialer/call-intelligence/contracts world's address book.
-- Separate from `leads` (cold Apollo prospects in the email pipeline) —
-- a contact here is anyone you might call: a prospect, a current or past
-- client, or someone who asked for a callback. The two tables are not
-- merged automatically; if a lead becomes a phone relationship, create a
-- matching contacts row (optionally linking lead_id) yourself.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    company_name TEXT NOT NULL DEFAULT '',
    segment TEXT NOT NULL DEFAULT 'prospect'
        CHECK (segment IN ('prospect', 'current_client', 'past_client', 'callback_requested')),
    service_line TEXT NOT NULL DEFAULT '',  -- e.g. 'marketing', 'junk_removal', or 'marketing,junk_removal'
    do_not_call INTEGER NOT NULL DEFAULT 0 CHECK (do_not_call IN (0, 1)),
    last_contacted_at TEXT,                 -- ISO8601 UTC
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_contacts_segment ON contacts(segment);
CREATE INDEX IF NOT EXISTS idx_contacts_do_not_call ON contacts(do_not_call);
CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone);

-- ---------------------------------------------------------------------
-- call_logs: one row per finished call, regardless of source.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS call_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    call_type TEXT NOT NULL CHECK (call_type IN ('ai_outbound', 'zoom', 'phone_manual')),
    direction TEXT NOT NULL DEFAULT 'outbound' CHECK (direction IN ('outbound', 'inbound')),
    external_call_id TEXT NOT NULL DEFAULT '',   -- Twilio Call SID / Vapi-Retell call id / Zoom meeting id
    transcript TEXT NOT NULL DEFAULT '',
    recording_url TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',             -- e.g. 'completed', 'no_answer', 'voicemail', 'closed', ...
    summary TEXT NOT NULL DEFAULT '',
    agreed_items TEXT NOT NULL DEFAULT '[]',       -- JSON array of strings
    deadline_mentioned TEXT,                       -- ISO8601 date, or NULL
    processed_at TEXT,                             -- set once call-intelligence has summarized it
    started_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_call_logs_contact ON call_logs(contact_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_call_type ON call_logs(call_type);
CREATE INDEX IF NOT EXISTS idx_call_logs_processed_at ON call_logs(processed_at);
CREATE INDEX IF NOT EXISTS idx_call_logs_external_call_id ON call_logs(external_call_id);

-- ---------------------------------------------------------------------
-- deadlines: extracted from a call, tracked and nagged until done.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deadlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_log_id INTEGER REFERENCES call_logs(id) ON DELETE SET NULL,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    description TEXT NOT NULL DEFAULT '',
    due_date TEXT NOT NULL,                        -- ISO8601 date
    completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
    reminded_count INTEGER NOT NULL DEFAULT 0,
    last_reminded_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_deadlines_due_date ON deadlines(due_date);
CREATE INDEX IF NOT EXISTS idx_deadlines_completed ON deadlines(completed);

-- ---------------------------------------------------------------------
-- contracts: auto-filled on close, never sent without explicit approval.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    call_log_id INTEGER REFERENCES call_logs(id) ON DELETE SET NULL,
    template_id TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'pandadoc' CHECK (provider IN ('pandadoc', 'docusign')),
    external_document_id TEXT NOT NULL DEFAULT '', -- PandaDoc/DocuSign document id once created
    filled_data TEXT NOT NULL DEFAULT '{}',         -- JSON: price, scope, deadline, contact fields, ...
    status TEXT NOT NULL DEFAULT 'pending_approval'
        CHECK (status IN ('pending_approval', 'approved', 'rejected', 'sent', 'signed')),
    rejection_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    sent_at TEXT,
    signed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_contracts_status ON contracts(status);
CREATE INDEX IF NOT EXISTS idx_contracts_contact ON contracts(contact_id);

-- ---------------------------------------------------------------------
-- notifications: the one channel everything else feeds into. Any module
-- may INSERT here; only the notifications module reads/delivers/marks
-- these delivered. See modules/common/notifications.py for the helper
-- every other module should call instead of writing raw INSERT SQL.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN (
        'payment_received', 'deadline_due', 'contract_needs_approval',
        'call_summary_ready', 'missed_message'
    )),
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',   -- JSON: free-form extra context (ids, amounts, links, ...)
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    delivered INTEGER NOT NULL DEFAULT 0 CHECK (delivered IN (0, 1)),
    delivered_at TEXT,
    delivery_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_notifications_delivered ON notifications(delivered);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications(type);

-- ---------------------------------------------------------------------
-- emails: every inbound/outbound message across both watched inboxes
-- (business + personal), logged by the `sync` module. Matched to a
-- contact by address when possible; unmatched messages are kept and
-- flagged instead of dropped, since an unmatched sender is often a new
-- lead nobody logged yet.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    inbox TEXT NOT NULL CHECK (inbox IN ('business', 'personal')),
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    gmail_message_id TEXT NOT NULL DEFAULT '',
    gmail_thread_id TEXT NOT NULL DEFAULT '',
    from_address TEXT NOT NULL DEFAULT '',
    to_address TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    flagged INTEGER NOT NULL DEFAULT 0 CHECK (flagged IN (0, 1)),   -- no matching contact -> possible new lead
    needs_reply INTEGER NOT NULL DEFAULT 0 CHECK (needs_reply IN (0, 1)),
    notified INTEGER NOT NULL DEFAULT 0 CHECK (notified IN (0, 1)), -- missed_message notification already fired
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_emails_contact ON emails(contact_id);
CREATE INDEX IF NOT EXISTS idx_emails_gmail_message_id ON emails(gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_emails_flagged ON emails(flagged);
CREATE UNIQUE INDEX IF NOT EXISTS idx_emails_inbox_message_unique ON emails(inbox, gmail_message_id);

-- ---------------------------------------------------------------------
-- Reconciliation triggers (the `sync` module's reason to exist as a
-- schema concept, not just application code): these fire on ANY insert/
-- update to call_logs or contracts, regardless of which module wrote the
-- row, so a contact's segment/last_contacted_at can never silently drift
-- out of sync with what actually happened on a call or a signature.
-- ---------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_call_logs_touch_contact
AFTER INSERT ON call_logs
WHEN NEW.contact_id IS NOT NULL
BEGIN
    UPDATE contacts
    SET last_contacted_at = COALESCE(NEW.started_at, NEW.created_at),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = NEW.contact_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_call_logs_outcome_closed
AFTER UPDATE OF outcome ON call_logs
WHEN NEW.contact_id IS NOT NULL AND NEW.outcome = 'closed' AND OLD.outcome IS NOT NEW.outcome
BEGIN
    UPDATE contacts
    SET segment = 'current_client', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = NEW.contact_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_call_logs_outcome_callback
AFTER UPDATE OF outcome ON call_logs
WHEN NEW.contact_id IS NOT NULL AND NEW.outcome = 'callback_requested' AND OLD.outcome IS NOT NEW.outcome
BEGIN
    UPDATE contacts
    SET segment = 'callback_requested', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = NEW.contact_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_contracts_signed_marks_current_client
AFTER UPDATE OF status ON contracts
WHEN NEW.contact_id IS NOT NULL AND NEW.status = 'signed' AND OLD.status IS NOT NEW.status
BEGIN
    UPDATE contacts
    SET segment = 'current_client', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = NEW.contact_id;
END;
