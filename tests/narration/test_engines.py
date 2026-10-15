import numpy as np
import pytest
import soundfile as sf

import text_to_speech
from narration import words
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


@pytest.mark.parametrize("space", ["\xa0", "\t", "\u2009", "\u3000", " \t\xa0 "])
def test_kokoro_engine_makes_every_space_but_a_newline_plain(tmp_path, monkeypatch, space):
    # misaki misplaces an override after such a space earlier on its line; the
    # engine gets plain spaces, exactly as words.check reads the text.
    seen = {}

    def fake_generate_audio(text, output_path, voice="af_heart", speed=1.0, **_):
        seen["text"] = text
        sf.write(output_path, np.zeros(2400, dtype="float32"), 24000)

    monkeypatch.setattr(text_to_speech, "generate_audio", fake_generate_audio)
    text = (f"a{space}b\nc{space}d -- e\n\n"
            f"We saw{space}Pengelley and{space}[Kevorkian](/kɛvˈɔɹkiən/) left.")
    KokoroEngine().record(text, tmp_path / "recording.wav", "af_heart", 1.0)
    # Newlines stay, the override is intact, and "--" is left for generate_audio's
    # own prepare_for_tts.
    assert seen["text"] == ("a b\nc d -- e\n\n"
                            "We saw Pengelley and [Kevorkian](/kɛvˈɔɹkiən/) left.")
    assert text_to_speech.prepare_for_tts(seen["text"]) == words._spoken_text(text)


def test_misaki_g2p_answers_for_names_outside_its_lexicon():
    # Runs only where misaki is installed (not in the light test environment).
    pytest.importorskip("misaki")
    from narration.engines import MisakiG2P
    phonemes = MisakiG2P().phonemes("Pengelley")
    assert phonemes and "❓" not in phonemes and any(ch.isalpha() for ch in phonemes)
