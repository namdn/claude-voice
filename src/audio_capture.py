"""Audio capture from microphone using sounddevice."""

from __future__ import annotations

import logging
import queue

import numpy as np
import sounddevice as sd

from src.vad import VADConfig, VoiceActivityDetector

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"  # PCM 16-bit signed
BLOCKSIZE = 1600  # 100ms at 16kHz


class AudioCapture:
    """Captures PCM 16-bit 16kHz mono audio from the microphone.

    Audio bytes are pushed to a queue.Queue for consumption by the STT client.
    """

    def __init__(
        self,
        audio_queue: queue.Queue | None = None,
        vad_config: VADConfig | None = None,
    ):
        self._queue = audio_queue or queue.Queue()
        self._stream: sd.RawInputStream | None = None
        self._recording = False
        self._vad = VoiceActivityDetector(vad_config)

    @property
    def queue(self) -> queue.Queue:
        return self._queue

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        """Start capturing audio from the default input device."""
        if self._recording:
            return

        try:
            self._stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCKSIZE,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._recording = True
            log.info("Audio capture started")
        except sd.PortAudioError:
            log.exception("Failed to start audio capture")
            raise

    def stop(self) -> None:
        """Stop capturing audio."""
        if not self._recording:
            return

        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.exception("Error stopping audio stream")
            self._stream = None

        self._vad.reset()

        # Drain remaining items
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        log.info("Audio capture stopped")

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status) -> None:
        """sounddevice callback — runs in a separate thread."""
        if status:
            log.warning(f"Audio status: {status}")
        if self._recording:
            for frame in self._vad.process(bytes(indata)):
                self._queue.put(frame)

    @staticmethod
    def list_devices() -> str:
        """List available audio input devices."""
        return str(sd.query_devices())
