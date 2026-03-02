"""App orchestrator — wires all components together."""

import logging
import queue
import time

from pynput.keyboard import Key, Listener as KeyboardListener

from src.audio_capture import AudioCapture
from src.config import AppConfig
from src.overlay import OverlayWindow, UIState
from src.soniox_client import SonioxClient, TranscriptUpdate
from src.tmux_bridge import TmuxBridge

log = logging.getLogger(__name__)

# Map config hotkey names to pynput keys
_HOTKEY_MAP = {
    "F1": Key.f1, "F2": Key.f2, "F3": Key.f3, "F4": Key.f4,
    "F5": Key.f5, "F6": Key.f6, "F7": Key.f7, "F8": Key.f8,
    "F9": Key.f9, "F10": Key.f10, "F11": Key.f11, "F12": Key.f12,
}


class VoiceClaudeApp:
    """Main application — orchestrates UI, audio, STT, and tmux."""

    def __init__(self, config: AppConfig):
        self._config = config

        # Shared queues
        self._audio_queue: queue.Queue = queue.Queue()
        self._transcript_queue: queue.Queue = queue.Queue()

        # Components
        self._tmux = TmuxBridge(
            session_name=config.tmux.session_name,
            auto_create=config.tmux.auto_create,
            auto_run_claude=config.tmux.auto_run_claude,
        )
        self._audio = AudioCapture(
            audio_queue=self._audio_queue,
            vad_config=config.vad,
        )
        self._soniox = SonioxClient(
            api_key=config.soniox.api_key,
            model=config.soniox.model,
            language_hints=config.soniox.language_hints,
            endpoint_detection=config.soniox.endpoint_detection,
            context_terms=config.soniox.context_terms,
            audio_queue=self._audio_queue,
            transcript_queue=self._transcript_queue,
        )
        self._overlay: OverlayWindow | None = None

        # State
        self._listening = False
        self._auto_send_timer: str | None = None  # after() ID
        self._auto_send_start: float = 0
        self._countdown_job: str | None = None
        self._last_final_text = ""
        self._typed_len = 0  # how many chars of final_text already typed to tmux

        # Hotkey
        self._hotkey_listener: KeyboardListener | None = None

        # Health check
        self._health_check_job: str | None = None

    def run(self) -> None:
        """Start the application (blocks on Tkinter mainloop)."""
        # Create overlay
        self._overlay = OverlayWindow(
            width=self._config.overlay.width,
            height=self._config.overlay.height,
            opacity=self._config.overlay.opacity,
            position=self._config.overlay.position,
            theme=self._config.overlay.theme,
        )

        # Wire callbacks
        self._overlay.on_mic_toggle = self._handle_mic_toggle
        self._overlay.on_close = self._handle_close

        # Connect to tmux
        if not self._tmux.check_tmux_installed():
            self._overlay.set_state(
                UIState.ERROR, "tmux not installed (brew install tmux)"
            )
        elif not self._tmux.connect():
            self._overlay.set_state(
                UIState.DISCONNECTED,
                "No tmux session found",
            )
        else:
            self._overlay.set_state(
                UIState.IDLE,
                f"Connected: {self._tmux.session_name}",
            )

        # Start polling transcript queue
        self._poll_transcript()

        # Start tmux health check
        self._schedule_health_check()

        # Bind hotkey on the overlay window (works when focused, no Accessibility needed)
        hotkey_name = self._config.voice.hotkey
        tk_key = f"<{hotkey_name}>"  # e.g. "<F5>"
        self._overlay.bind_all(tk_key, lambda e: self._handle_mic_toggle())
        log.info(f"Bound {hotkey_name} on overlay window")

        # Also try pynput for global hotkey (needs macOS Accessibility permission)
        self._start_hotkey_listener()

        # Run Tkinter mainloop
        log.info("Starting overlay mainloop")
        self._overlay.mainloop()

    # --- Mic toggle ---

    def _handle_mic_toggle(self) -> None:
        """Toggle listening on/off."""
        if self._listening:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self) -> None:
        """Start recording + STT."""
        if self._listening:
            return

        if not self._tmux.is_connected:
            # Try reconnect
            if self._tmux.connect():
                self._overlay.set_state(
                    UIState.IDLE,
                    f"Reconnected: {self._tmux.session_name}",
                )
            else:
                self._overlay.set_state(
                    UIState.DISCONNECTED, "No tmux session"
                )
                return

        self._cancel_auto_send()
        self._last_final_text = ""
        self._typed_len = 0
        self._overlay.clear_transcript()

        try:
            self._audio.start()
            self._soniox.start()
            self._listening = True
            self._overlay.set_state(UIState.LISTENING)
            log.info("Started listening")
        except Exception as e:
            log.exception("Failed to start listening")
            self._overlay.set_state(UIState.ERROR, str(e)[:50])

    def _stop_listening(self) -> None:
        """Stop recording + STT. Type remaining text and send (Enter)."""
        if not self._listening:
            return

        self._listening = False

        try:
            self._audio.stop()
            self._soniox.stop()
        except Exception:
            log.exception("Error stopping audio/soniox")

        # Type any remaining un-typed text and send
        final_text = self._strip_tags(
            self._soniox.get_final_text() or self._last_final_text
        )
        if final_text:
            remaining = final_text[self._typed_len:]
            if remaining.strip():
                prefix = " " if self._typed_len > 0 else ""
                self._tmux.type_text(prefix + remaining.strip())
            self._tmux.send_enter()
            self._overlay.set_state(UIState.IDLE, "Sent!")
            self._overlay.after(
                2000, lambda: self._overlay.clear_transcript()
            )
        else:
            self._overlay.set_state(UIState.IDLE, "No speech detected")
        self._typed_len = 0

    # --- Auto-send with countdown ---

    def _schedule_send(self, text: str) -> None:
        """Schedule sending text after auto_send_delay."""
        delay_ms = self._config.voice.auto_send_delay_ms

        if self._config.voice.auto_send and delay_ms > 0:
            self._auto_send_start = time.time()
            self._overlay.update_transcript(final_text=text)
            self._update_countdown(text, delay_ms / 1000.0)
        else:
            self._send_text(text)

    def _update_countdown(self, text: str, total_seconds: float) -> None:
        """Update countdown display and eventually send."""
        elapsed = time.time() - self._auto_send_start
        remaining = total_seconds - elapsed

        if remaining <= 0:
            self._send_text(text)
            return

        self._overlay.show_countdown(remaining)
        self._countdown_job = self._overlay.after(
            50, lambda: self._update_countdown(text, total_seconds)
        )

    def _cancel_auto_send(self) -> None:
        """Cancel pending auto-send."""
        if self._countdown_job:
            self._overlay.after_cancel(self._countdown_job)
            self._countdown_job = None
        if self._auto_send_timer:
            self._overlay.after_cancel(self._auto_send_timer)
            self._auto_send_timer = None

    def _send_text(self, text: str) -> None:
        """Send text to tmux."""
        self._cancel_auto_send()
        text = self._strip_tags(text)
        self._overlay.set_state(UIState.SENDING)

        success = self._tmux.send_text(text)
        if success:
            self._overlay.set_state(UIState.IDLE, "Sent!")
            self._overlay.update_transcript(final_text=text)
            # Clear transcript after a short delay
            self._overlay.after(
                2000, lambda: self._overlay.clear_transcript()
            )
        else:
            self._overlay.set_state(UIState.ERROR, "Failed to send to tmux")

    # --- Transcript polling ---

    def _poll_transcript(self) -> None:
        """Poll transcript_queue every 50ms.

        Streams final text to Claude in real-time.
        On end phrase → press Enter to submit.
        """
        try:
            while True:
                update: TranscriptUpdate = self._transcript_queue.get_nowait()

                if update.final_text:
                    self._last_final_text = update.final_text

                if not self._listening:
                    continue

                # Display on overlay
                display_final = self._strip_tags(update.final_text or "")
                display_pending = self._strip_tags(update.pending_text or "")
                self._overlay.update_transcript(
                    final_text=display_final,
                    pending_text=display_pending,
                )

                # Check end phrase in combined (final + pending)
                combined = (update.final_text or "") + (update.pending_text or "")
                if combined:
                    end_result = self._check_end_phrase(combined)
                    if end_result is not None:
                        log.info(f"End phrase → submit: {end_result!r}")
                        self._listening = False
                        try:
                            self._audio.stop()
                            self._soniox.stop()
                        except Exception:
                            log.exception("Error stopping audio/soniox")
                        # Type whatever hasn't been typed yet
                        remaining = end_result[self._typed_len:]
                        if remaining:
                            self._tmux.type_text(remaining)
                        self._tmux.send_enter()
                        self._typed_len = 0
                        self._overlay.set_state(UIState.IDLE, "Sent!")
                        self._overlay.after(
                            2000, lambda: self._overlay.clear_transcript()
                        )
                        break  # Stop processing queue but keep polling

                # Stream new final text to Claude (no Enter)
                clean_final = self._strip_tags(update.final_text or "")
                if len(clean_final) > self._typed_len:
                    new_text = clean_final[self._typed_len:]
                    if new_text.strip():
                        self._tmux.type_text(new_text)
                        log.debug(f"Streamed: {new_text!r}")
                    self._typed_len = len(clean_final)

        except queue.Empty:
            pass

        if self._overlay:
            self._overlay.after(50, self._poll_transcript)

    @staticmethod
    def _strip_tags(text: str) -> str:
        """Remove <end> and similar tags from text."""
        import re
        return re.sub(r"<\s*end\s*>", "", text, flags=re.IGNORECASE).strip()

    def _check_end_phrase(self, text: str) -> str | None:
        """Check if text ends with an end phrase.

        Returns cleaned text (including the phrase) if found, None otherwise.
        Handles <end> tags, trailing punctuation, and Unicode normalization.
        """
        import re
        import unicodedata
        # Strip <end> tags and normalize
        cleaned_text = self._strip_tags(text)
        normalized = unicodedata.normalize("NFC", cleaned_text)
        # Collapse whitespace and strip punctuation
        text_clean = re.sub(r"\s+", " ", normalized).rstrip(".,;:!?… ").lower()
        for phrase in self._config.voice.end_phrases:
            phrase_norm = unicodedata.normalize("NFC", phrase.lower())
            if text_clean.endswith(phrase_norm):
                log.info(f"End phrase '{phrase}' matched in: {text!r}")
                return text_clean
        return None

    # --- Health check ---

    def _schedule_health_check(self) -> None:
        """Check tmux health every 5 seconds."""
        if not self._overlay:
            return

        if self._tmux._connected and not self._tmux.health_check():
            self._overlay.set_state(
                UIState.DISCONNECTED, "tmux session lost"
            )
            if self._listening:
                self._stop_listening()

        self._health_check_job = self._overlay.after(
            5000, self._schedule_health_check
        )

    # --- Global hotkey ---

    def _start_hotkey_listener(self) -> None:
        """Start listening for the global hotkey."""
        hotkey_name = self._config.voice.hotkey
        target_key = _HOTKEY_MAP.get(hotkey_name)

        if not target_key:
            log.warning(f"Unknown hotkey: {hotkey_name}, skipping")
            return

        def on_press(key):
            if key == target_key:
                if self._overlay:
                    self._overlay.after(0, self._handle_mic_toggle)

        try:
            self._hotkey_listener = KeyboardListener(on_press=on_press)
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
            log.info(
                f"Global hotkey {hotkey_name} registered. "
                "If it doesn't work, grant Accessibility permission: "
                "System Settings > Privacy & Security > Accessibility > add your terminal app"
            )
        except Exception:
            log.warning(
                f"Failed to register global hotkey {hotkey_name}. "
                "Click the overlay window and press {hotkey_name} instead."
            )

    # --- Shutdown ---

    def _handle_close(self) -> None:
        """Graceful shutdown."""
        log.info("Shutting down")

        if self._listening:
            self._audio.stop()
            self._soniox.stop()

        if self._hotkey_listener:
            self._hotkey_listener.stop()

        if self._overlay:
            self._overlay.save_position()
            self._overlay.destroy()
            self._overlay = None
