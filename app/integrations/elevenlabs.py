"""ElevenLabs — text-to-speech for Beacon's voice channel. Turns a drafted,
approved call script into an MP3 saved to disk, which Twilio then fetches
and plays on the outbound call (see app/integrations/twilio_sms.py).
"""
from __future__ import annotations

from pathlib import Path

import httpx

_API_BASE = "https://api.elevenlabs.io/v1"


class ElevenLabsError(RuntimeError):
    pass


def synthesize_to_file(api_key: str, voice_id: str, text: str, out_path: str | Path) -> Path:
    if not api_key:
        raise ElevenLabsError("ELEVENLABS_API_KEY is not configured.")
    if not voice_id:
        raise ElevenLabsError("ELEVENLABS_VOICE_ID is not configured.")
    if not text.strip():
        raise ElevenLabsError("No script text to synthesize.")

    resp = httpx.post(
        f"{_API_BASE}/text-to-speech/{voice_id}",
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        json={
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.75},
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise ElevenLabsError(f"ElevenLabs TTS failed ({resp.status_code}): {resp.text[:300]}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    return out_path
