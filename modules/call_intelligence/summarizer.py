"""Claude wrapper: turns a raw call transcript into a structured summary.

Deliberately a standalone copy of the small "ask for strict JSON, parse
defensively" pattern in app/integrations/claude.py (see its `_extract_json`)
rather than an import across the app/modules boundary — see modules/README.md
("do not import from another modules/<name>/ package"; the same spirit
applies to not reaching into app/integrations for a helper this small).
"""
from __future__ import annotations

import json
import re
from typing import Any

from anthropic import Anthropic

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    match = _JSON_FENCE_RE.search(text)
    candidate = match.group(1) if match else text
    return json.loads(candidate)


class CallSummarizer:
    """One Claude call per finished call: transcript in, structured JSON out.

    Usage:
        summarizer = CallSummarizer(settings.anthropic_api_key, settings.anthropic_model)
        result = summarizer.summarize(transcript)
        # result == {"summary": str, "agreed_items": list[str], "deadline_mentioned": str | None}
    """

    def __init__(self, api_key: str, model: str):
        self._client = Anthropic(api_key=api_key)
        self._model = model

    def summarize(self, transcript: str) -> dict[str, Any]:
        system = (
            "You summarize business phone/video call transcripts for a small "
            "business owner. Read the transcript and extract: a short factual "
            "summary of what was discussed and decided, any concrete items either "
            "party agreed to do (deliverables, follow-ups, payments, next steps), "
            "and the single most concrete deadline or due date mentioned in the "
            "call, if any (e.g. 'send the proposal by Friday', 'contract signed by "
            "the 15th'). Resolve relative dates ('next Friday', 'in two weeks') "
            "against the call date if it's inferable from the transcript, "
            "otherwise leave deadline_mentioned null rather than guessing.\n\n"
            "Respond with ONLY JSON, no prose, no markdown fence:\n"
            '{"summary": "2-4 sentence summary", '
            '"agreed_items": ["short imperative phrase", ...], '
            '"deadline_mentioned": "YYYY-MM-DD" or null}'
        )
        user = f"Transcript:\n{transcript}"

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text")

        try:
            data = _extract_json(text)
        except (json.JSONDecodeError, AttributeError):
            data = {}

        summary = str(data.get("summary") or "").strip()
        agreed_items_raw = data.get("agreed_items")
        agreed_items = (
            [str(item).strip() for item in agreed_items_raw if str(item).strip()]
            if isinstance(agreed_items_raw, list)
            else []
        )
        deadline = data.get("deadline_mentioned")
        deadline_mentioned = str(deadline).strip() if deadline else None

        return {
            "summary": summary,
            "agreed_items": agreed_items,
            "deadline_mentioned": deadline_mentioned or None,
        }
