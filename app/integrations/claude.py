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
