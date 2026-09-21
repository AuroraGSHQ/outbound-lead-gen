from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeadStatus(str, enum.Enum):
    NEW = "new"                        # sourced, not yet scored above threshold
    QUEUED = "queued"                  # scored high enough, waiting on first draft
    CONTACTED = "contacted"            # first-touch email sent
    REPLIED = "replied"                # prospect replied, awaiting our next move
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    MEETING_BOOKED = "meeting_booked"
    CLOSED_LOST = "closed_lost"
    UNSUBSCRIBED = "unsubscribed"


class MessageDirection(str, enum.Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class MessageStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    REJECTED = "rejected"
    RECEIVED = "received"  # inbound message from the prospect; not part of the approval flow


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(255), default="")
    industry: Mapped[str] = mapped_column(String(255), default="")
    company_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str] = mapped_column(String(255), default="")

    contact_name: Mapped[str] = mapped_column(String(255), default="")
    contact_title: Mapped[str] = mapped_column(String(255), default="")
    contact_email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    linkedin_url: Mapped[str] = mapped_column(String(500), default="")

    source: Mapped[str] = mapped_column(String(50), default="apollo")
    icp_score: Mapped[float] = mapped_column(Float, default=0.0)
    score_reasons: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(50), default=LeadStatus.NEW.value)
    unsubscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="lead")
    meetings: Mapped[list["Meeting"]] = relationship(back_populates="lead")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    gmail_thread_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    status: Mapped[str] = mapped_column(String(50), default="active")
    # If true, replies in this thread are drafted AND sent without owner
    # approval. Off by default; the owner opts a thread in from the UI.
    autopilot: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    lead: Mapped["Lead"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    direction: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default=MessageStatus.DRAFT.value)
    gmail_message_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    detected_intent: Mapped[str] = mapped_column(String(50), default="")
    reviewer_note: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    calendly_event_uri: Mapped[str] = mapped_column(String(500), default="")
    scheduled_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="scheduled")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    lead: Mapped["Lead"] = relationship(back_populates="meetings")


class DigestState(Base):
    """Tracks when we last sent the owner a digest, so we don't re-report items."""

    __tablename__ = "digest_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Aurora Growth OS: agents, users, and the client-lifecycle layer on top of
# the outbound engine above.
# ---------------------------------------------------------------------------


class UserRole(str, enum.Enum):
    OWNER = "owner"
    SALES = "sales"
    MARKETING = "marketing"
    OPS = "ops"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default=UserRole.OPS.value)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Hephaestus (system health) sends an immediate alert to every owner
    # plus anyone with this flag on, rather than waiting for the daily
    # digest — see services/system_health.py.
    notify_system_alerts: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClientStatus(str, enum.Enum):
    PROSPECT = "prospect"
    ACTIVE = "active"
    PAUSED = "paused"
    CHURNED = "churned"


class Client(Base):
    """A won (or being-won) account. Created by the intake agent right after
    a discovery call, promoted to ACTIVE once the contract is signed."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), nullable=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(50), default=ClientStatus.PROSPECT.value)
    tier: Mapped[str] = mapped_column(String(100), default="")
    monthly_fee: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    referral_source: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    lead: Mapped["Lead | None"] = relationship()
    intake_calls: Mapped[list["IntakeCall"]] = relationship(back_populates="client")
    referrals: Mapped[list["ReferralRecord"]] = relationship(back_populates="client")


class IntakeCall(Base):
    """Raw + Claude-structured notes from a discovery/intro call. The
    Concierge agent's source record."""

    __tablename__ = "intake_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), nullable=True, index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    raw_notes: Mapped[str] = mapped_column(Text, default="")
    structured: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    lead: Mapped["Lead | None"] = relationship()
    client: Mapped["Client | None"] = relationship(back_populates="intake_calls")
    created_by: Mapped["User | None"] = relationship()


class ActionCategory(str, enum.Enum):
    INTAKE_FOLLOWUP = "intake_followup"
    OUTREACH = "outreach"
    SCANNER_VERIFY = "scanner_verify"
    REFERRAL = "referral"
    ADS = "ads"
    CONTENT = "content"
    ADMIN = "admin"
    ONBOARDING = "onboarding"
    BILLING = "billing"
    REVIEWS = "reviews"
    COMPETITOR = "competitor"
    CHURN = "churn"
    SYSTEM = "system"


class ActionStatus(str, enum.Enum):
    PROPOSED = "proposed"
    QUEUED_FOR_APPROVAL = "queued_for_approval"
    APPROVED = "approved"
    DONE = "done"
    REJECTED = "rejected"


class ActionItem(Base):
    """The general task/agent-action queue: every next-step any agent comes
    up with, whether the system can execute it itself or a person has to.

    `handler` is a key into services.actions._HANDLERS. `auto_executable`
    means a handler exists and is safe to run without a human doing the work
    by hand (it may still land in the Message approval queue if it's
    client-facing — auto_executable only means "the system knows how", not
    "it ships without review")."""

    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50))
    handler: Mapped[str] = mapped_column(String(100), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    auto_executable: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default=ActionStatus.PROPOSED.value)
    result: Mapped[str] = mapped_column(Text, default="")
    assignee_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="")  # "agent:<name>" or "user:<id>"
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    assignee: Mapped["User | None"] = relationship()
    lead: Mapped["Lead | None"] = relationship()
    client: Mapped["Client | None"] = relationship()


class ScanResult(Base):
    """One Broken-Funnel Scanner (manual §8) pass over a domain. Only the
    cheap/passive/objective checks are automated; the two checks the manual
    says to do by hand (test enquiry reply time, phone answer rate) create a
    verification ActionItem instead of being auto-checked."""

    __tablename__ = "scan_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    company_name: Mapped[str] = mapped_column(String(255), default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    scanned: Mapped[bool] = mapped_column(Boolean, default=False)

    load_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tracking_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    click_to_call_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_recency_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    fault_list: Mapped[list] = mapped_column(JSON, default=list)
    leak_score: Mapped[float] = mapped_column(Float, default=0.0)

    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    verification_notes: Mapped[str] = mapped_column(Text, default="")

    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    added_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    verified_by: Mapped["User | None"] = relationship(foreign_keys=[verified_by_user_id])
    added_by: Mapped["User | None"] = relationship(foreign_keys=[added_by_user_id])
    lead: Mapped["Lead | None"] = relationship()


class AdCampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHED = "published"
    PAUSED = "paused"


class AdCampaign(Base):
    """A Promoter-agent-generated campaign brief (manual §4/§6/§14). Publish
    stays a manual step (or a future real-API integration) — this row is the
    plan plus the paste-ready copy, not a live campaign."""

    __tablename__ = "ad_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(50))
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    quarter_label: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(50), default=AdCampaignStatus.DRAFT.value)
    budget_monthly: Mapped[float] = mapped_column(Float, default=0.0)
    targeting: Mapped[dict] = mapped_column(JSON, default=dict)
    creative: Mapped[dict] = mapped_column(JSON, default=dict)
    brief_markdown: Mapped[str] = mapped_column(Text, default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    client: Mapped["Client | None"] = relationship()


class ReferralStatus(str, enum.Enum):
    ASKED = "asked"
    INTRODUCED = "introduced"
    BOOKED = "booked"
    CLOSED = "closed"
    DECLINED = "declined"


class ReferralRecord(Base):
    """Connector-agent tracking for manual §11's referral engine."""

    __tablename__ = "referral_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    referrer_name: Mapped[str] = mapped_column(String(255), default="")
    referred_name: Mapped[str] = mapped_column(String(255), default="")
    referred_email: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(50), default=ReferralStatus.ASKED.value)
    commission_pct: Mapped[float] = mapped_column(Float, default=10.0)
    commission_amount: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped["Client"] = relationship(back_populates="referrals")


class SourcingRequestStatus(str, enum.Enum):
    REQUESTED = "requested"
    FULFILLED = "fulfilled"


class SourcingRequest(Base):
    """A scouting request for a data source that can't be called directly
    from the backend — Vibe Prospecting (Explorium) is credit-metered and
    requires a human to confirm cost before every export, so it can't run as
    an unattended job the way Apollo sourcing does. This captures the
    criteria, backs a ready-to-paste prompt for a Vibe-Prospecting-enabled
    Claude session (services/sourcing_requests.py), and tracks the CSV
    import that fulfills it (services/lead_import.py). `pending_csv` holds
    an uploaded export between the upload step and the column-mapping
    confirmation step, then gets cleared."""

    __tablename__ = "sourcing_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(50), default=SourcingRequestStatus.REQUESTED.value)
    imported_lead_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_csv: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requested_by: Mapped["User | None"] = relationship()


class AgentToggle(Base):
    """The on/off switch for a named agent (matches an AGENT_JOBS `key` in
    app/scheduler.py). Absence of a row means "on" — see
    services/agent_toggles.py::is_enabled — so newly added agents default
    to enabled unless explicitly seeded off."""

    __tablename__ = "agent_toggles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class JobRunStatus(str, enum.Enum):
    OK = "ok"
    ERROR = "error"
    SKIPPED_DISABLED = "skipped_disabled"


class JobRun(Base):
    """One execution record per scheduled job. Written by
    scheduler._run_safely on every run (not just failures), so Hephaestus
    (services/system_health.py) can tell a job that's failing from one
    that's simply gone quiet, and /team can show real last-run status
    instead of only "next run"."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_key: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), default=JobRunStatus.OK.value)
    message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CompetitorNoteType(str, enum.Enum):
    AD = "ad"
    PRICING = "pricing"


class CompetitorNote(Base):
    """A manually-logged observation about a competitor — an ad, a pricing
    page, a positioning line. Eris (competitor watch) and Astraea (pricing
    benchmark) share this table, distinguished by `note_type`; there's no
    ad-transparency or scraping API wired in, on purpose — see
    services/competitor_watch.py."""

    __tablename__ = "competitor_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competitor_name: Mapped[str] = mapped_column(String(255))
    note_type: Mapped[str] = mapped_column(String(20), default=CompetitorNoteType.AD.value)
    source_url: Mapped[str] = mapped_column(String(500), default="")
    observed_text: Mapped[str] = mapped_column(Text, default="")
    analysis: Mapped[str] = mapped_column(Text, default="")
    logged_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MetricSnapshot(Base):
    """Daily rollup of the manual §15 six numbers, computed by the Analyst
    agent so the /metrics page doesn't recompute on every load."""

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    qualified_conversations: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_qualified_conversation: Mapped[dict] = mapped_column(JSON, default=dict)  # by channel
    close_rate: Mapped[dict] = mapped_column(JSON, default=dict)  # by channel
    cost_per_closed_client: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_time_to_close_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    referral_share: Mapped[float | None] = mapped_column(Float, nullable=True)
