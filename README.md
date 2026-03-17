# Kokoro TTS Toolkit

Produce high-quality audio recordings of articles and long-form text on Apple Silicon. Give it a URL or a text file, and it will generate a natural-sounding reading, verify the audio against the source for errors, and output a tagged MP3.

Under the hood, the toolkit uses the [Kokoro-82M](https://huggingface.co/mlx-community/Kokoro-82M-bf16) text-to-speech model running on [MLX](https://github.com/ml-explore/mlx), Google's Gemini for audio verification, and [ffmpeg](https://ffmpeg.org/) for MP3 encoding.

**Requires Apple Silicon (M1/M2/M3/M4 Mac).**

## Quick Start

### With Claude Code (recommended)

The easiest way to use this toolkit is through its [Claude Code](https://claude.ai/code) skill, which manages the full workflow — fetching articles, preparing text, generating audio, verifying quality, and fixing issues iteratively:

```
> Record an audio version of this article: https://example.com/article
```

The skill (`.claude/skills/article-tts-recording.md`) coordinates this toolkit with the [article-assistant](../article-assistant) project for content retrieval. You can also invoke it explicitly with `/article-tts-recording`.

### From the Command Line

Generate audio from a text file:

```bash
uv run python text_to_speech.py article.md --voice af_heart --output recording.wav
```

Verify the recording against the source text:

```bash
uv run python verify_audio.py recording.wav article.md
```

Convert to MP3 with metadata:

```bash
uv run python convert_audio.py recording.wav "Article Title.mp3" \
    --title "Article Title" --artist "Author Name" \
    --album "The New Atlantis, No. 83 (Winter 2026)"
```

## Installation

1. **Install [uv](https://docs.astral.sh/uv/getting-started/installation/)**:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Install dependencies**:
   ```bash
   uv sync
   uv pip install en-core-web-sm@https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
   ```

3. **Set up Gemini API key** (for audio verification):
   ```bash
   echo "GEMINI_API_KEY=your_key_here" > .env
   ```

## Preparing Text for TTS

The toolkit reads plain text or Markdown. For best results with articles:

**Section breaks** — Use `[BREAK]` on its own line between sections. This inserts a 2-second silent pause.

**Section headings** — Include them as plain text. The markdown cleaner strips `#` but preserves the words.

**Em dashes** — Write as `--`. They're automatically converted to pauses.

**Roman numerals** — Spell out ("World War One" not "World War I").

**Pronunciation overrides** — Use `[word](/phonemes/)` for difficult proper nouns:
```
[Kevorkian](/kɛvˈɔːɹkiən/) was born in Michigan.
```

## Command Line Reference

<details>
<summary><strong>text_to_speech.py</strong> — Generate audio from text</summary>

```bash
uv run python text_to_speech.py document.txt
uv run python text_to_speech.py article.md --voice af_bella --output recording.wav
echo "Hello world" | uv run python text_to_speech.py
```

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `input_file` | | Input text file, or `-` for stdin | (required unless using stdin) |
| `--output` | `-o` | Output audio file path | `input_filename.wav` or `output.wav` |
| `--voice` | `-v` | Voice to use for TTS | `af_heart` |
| `--speed` | `-s` | Speech speed multiplier | `1.0` |
| `--lang` | `-l` | Language code | `a` (American English) |
| `--markdown` | `-m` | Treat input as markdown | auto-detect for `.md` files |
| `--keep-temp` | | Keep temporary chunk files | `false` |
| `--list-voices` | | Show all available voices | |

Markdown files are automatically cleaned: headers, images, links, emphasis, code blocks, lists, and blockquotes are stripped. Phonetic pronunciation links (`[word](/phonemes/)`) are preserved.

</details>

<details>
<summary><strong>verify_audio.py</strong> — Verify audio quality with Gemini</summary>

```bash
uv run python verify_audio.py recording.wav source_text.md
```

Sends audio + source text to Gemini, which listens and reports mispronunciations, missing or extra content, and pacing issues with timestamps. Costs ~3 cents per 16 minutes of audio.

| Option | Description | Default |
|--------|-------------|---------|
| `audio_file` | Path to the audio file (.wav) | (required) |
| `text_file` | Path to the source text file | (required) |
| `--model` | Gemini model to use | `gemini-2.5-flash` |

Requires a `GEMINI_API_KEY` environment variable or `.env` file.

</details>

<details>
<summary><strong>convert_audio.py</strong> — Convert WAV to tagged MP3</summary>

```bash
uv run python convert_audio.py recording.wav output.mp3 \
    --title "Article Title" --artist "Author Name" \
    --album "The New Atlantis, No. 83 (Winter 2026)"
```

| Option | Description | Default |
|--------|-------------|---------|
| `wav_file` | Path to the input WAV file | (required) |
| `mp3_file` | Path for the output MP3 file | (required) |
| `--title` | Article title (ID3 TIT2 tag) | |
| `--artist` | Author name (ID3 TPE1 tag) | |
| `--album` | Publication name and issue (ID3 TALB tag) | |

</details>

<details>
<summary><strong>Available voices and languages</strong></summary>

**American English (lang: `a`)**: `af_heart`, `af_bella`, `af_sarah`, `am_adam`, `am_michael`

**British English (lang: `b`)**: `bf_emma`, `bf_isabella`, `bm_george`, `bm_daniel`

**Other Languages**: Spanish (`e`), French (`f`), Hindi (`h`), Italian (`i`), Japanese (`j`), Portuguese (`p`), Chinese (`z`)

| Code | Language |
|------|----------|
| `a` | American English |
| `b` | British English |
| `e` | Spanish |
| `f` | French |
| `h` | Hindi |
| `i` | Italian |
| `j` | Japanese |
| `p` | Brazilian Portuguese |
| `z` | Mandarin Chinese |

</details>

## System Requirements

- **Hardware**: Apple Silicon Mac (M1, M2, M3, or M4)
- **Python**: 3.10+
- **OS**: macOS (MLX is Apple Silicon only)
- **ffmpeg**: Required for MP3 conversion

## License

MIT License. The Kokoro TTS model has its own Apache 2.0 license.

## Acknowledgments

- [MLX Audio](https://github.com/Blaizzy/mlx-audio) — MLX-native audio processing
- [Kokoro TTS](https://github.com/hexgrad/Kokoro) — Open-source TTS model
- [Apple MLX](https://github.com/ml-explore/mlx) — ML framework for Apple Silicon
