"""Claude wrapper: drafts outreach/replies and classifies inbound intent.

Every call asks the model for strict JSON and we parse it defensively. All
generated content is a *draft* — nothing in this module sends anything; it
just produces text for the approval queue (or for the autonomous sender, if
APPROVAL_MODE=autonomous).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    match = _JSON_FENCE_RE.search(text)
    candidate = match.group(1) if match else text
    return json.loads(candidate)


@dataclass
class BusinessProfile:
    business_name: str
    business_pitch: str
    sender_name: str
    sender_title: str
    calendly_link: str
    physical_address: str


class ClaudeDrafter:
    def __init__(self, api_key: str, model: str):
        self._client = Anthropic(api_key=api_key)
        self._model = model

    def _complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return _extract_json(text)

    def draft_first_touch_email(
        self, lead: dict[str, Any], profile: BusinessProfile
    ) -> dict[str, str]:
        system = (
            "You write short, specific, non-cringe cold outreach emails for a real "
            "business owner. No hype, no exclamation points, no 'I hope this email "
            "finds you well'. One clear reason this specific recipient might care, "
            "one clear ask (a quick reply or a short call), and it must read like a "
            "human wrote it in two minutes, not a template. 90-130 words for the body. "
            'Respond with ONLY JSON: {"subject": "...", "body": "..."}. The body must '
            "NOT include a greeting salutation line or sign-off — those are added "
            "separately."
        )
        user = (
            f"Recipient: {lead.get('contact_name')}, {lead.get('contact_title')} at "
            f"{lead.get('company_name')} ({lead.get('industry')}, "
            f"{lead.get('company_size')} employees, {lead.get('location')}).\n"
            f"Company description: {lead.get('description', 'n/a')}\n\n"
            f"My business: {profile.business_name}. What we do: {profile.business_pitch}\n"
            f"Ask: a quick reply, or a 15-minute call ({profile.calendly_link})."
        )
        data = self._complete_json(system, user)
        return {"subject": data.get("subject", ""), "body": data.get("body", "")}

    def classify_intent(self, thread_text: str) -> dict[str, str]:
        system = (
            "Classify the latest reply in this cold-outreach email thread. "
            'Respond with ONLY JSON: {"intent": one of '
            '["interested", "wants_more_info", "objection", "not_interested", '
            '"out_of_office", "unsubscribe_request", "scheduling", "other"], '
            '"summary": "one sentence"}'
        )
        data = self._complete_json(system, thread_text, max_tokens=256)
        return {
            "intent": data.get("intent", "other"),
            "summary": data.get("summary", ""),
        }

    def draft_reply(
        self,
        lead: dict[str, Any],
        thread_text: str,
        intent: str,
        profile: BusinessProfile,
    ) -> dict[str, str]:
        system = (
            "You are continuing a real email conversation on behalf of a business "
            "owner, replying to a prospect who is engaging with a cold outreach "
            "email. Match their tone, keep it short (under 120 words), answer their "
            "actual question/objection, and if the detected intent is 'interested' "
            "or 'scheduling', include this booking link naturally: "
            f"{profile.calendly_link}. If intent is 'unsubscribe_request', instead "
            "write a brief one-line confirmation that they've been removed and will "
            "not be contacted again — nothing else. "
            'Respond with ONLY JSON: {"body": "..."} (no subject, no greeting/sign-off).'
        )
        user = (
            f"Detected intent: {intent}\n"
            f"My business: {profile.business_name}. What we do: {profile.business_pitch}\n"
            f"Thread so far:\n{thread_text}"
        )
        data = self._complete_json(system, user)
        return {"body": data.get("body", "")}

    def draft_fault_led_email(
        self, lead: dict[str, Any], faults: list[str], profile: BusinessProfile
    ) -> dict[str, str]:
        """Manual §9 Sequence A: opens with a specific, verified fault, not a
        pitch. Only ever call this with faults a human has ticked `verified`
        on a ScanResult — see app/services/scanner.py."""
        system = (
            "You write the opening email of a fault-led outbound sequence, in the "
            "style of an operator who noticed something, not a vendor pitching. "
            "Lead with the single most specific verified fault. State it plainly, "
            "no hedging language, no exclamation points. Explicitly say you're not "
            "selling anything yet. One line connecting it to the sender's own "
            "operator experience if relevant. End with a low-pressure ask for a "
            "short conversation. Under 90 words. "
            'Respond with ONLY JSON: {"subject": "...", "body": "..."}. The body '
            "must NOT include a greeting salutation line or sign-off."
        )
        user = (
            f"Recipient: {lead.get('contact_name')} at {lead.get('company_name')} "
            f"({lead.get('industry')}, {lead.get('location')}).\n"
            f"Verified faults found on their site/funnel: {'; '.join(faults)}\n\n"
            f"My business: {profile.business_name}. What we do: {profile.business_pitch}\n"
            f"Ask: a quick reply, or a 15-minute call ({profile.calendly_link})."
        )
        data = self._complete_json(system, user)
        return {"subject": data.get("subject", ""), "body": data.get("body", "")}

    def draft_operator_led_email(
        self, lead: dict[str, Any], profile: BusinessProfile
    ) -> dict[str, str]:
        """Manual §9 Sequence B: for prospects the scanner found nothing
        wrong with. Leads with operator credibility instead of a fault."""
        system = (
            "You write the opening email of an operator-to-operator outbound "
            "message: one business owner writing to another, not a marketer "
            "pitching an agency. Establish operator credibility in one line, "
            "state plainly what was built and why, explicitly say this isn't a "
            "pitch, and offer to walk them through it in fifteen minutes. "
            "Under 90 words. "
            'Respond with ONLY JSON: {"subject": "...", "body": "..."}. No '
            "greeting salutation line or sign-off."
        )
        user = (
            f"Recipient: {lead.get('contact_name')} at {lead.get('company_name')} "
            f"({lead.get('industry')}, {lead.get('location')}).\n\n"
            f"My business: {profile.business_name}. What we do: {profile.business_pitch}\n"
            f"Ask: a 15-minute call ({profile.calendly_link})."
        )
        data = self._complete_json(system, user)
        return {"subject": data.get("subject", ""), "body": data.get("body", "")}

    def draft_enterprise_touch(
        self, lead: dict[str, Any], benchmark_summary: str, profile: BusinessProfile
    ) -> dict[str, str]:
        """Manual §9 Sequence C / §13: slower, benchmark-led, never sent at
        volume. Leads with data, not a pitch."""
        system = (
            "You write a single benchmark-led outreach email to a director/VP-level "
            "enterprise or franchise contact. Open with the category benchmark data "
            "point, offer to share the segment breakdown with no strings attached, "
            "and make clear it's useful whether or not they ever work together. No "
            "hard sell. Under 100 words. "
            'Respond with ONLY JSON: {"subject": "...", "body": "..."}. No '
            "greeting salutation line or sign-off."
        )
        user = (
            f"Recipient: {lead.get('contact_name')}, {lead.get('contact_title')} at "
            f"{lead.get('company_name')}.\n"
            f"Benchmark data available: {benchmark_summary}\n\n"
            f"My business: {profile.business_name}. What we do: {profile.business_pitch}"
        )
        data = self._complete_json(system, user)
        return {"subject": data.get("subject", ""), "body": data.get("body", "")}

    def analyze_intro_call(
        self, notes: str, lead: dict[str, Any], profile: BusinessProfile
    ) -> dict[str, Any]:
        """The Concierge agent's core call: turn raw discovery-call notes into
        a structured client picture plus a concrete next-steps plan.

        Each next step names a `handler` key. `auto_executable=true` only
        means "a handler exists" — client-facing handlers still land in the
        existing approval queue rather than sending on their own. Handlers
        the model may propose: schedule_90day_review_reminder,
        draft_proposal_email, run_scanner_scan, draft_welcome_email,
        add_to_referral_program, human_review (fallback for anything with no
        automated handler — always auto_executable=false)."""
        system = (
            "You are an operations analyst at a home-services growth agency, "
            "turning raw notes from a just-finished discovery call into a "
            "structured client record and a concrete action plan for the team.\n\n"
            "Respond with ONLY JSON in this shape:\n"
            "{\n"
            '  "company_snapshot": "1-2 sentences",\n'
            '  "pain_points": ["..."],\n'
            '  "goals": ["..."],\n'
            '  "budget_signal": "what they indicated about budget, or \'not discussed\'",\n'
            '  "decision_maker": "name/role, or \'unclear\'",\n'
            '  "timeline": "urgency/timeline signal",\n'
            '  "recommended_tier": "your recommendation given what was discussed",\n'
            '  "objections": ["..."],\n'
            '  "next_steps": [\n'
            "    {\n"
            '      "title": "short imperative title",\n'
            '      "description": "1-2 sentences",\n'
            '      "category": one of ["intake_followup","outreach","scanner_verify","referral","ads","content","admin"],\n'
            '      "handler": one of ["schedule_90day_review_reminder","draft_proposal_email","run_scanner_scan","draft_welcome_email","add_to_referral_program","human_review"],\n'
            '      "auto_executable": true or false,\n'
            '      "payload_hint": "one sentence on what the handler needs to know"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Propose 3-6 next steps. Never mark handler=human_review as "
            "auto_executable=true. Base every field only on what's actually in "
            "the notes — write \"not discussed\" rather than inventing detail."
        )
        user = (
            f"Prospect: {lead.get('contact_name')}, {lead.get('contact_title')} at "
            f"{lead.get('company_name')} ({lead.get('industry')}, {lead.get('location')}).\n\n"
            f"My business: {profile.business_name}. What we do: {profile.business_pitch}\n\n"
            f"Call notes:\n{notes}"
        )
        return self._complete_json(system, user, max_tokens=2048)

    def draft_referral_ask(
        self, client: dict[str, Any], case_study_numbers: str, profile: BusinessProfile
    ) -> dict[str, str]:
        """Manual §11: the 90-day-review ask script plus a ready-to-forward
        introduction message, written for the specific client."""
        system = (
            "Write two short pieces of referral-engine copy for a client's "
            "90-day review, in the plain, non-salesy voice of an operator "
            "talking to another business owner:\n"
            "1. ask_script: what to say out loud, asking for two names of "
            "operators dealing with the same problem the client had before "
            "signing up.\n"
            "2. forwardable_message: a short message the client can literally "
            "forward as-is to introduce the referred business, citing the "
            "client's own real result.\n"
            'Respond with ONLY JSON: {"ask_script": "...", "forwardable_message": "..."}'
        )
        user = (
            f"Client: {client.get('company_name')}, contact {client.get('contact_name')}.\n"
            f"Their result so far: {case_study_numbers}\n"
            f"My business: {profile.business_name}. {profile.business_pitch}\n"
            f"Sender: {profile.sender_name}"
        )
        data = self._complete_json(system, user)
        return {
            "ask_script": data.get("ask_script", ""),
            "forwardable_message": data.get("forwardable_message", ""),
        }

    def draft_ad_copy(
        self, platform: str, angle: str, segment: str, profile: BusinessProfile
    ) -> dict[str, str]:
        """Manual §6: one ad unit (headline + body) for a given platform and
        creative angle, aimed at a given audience segment."""
        system = (
            "Write one piece of ad creative for the given platform and angle. "
            "Follow the manual's rule: cover the logo test — a competitor "
            "couldn't paste their name over this and have it still make sense. "
            "Carry at least two of: the specific mechanism, a specific number, "
            "operator credibility, the guarantee. No generic agency language "
            "('full-service', 'results-driven', 'data-driven', 'ROI-focused', "
            "'tailored solutions', etc.). "
            'Respond with ONLY JSON: {"headline": "...", "body": "..."}'
        )
        user = (
            f"Platform: {platform}\nAngle: {angle}\nAudience segment: {segment}\n"
            f"My business: {profile.business_name}. What we do: {profile.business_pitch}\n"
            f"Sender operator story: {profile.sender_name}, {profile.sender_title}"
        )
        data = self._complete_json(system, user, max_tokens=512)
        return {"headline": data.get("headline", ""), "body": data.get("body", "")}

    def draft_content_extract(self, metrics_summary: str, profile: BusinessProfile) -> str:
        """Manual §12: a quarterly benchmark-report extract drafted from real
        aggregated metrics. Honesty over polish — the model is told to say so
        plainly when the sample is small."""
        system = (
            "Write a short quarterly benchmark-report extract (under 300 words, "
            "markdown, one page) for a home-services growth agency to publish. "
            "State the sample size honestly. If a finding is uncomfortable, "
            "include it anyway rather than only publishing flattering numbers. "
            "No hype language. Respond with markdown only, no JSON, no code fence."
        )
        user = f"Aggregated metrics this quarter:\n{metrics_summary}\n\nPublisher: {profile.business_name}"
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    def draft_client_email(
        self, purpose: str, lead: dict[str, Any], context: str, profile: BusinessProfile
    ) -> dict[str, str]:
        """General-purpose short client email for intake-plan handlers, e.g.
        purpose='proposal follow-up' or purpose='welcome / onboarding'."""
        system = (
            f"Write a short, specific {purpose} email from a real business owner to a "
            "prospect/client they've already spoken with. No hype, no generic agency "
            "language, reads like a person wrote it. Under 130 words. "
            'Respond with ONLY JSON: {"subject": "...", "body": "..."}. No greeting '
            "salutation line or sign-off."
        )
        user = (
            f"Recipient: {lead.get('contact_name')} at {lead.get('company_name')}.\n"
            f"Context from the call: {context}\n\n"
            f"My business: {profile.business_name}. What we do: {profile.business_pitch}"
        )
        data = self._complete_json(system, user)
        return {"subject": data.get("subject", ""), "body": data.get("body", "")}

    def draft_followup(self, lead: dict[str, Any], profile: BusinessProfile) -> dict[str, str]:
        system = (
            "Write a brief, low-pressure follow-up to a cold email that got no "
            "reply. Under 60 words. Assume they're busy, not uninterested. One new "
            "angle or piece of value, not just 'just checking in'. "
            'Respond with ONLY JSON: {"subject": "...", "body": "..."} (no greeting/sign-off).'
        )
        user = (
            f"Recipient: {lead.get('contact_name')} at {lead.get('company_name')}.\n"
            f"My business: {profile.business_name}. What we do: {profile.business_pitch}\n"
            f"Booking link: {profile.calendly_link}"
        )
        data = self._complete_json(system, user)
        return {"subject": data.get("subject", ""), "body": data.get("body", "")}
