# Kokoro TTS Toolkit

A Python toolkit for producing high-quality audio recordings of articles and long-form text using [MLX Audio](https://github.com/Blaizzy/mlx-audio) and the [Kokoro-82M model](https://huggingface.co/mlx-community/Kokoro-82M-bf16). Includes Gemini-based audio verification and MP3 conversion with ID3 tagging.

**Requires Apple Silicon (M1/M2/M3/M4 Mac).**

## Features

- **Text-to-Speech**: Converts text and Markdown files to audio with automatic chunking
- **Audio Verification**: Sends recordings to Gemini to detect mispronunciations, missing content, and pacing issues
- **MP3 Conversion**: Converts WAV output to MP3 with ID3 metadata tags (title, artist, album)
- **Markdown Support**: Strips formatting while preserving Kokoro phonetic pronunciation links (`[word](/phonemes/)`)
- **Section Breaks**: `[BREAK]` markers insert clean 2-second silent pauses between sections
- **Em Dash Handling**: Automatically converts `--` to `...` for natural pauses
- **Multiple Voices**: Supports all Kokoro voices across 9 languages
- **Stdin Support**: Read from pipes for Unix-style text processing workflows

## Requirements

- **Apple Silicon Mac** (M1, M2, M3, or M4)
- **Python 3.10+**
- **macOS** (MLX framework is Apple Silicon only)
- **ffmpeg** (for MP3 conversion)

## Installation

1. **Install [uv](https://docs.astral.sh/uv/getting-started/installation/)** (if you don't have it):
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

## Text-to-Speech

### Basic Usage

```bash
# Convert a text file to audio
uv run python text_to_speech.py document.txt

# Convert a markdown file
uv run python text_to_speech.py README.md

# Read from stdin (pipe or redirect)
echo "Hello world!" | uv run python text_to_speech.py
cat document.txt | uv run python text_to_speech.py

# Read markdown from stdin
cat README.md | uv run python text_to_speech.py --markdown
```

### Advanced Usage

```bash
# Use a different voice
uv run python text_to_speech.py document.txt --voice af_bella

# Specify output file
uv run python text_to_speech.py document.txt --output my_audio.wav

# Adjust speech speed
uv run python text_to_speech.py document.txt --speed 1.2

# Use different language
uv run python text_to_speech.py documento.txt --lang e --voice ef_dora  # Spanish

# Process only part of a document (Unix-style)
head -n 20 long_document.txt | uv run python text_to_speech.py
head -c 1000 README.md | uv run python text_to_speech.py --markdown

# Extract and convert specific sections
grep -A 10 "Introduction" document.md | uv run python text_to_speech.py --markdown
```

### Command Line Options

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

## Audio Verification

After generating a recording, verify its quality against the source text using Gemini:

```bash
uv run python verify_audio.py recording.wav source_text.md
```

This sends the audio and source text to Gemini, which listens to the recording and reports mispronunciations, missing or extra content, and pause issues with approximate timestamps.

| Option | Description | Default |
|--------|-------------|---------|
| `audio_file` | Path to the audio file (.wav) | (required) |
| `text_file` | Path to the source text file | (required) |
| `--model` | Gemini model to use | `gemini-2.5-flash` |

Requires a `GEMINI_API_KEY` environment variable (or a `.env` file in the project directory). Costs ~3 cents per verification of a 16-minute article.

## MP3 Conversion

Convert a WAV recording to MP3 with ID3 metadata tags:

```bash
uv run python convert_audio.py recording.wav output.mp3 \
    --title "The Cassandra of the Machine" \
    --artist "Charles Carman" \
    --album "The New Atlantis, No. 83 (Winter 2026)"
```

| Option | Description | Default |
|--------|-------------|---------|
| `wav_file` | Path to the input WAV file | (required) |
| `mp3_file` | Path for the output MP3 file | (required) |
| `--title` | Article title (ID3 TIT2 tag) | |
| `--artist` | Author name (ID3 TPE1 tag) | |
| `--album` | Publication name and issue (ID3 TALB tag) | |

## Preparing Text for TTS

### Markdown Cleaning

When processing Markdown files, the script automatically cleans formatting for better TTS output: headers, images, links, emphasis, code blocks, lists, and blockquotes are stripped while preserving the text content.

Phonetic pronunciation links (`[word](/phonemes/)`) are preserved — these use the Kokoro/misaki format to override pronunciation of difficult words.

### Section Breaks

Use `[BREAK]` on its own line to insert a 2-second silent pause between sections:

```
End of section one.

[BREAK]

Section Two Heading

Start of section two.
```

### Phonetic Overrides

Override pronunciation of proper nouns or unusual words using `[word](/phonemes/)`:

```
[Kevorkian](/kɛvˈɔːɹkiən/) was born in [Pontiac](/pˈɑntɪˌæk/), Michigan.
```

Find phonemes for similar-sounding words with misaki's G2P:

```python
from misaki import en
g2p = en.G2P()
print(g2p('jelly'))   # ʤˈɛli
```

### Available Voices

The toolkit supports all Kokoro voices. Some popular options:

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

## System Requirements

- **Hardware**: Apple Silicon Mac (M1, M2, M3, or M4)
- **Python**: 3.10 or higher
- **OS**: macOS only (MLX is Apple Silicon native)
- **Memory**: At least 4GB RAM recommended
- **Storage**: ~500MB for model weights (downloaded automatically)

## Troubleshooting

**"Module not found" errors**:
```bash
uv sync
```

**"mlx not supported" or platform errors**:
MLX Audio requires an Apple Silicon Mac. This script does not support Intel Macs, Linux, or Windows.

## License

This project is provided under the MIT License. The Kokoro TTS model has its own Apache 2.0 license.

## Acknowledgments

- [MLX Audio](https://github.com/Blaizzy/mlx-audio) - MLX-native audio processing library
- [Kokoro TTS](https://github.com/hexgrad/Kokoro) - The excellent open-source TTS model
- [Apple MLX](https://github.com/ml-explore/mlx) - Machine learning framework for Apple Silicon
