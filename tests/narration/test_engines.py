import numpy as np
import pytest
import soundfile as sf

import text_to_speech
from narration.engines import KokoroEngine


def test_kokoro_engine_cleans_markdown_like_the_cli(tmp_path, monkeypatch):
    seen = {}

    def fake_generate_audio(text, output_path, voice="af_heart", speed=1.0, **_):
        seen.update(text=text, voice=voice, speed=speed)
        sf.write(output_path, np.zeros(2400, dtype="float32"), 24000)  # 0.1 s of silence

    monkeypatch.setattr(text_to_speech, "generate_audio", fake_generate_audio)
    out = tmp_path / "recording.wav"
    seconds = KokoroEngine().record(
        "# Zoning Alone\n\nSee the [court](https://example.com/zoning) **today**, "
        "said [Pengelley](/pɛnʤˈɛli/).", out, "af_heart", 1.0)
    # The heading marker, the URL and the emphasis are gone; the phonetic override stays.
    assert seen["text"] == "Zoning Alone\n\nSee the court today, said [Pengelley](/pɛnʤˈɛli/)."
    assert (seen["voice"], seen["speed"]) == ("af_heart", 1.0)
    assert seconds == pytest.approx(0.1, abs=0.01)
