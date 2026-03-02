"""Configuration loader with YAML + environment variable fallback."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SonioxConfig:
    api_key: str = ""
    model: str = "stt-rt-preview"
    language_hints: list[str] = field(default_factory=lambda: ["vi", "en"])
    endpoint_detection: bool = True
    context_terms: list[str] = field(default_factory=lambda: [
        "refactor", "debug", "commit", "push", "merge",
        "function", "component", "API", "deploy",
    ])


@dataclass
class TmuxConfig:
    session_name: str = "voice-claude"
    auto_create: bool = True
    auto_run_claude: bool = True


@dataclass
class OverlayConfig:
    width: int = 300
    height: int = 44
    opacity: float = 0.85
    position: str = "bottom-right"
    theme: str = "dark"


@dataclass
class VoiceConfig:
    mode: str = "push_to_talk"
    hotkey: str = "F5"
    auto_send: bool = True
    auto_send_delay_ms: int = 500
    end_phrases: list[str] = field(default_factory=lambda: [
        "cảm ơn nhiều", "thank you very much",
        "cảm ơn", "cám ơn", "thank you",
    ])


@dataclass
class VADConfig:
    enabled: bool = True
    aggressiveness: int = 2  # 0-3, higher = filters more silence
    pre_roll_frames: int = 3  # Keep N silence frames before speech (300ms)
    hangover_frames: int = 5  # Keep sending N frames after speech ends (500ms)
    speech_threshold: float = 0.6  # Fraction of sub-frames that must be speech


@dataclass
class AppConfig:
    soniox: SonioxConfig = field(default_factory=SonioxConfig)
    tmux: TmuxConfig = field(default_factory=TmuxConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    vad: VADConfig = field(default_factory=VADConfig)


def load_config(config_path: str | None = None) -> AppConfig:
    """Load config from YAML file with env var fallback.

    Priority: YAML file > environment variables > defaults.
    """
    config = AppConfig()

    # Try loading YAML
    if config_path is None:
        config_path = "config.yaml"

    path = Path(config_path)
    raw: dict = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    # Soniox config
    soniox_raw = raw.get("soniox", {})
    config.soniox.api_key = (
        soniox_raw.get("api_key")
        or os.environ.get("SONIOX_API_KEY", "")
    )
    config.soniox.model = soniox_raw.get("model", config.soniox.model)
    if "language_hints" in soniox_raw:
        config.soniox.language_hints = soniox_raw["language_hints"]
    config.soniox.endpoint_detection = soniox_raw.get(
        "endpoint_detection", config.soniox.endpoint_detection
    )
    if "context_terms" in soniox_raw:
        config.soniox.context_terms = soniox_raw["context_terms"]

    # Tmux config
    tmux_raw = raw.get("tmux", {})
    config.tmux.session_name = tmux_raw.get(
        "session_name", config.tmux.session_name
    )
    config.tmux.auto_create = tmux_raw.get(
        "auto_create", config.tmux.auto_create
    )
    config.tmux.auto_run_claude = tmux_raw.get(
        "auto_run_claude", config.tmux.auto_run_claude
    )

    # Overlay config
    overlay_raw = raw.get("overlay", {})
    config.overlay.width = overlay_raw.get("width", config.overlay.width)
    config.overlay.height = overlay_raw.get("height", config.overlay.height)
    config.overlay.opacity = overlay_raw.get("opacity", config.overlay.opacity)
    config.overlay.position = overlay_raw.get(
        "position", config.overlay.position
    )
    config.overlay.theme = overlay_raw.get("theme", config.overlay.theme)

    # Voice config
    voice_raw = raw.get("voice", {})
    config.voice.mode = voice_raw.get("mode", config.voice.mode)
    config.voice.hotkey = voice_raw.get("hotkey", config.voice.hotkey)
    config.voice.auto_send = voice_raw.get(
        "auto_send", config.voice.auto_send
    )
    config.voice.auto_send_delay_ms = voice_raw.get(
        "auto_send_delay_ms", config.voice.auto_send_delay_ms
    )
    if "end_phrases" in voice_raw:
        config.voice.end_phrases = voice_raw["end_phrases"]

    # VAD config
    vad_raw = raw.get("vad", {})
    config.vad.enabled = vad_raw.get("enabled", config.vad.enabled)
    config.vad.aggressiveness = vad_raw.get(
        "aggressiveness", config.vad.aggressiveness
    )
    config.vad.pre_roll_frames = vad_raw.get(
        "pre_roll_frames", config.vad.pre_roll_frames
    )
    config.vad.hangover_frames = vad_raw.get(
        "hangover_frames", config.vad.hangover_frames
    )
    config.vad.speech_threshold = vad_raw.get(
        "speech_threshold", config.vad.speech_threshold
    )

    return config


def validate_config(config: AppConfig) -> list[str]:
    """Validate config, return list of error messages."""
    errors = []
    if not config.soniox.api_key:
        errors.append(
            "Soniox API key is required. "
            "Set it in config.yaml or SONIOX_API_KEY env var."
        )
    if config.overlay.opacity < 0.1 or config.overlay.opacity > 1.0:
        errors.append("overlay.opacity must be between 0.1 and 1.0")
    if config.voice.mode not in ("push_to_talk", "auto_detect"):
        errors.append("voice.mode must be 'push_to_talk' or 'auto_detect'")
    if config.vad.aggressiveness not in (0, 1, 2, 3):
        errors.append("vad.aggressiveness must be 0, 1, 2, or 3")
    if not 0.0 < config.vad.speech_threshold <= 1.0:
        errors.append("vad.speech_threshold must be between 0.0 (exclusive) and 1.0")
    return errors
