# Kokoro Text-to-Speech Script

A Python script that converts text files (including Markdown) to high-quality audio using the [Kokoro TTS library](https://github.com/hexgrad/Kokoro). The script automatically handles text chunking for long documents and combines the audio chunks into a single output file.

I wrote this script using [Claude Code](https://claude.ai/code).

## Features

- 🎯 **Smart Text Processing**: Automatically chunks long texts into manageable pieces
- 📝 **Markdown Support**: Cleans markdown formatting for better TTS output
- 🎵 **High-Quality Audio**: Uses Kokoro-82M, an 82-million parameter TTS model
- 🔧 **Flexible Options**: Multiple voices, languages, and speed controls
- 🚀 **Apple Silicon Optimized**: Automatically enables MPS acceleration on M1/M2/M3/M4 Macs
- 📁 **Multiple Formats**: Supports various text file encodings and formats
- 🔄 **Stdin Support**: Read from pipes for Unix-style text processing workflows

## Installation

1. **Clone or download this repository**

2. **Set up a Python virtual environment** (recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install kokoro soundfile pydub
   ```

4. **Install system dependencies** (if needed):
   - **macOS**: No additional setup required
   - **Linux**: `sudo apt-get install espeak-ng`
   - **Windows**: Download and install [espeak-ng](https://github.com/espeak-ng/espeak-ng)

## Usage

### Basic Usage

```bash
# Convert a text file to audio
python text_to_speech.py document.txt

# Convert a markdown file
python text_to_speech.py README.md

# Read from stdin (pipe or redirect)
echo "Hello world!" | python text_to_speech.py
cat document.txt | python text_to_speech.py

# Read markdown from stdin
cat README.md | python text_to_speech.py --markdown
```

### Advanced Usage

```bash
# Use a different voice
python text_to_speech.py document.txt --voice af_bella

# Specify output file
python text_to_speech.py document.txt --output my_audio.wav

# Adjust speech speed
python text_to_speech.py document.txt --speed 1.2

# Use different language
python text_to_speech.py documento.txt --lang e --voice ef_dora  # Spanish

# Force CPU usage (disable GPU acceleration)
python text_to_speech.py document.txt --device cpu

# Process only part of a document (Unix-style)
head -n 20 long_document.txt | python text_to_speech.py
head -c 1000 README.md | python text_to_speech.py --markdown

# Extract and convert specific sections
grep -A 10 "Introduction" document.md | python text_to_speech.py --markdown
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
| `--device` | | Device for inference | `auto` |
| `--keep-temp` | | Keep temporary chunk files | `false` |
| `--list-voices` | | Show all available voices | |

### Available Voices

The script supports all Kokoro voices. Some popular options:

**American English (lang: `a`)**:
- `af_heart`, `af_bella`, `af_sarah`, `af_nicole`, `af_sky`
- `am_adam`, `am_michael`, `am_eric`, `am_liam`

**British English (lang: `b`)**:
- `bf_emma`, `bf_isabella`, `bf_lily`
- `bm_george`, `bm_lewis`, `bm_daniel`

**Other Languages**:
- Spanish (`e`): `ef_dora`, `em_alex`
- French (`f`): `ff_siwis`
- Italian (`i`): `if_sara`, `im_nicola`
- Portuguese (`p`): `pf_dora`, `pm_alex`
- Chinese (`z`): `zf_xiaoxiao`, `zm_yunxi`
- Japanese (`j`): `jf_gongitsune`, `jm_kumo`

### Language Codes

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

## How It Works

1. **Text Processing**: Reads the input file and cleans markdown formatting if applicable
2. **Chunking**: Automatically splits long text into smaller chunks suitable for the TTS model
3. **Audio Generation**: Converts each chunk to audio using the Kokoro TTS model
4. **Concatenation**: Combines all audio chunks into a single output file with natural pauses

## Markdown Processing

When processing Markdown files (`.md`, `.markdown`), the script automatically cleans the text for better TTS output by removing:

- **Headers** (`# ## ###`) - formatting removed, text preserved
- **Images** (`![alt text](url)`) - completely removed including alt text
- **Links** (`[text](url)`) - URLs removed, link text preserved
- **Emphasis** (`*italic*`, `**bold**`) - formatting removed, text preserved
- **Code blocks** and `inline code` - removed entirely
- **Lists** (`- item`, `1. item`) - bullet/numbering removed, text preserved
- **Blockquotes** (`> text`) - formatting removed, text preserved

**Example transformation:**
```markdown
# My Document
Here's an image: ![Screenshot](image.png)
Check out this [link](https://example.com) for more info.
```

Becomes:
```
My Document
Here's an image: 
Check out this link for more info.
```

## Examples

### Convert a Simple Text File
```bash
python text_to_speech.py story.txt
# Output: story.wav
```

### Convert Markdown Documentation
```bash
python text_to_speech.py README.md --voice af_bella --output readme_audio.wav
# Output: readme_audio.wav (with cleaned markdown formatting)
```

### Process Text from Stdin
```bash
# Convert piped text
echo "Welcome to our documentation" | python text_to_speech.py --voice af_sarah

# Process first 500 characters of a long document
head -c 500 long_document.txt | python text_to_speech.py --output preview.wav

# Convert specific sections using grep
grep -A 20 "Installation" README.md | python text_to_speech.py --markdown --output install_guide.wav
```

### Spanish Text with Spanish Voice
```bash
python text_to_speech.py documento.txt --lang e --voice ef_dora --speed 0.9
# Output: documento.wav (slower Spanish speech)
```

## System Requirements

- **Python**: 3.8 or higher
- **OS**: macOS, Linux, or Windows
- **Memory**: At least 4GB RAM recommended
- **Storage**: ~2GB for model weights (downloaded automatically)

### Apple Silicon Optimization

On M1/M2/M3/M4 Macs, the script automatically enables MPS (Metal Performance Shaders) acceleration by setting `PYTORCH_ENABLE_MPS_FALLBACK=1`. This provides significant speed improvements over CPU-only inference.

## Troubleshooting

### Common Issues

**"Module not found" errors**:
```bash
pip install kokoro soundfile pydub
```

**"espeak-ng not found" warnings**:
- macOS: Usually works without espeak-ng
- Linux: `sudo apt-get install espeak-ng`
- Windows: Install from [releases page](https://github.com/espeak-ng/espeak-ng/releases)

**CUDA out of memory**:
```bash
python text_to_speech.py document.txt --device cpu
```

**Long processing times**:
- Try using a smaller text file first
- Use `--device cpu` if GPU memory is limited
- Consider splitting very large documents manually

## License

This script is provided under the MIT License. The Kokoro TTS model has its own Apache 2.0 license.

## Acknowledgments

- [Kokoro TTS](https://github.com/hexgrad/Kokoro) - The excellent open-source TTS model
- [StyleTTS 2](https://github.com/yl4579/StyleTTS2) - The underlying architecture
- All contributors to the Kokoro project

## Contributing

Feel free to submit issues, feature requests, or pull requests to improve this script!