import pytest

from app.integrations import elevenlabs


def test_synthesize_requires_api_key(tmp_path):
    with pytest.raises(elevenlabs.ElevenLabsError, match="ELEVENLABS_API_KEY"):
        elevenlabs.synthesize_to_file("", "voice123", "hello", tmp_path / "clip.mp3")


def test_synthesize_requires_voice_id(tmp_path):
    with pytest.raises(elevenlabs.ElevenLabsError, match="ELEVENLABS_VOICE_ID"):
        elevenlabs.synthesize_to_file("key123", "", "hello", tmp_path / "clip.mp3")


def test_synthesize_requires_text(tmp_path):
    with pytest.raises(elevenlabs.ElevenLabsError, match="No script text"):
        elevenlabs.synthesize_to_file("key123", "voice123", "   ", tmp_path / "clip.mp3")
