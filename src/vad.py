"""Voice Activity Detection using webrtcvad.

Filters silence frames before sending to the STT service,
reducing unnecessary API token usage.
"""

import logging
from collections import deque
from dataclasses import dataclass

import webrtcvad

log = logging.getLogger(__name__)

# Audio format constants (must match audio_capture.py)
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # int16
FRAME_DURATION_MS = 100
FRAME_SIZE = SAMPLE_RATE * FRAME_DURATION_MS // 1000 * BYTES_PER_SAMPLE  # 3200

# webrtcvad accepts 10/20/30ms frames at 16kHz
SUB_FRAME_DURATION_MS = 20
SUB_FRAME_SIZE = SAMPLE_RATE * SUB_FRAME_DURATION_MS // 1000 * BYTES_PER_SAMPLE  # 640
SUB_FRAMES_PER_FRAME = FRAME_DURATION_MS // SUB_FRAME_DURATION_MS  # 5


@dataclass
class VADConfig:
    enabled: bool = True
    aggressiveness: int = 2  # 0-3, higher = filters more silence
    pre_roll_frames: int = 3  # Keep N silence frames before speech (300ms)
    hangover_frames: int = 5  # Keep sending N frames after speech ends (500ms)
    speech_threshold: float = 0.6  # Fraction of sub-frames that must be speech


class VoiceActivityDetector:
    """Filters audio frames, passing only speech (plus pre-roll/hangover).

    Usage:
        vad = VoiceActivityDetector(config)
        frames_to_send = vad.process(frame_bytes)
        # frames_to_send is a list of bytes; empty if silence
    """

    def __init__(self, config: VADConfig | None = None):
        self._config = config or VADConfig()
        self._vad = webrtcvad.Vad(self._config.aggressiveness)
        self._pre_roll: deque[bytes] = deque(
            maxlen=self._config.pre_roll_frames
        )
        self._hangover_remaining = 0
        self._in_speech = False

    def process(self, frame_bytes: bytes) -> list[bytes]:
        """Process one audio frame (100ms / 3200 bytes).

        Returns a list of frames to send:
        - Empty list if silence (and not in hangover)
        - Pre-roll buffer + current frame on speech onset
        - Just the current frame during ongoing speech or hangover
        """
        if not self._config.enabled:
            return [frame_bytes]

        is_speech = self._detect_speech(frame_bytes)

        if is_speech:
            if not self._in_speech:
                # Speech onset — flush pre-roll buffer + current frame
                self._in_speech = True
                frames = list(self._pre_roll)
                self._pre_roll.clear()
                frames.append(frame_bytes)
                log.debug(
                    "Speech onset, flushing %d pre-roll frames", len(frames) - 1
                )
                self._hangover_remaining = self._config.hangover_frames
                return frames
            else:
                # Ongoing speech
                self._hangover_remaining = self._config.hangover_frames
                return [frame_bytes]
        else:
            # Silence
            if self._hangover_remaining > 0:
                # Still in hangover period
                self._hangover_remaining -= 1
                if self._hangover_remaining == 0:
                    self._in_speech = False
                    log.debug("Hangover ended, returning to silence")
                return [frame_bytes]
            else:
                # Pure silence — buffer for pre-roll, don't send
                self._in_speech = False
                self._pre_roll.append(frame_bytes)
                return []

    def reset(self) -> None:
        """Reset state between recording sessions."""
        self._pre_roll.clear()
        self._hangover_remaining = 0
        self._in_speech = False

    def _detect_speech(self, frame_bytes: bytes) -> bool:
        """Split frame into 20ms sub-frames and do majority vote.

        Returns True if the fraction of speech sub-frames >= threshold.
        """
        speech_count = 0
        for i in range(SUB_FRAMES_PER_FRAME):
            start = i * SUB_FRAME_SIZE
            sub_frame = frame_bytes[start : start + SUB_FRAME_SIZE]
            if len(sub_frame) < SUB_FRAME_SIZE:
                break
            try:
                if self._vad.is_speech(sub_frame, SAMPLE_RATE):
                    speech_count += 1
            except Exception:
                # If VAD fails on a sub-frame, treat as silence
                pass

        return speech_count / SUB_FRAMES_PER_FRAME >= self._config.speech_threshold
