# Voice Input for Claude Code

A floating macOS overlay that lets you control Claude Code with your voice. Speak into your mic instead of typing — the app converts speech to text (via Soniox API) and sends it directly to Claude Code running in tmux.

> **macOS only** — Supports Apple Silicon and Intel.

## Demo

```
┌─────────────────────────────────────────────────────┐
│                    Terminal (tmux)                   │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │ Claude Code interactive session               │  │
│  │ > output displays here                        │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│         ┌──────────────────────────┐                │
│         │  "refactor main.py"      │  Floating      │
│         │  Listening...     [x]    │  Overlay       │
│         └──────────────────────────┘                │
└─────────────────────────────────────────────────────┘
```

## Requirements

- **macOS** (Apple Silicon or Intel)
- **Python 3.10+** — `brew install python`
- **tmux** — `brew install tmux`
- **Claude Code** — installed and working
- **Microphone** — built-in or external
- **Soniox API Key** — sign up at [console.soniox.com](https://console.soniox.com)

## Installation

```bash
# Clone repo
git clone https://github.com/namdn/claude-voice.git
cd claude-voice

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create config
cp config.example.yaml config.yaml
```

Open `config.yaml` and add your Soniox API key:

```yaml
soniox:
  api_key: "YOUR_SONIOX_API_KEY"
```

## Usage

### 1. Start Claude Code in tmux

```bash
tmux new -s voice-claude
claude
```

### 2. Run the app (in another terminal)

```bash
source .venv/bin/activate
python main.py
```

The app will show a setup screen where you can select a tmux session from a dropdown, then click Connect.

### 3. Speak

- **Push-to-talk (default):** Click the mic button on the overlay or press `F5`
- When you finish speaking, the app auto-detects the endpoint and sends text to Claude Code
- Supports Vietnamese, English, and mixed (Vietnamese-English)

## How It Works

```
Microphone → VAD filter → Soniox STT API (WebSocket) → Transcript → tmux send-keys → Claude Code
```

1. App captures audio frames from the mic
2. **VAD (Voice Activity Detection)** filters silence — only speech frames are sent to Soniox, saving API tokens
3. Soniox returns real-time transcript (displayed on the overlay)
4. When speech ends (endpoint detected), the complete text is sent to Claude Code via `tmux send-keys`
5. Claude Code receives the text as if the user typed it on the keyboard

## VAD (Voice Activity Detection)

Client-side VAD uses **webrtcvad** to filter silence before sending audio to Soniox.

```
Mic (100ms frames)
    │
    ▼
VAD filter ──── silence? → drop (not sent)
    │
    speech? → queue → Soniox WebSocket
```

### Anti-clipping mechanisms

| Mechanism | Description | Default |
|-----------|-------------|---------|
| **Pre-roll** | Keep N silence frames before speech starts — prevents clipping the beginning | 3 frames (300ms) |
| **Hangover** | After speech ends, keep sending N more frames — prevents clipping the end | 5 frames (500ms) |
| **Speech threshold** | Fraction of sub-frames that must be speech to count the frame as speech | 0.6 (3/5) |

### VAD Configuration

```yaml
vad:
  enabled: true
  aggressiveness: 2        # 0-3, higher = filters more silence
  pre_roll_frames: 3       # Keep 300ms before speech
  hangover_frames: 5       # Keep 500ms after speech ends
  speech_threshold: 0.6    # 3/5 sub-frames must be speech
```

Set `vad.enabled: false` to disable VAD and send all frames.

## Configuration

See [`config.example.yaml`](config.example.yaml) for all available options.

| Group | Option | Description | Default |
|-------|--------|-------------|---------|
| `soniox` | `api_key` | Soniox API key | — |
| `soniox` | `model` | STT model | `stt-rt-preview` |
| `soniox` | `language_hints` | Languages | `["vi", "en"]` |
| `tmux` | `session_name` | tmux session name | `voice-claude` |
| `tmux` | `auto_create` | Auto-create session | `true` |
| `overlay` | `position` | Overlay position | `bottom-right` |
| `overlay` | `opacity` | Opacity | `0.85` |
| `overlay` | `theme` | Theme | `dark` |
| `voice` | `mode` | Input mode | `push_to_talk` |
| `voice` | `hotkey` | Hotkey | `F5` |
| `voice` | `auto_send` | Auto-send on speech end | `true` |
| `vad` | `enabled` | Enable/disable VAD | `false` |
| `vad` | `aggressiveness` | Silence filter level (0-3) | `2` |

## Project Structure

```
claude-voice/
├── main.py                 # Entry point
├── config.yaml             # Config (gitignored)
├── config.example.yaml     # Example config
├── requirements.txt        # Python dependencies
├── src/
│   ├── app.py              # Main application class
│   ├── overlay.py          # Floating overlay UI (CustomTkinter)
│   ├── soniox_client.py    # Soniox WebSocket STT client
│   ├── tmux_bridge.py      # tmux connection & send-keys
│   ├── audio_capture.py    # Microphone capture (sounddevice)
│   ├── vad.py              # Voice Activity Detection (webrtcvad)
│   └── config.py           # Config loader
└── .gitignore
```

## Error Handling

| Scenario | Behavior |
|----------|----------|
| tmux not installed | Shows installation instructions |
| No tmux session found | Setup screen with session dropdown |
| tmux session disconnected | Auto-detects, allows reconnect |
| Soniox WebSocket disconnected | Auto-reconnect |
| Mic not working | Shows error message |

## License

MIT
