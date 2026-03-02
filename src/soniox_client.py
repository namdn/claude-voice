"""Soniox real-time STT client using raw WebSocket."""

import asyncio
import json
import logging
import queue
import threading
from dataclasses import dataclass

import websockets

log = logging.getLogger(__name__)

SONIOX_WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"


@dataclass
class TranscriptUpdate:
    """Represents a transcript update from Soniox."""
    final_text: str  # Confirmed text so far
    pending_text: str  # Non-final text (may change)
    is_endpoint: bool  # True when speaker paused (endpoint detected)
    is_finished: bool  # True when session ended


class SonioxClient:
    """Real-time speech-to-text via Soniox WebSocket API.

    Runs its own asyncio event loop in a background thread.
    Reads audio from audio_queue, writes TranscriptUpdates to transcript_queue.
    """

    def __init__(self, api_key: str, model: str = "stt-rt-preview",
                 language_hints: list[str] | None = None,
                 endpoint_detection: bool = True,
                 context_terms: list[str] | None = None,
                 audio_queue: queue.Queue | None = None,
                 transcript_queue: queue.Queue | None = None):
        self._api_key = api_key
        self._model = model
        self._language_hints = language_hints or ["vi", "en"]
        self._endpoint_detection = endpoint_detection
        self._context_terms = context_terms or []
        self._audio_queue = audio_queue or queue.Queue()
        self._transcript_queue = transcript_queue or queue.Queue()

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._ws = None
        self._session_task: asyncio.Task | None = None

        # Accumulated transcript
        self._final_tokens: list[str] = []
        self._pending_text = ""

        # Reconnect state
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._should_reconnect = False

    @property
    def audio_queue(self) -> queue.Queue:
        return self._audio_queue

    @property
    def transcript_queue(self) -> queue.Queue:
        return self._transcript_queue

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the STT client in a background thread."""
        if self._running:
            return

        self._running = True
        self._should_reconnect = True
        self._reconnect_attempts = 0
        self._final_tokens = []
        self._pending_text = ""

        # Drain audio queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        self._thread = threading.Thread(
            target=self._run_event_loop, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the STT client and finalize."""
        self._running = False
        self._should_reconnect = False

        # Signal the send loop to exit
        self._audio_queue.put(None)

        # Close websocket to unblock receive loop
        if self._ws:
            asyncio.run_coroutine_threadsafe(
                self._ws.close(), self._loop
            )

        # Cancel the session task so run_until_complete finishes cleanly
        if self._session_task and self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._session_task.cancel)

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def get_final_text(self) -> str:
        """Get the accumulated final transcript."""
        return "".join(self._final_tokens).strip()

    # --- Background thread ---

    def _run_event_loop(self) -> None:
        """Run asyncio event loop in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._session_task = self._loop.create_task(
                self._session_with_reconnect()
            )
            self._loop.run_until_complete(self._session_task)
        except asyncio.CancelledError:
            log.debug("Soniox session cancelled (normal shutdown)")
        except Exception:
            log.exception("Soniox event loop crashed")
        finally:
            self._session_task = None
            self._loop.close()
            self._loop = None

    async def _session_with_reconnect(self) -> None:
        """Run STT session with automatic reconnect."""
        while self._running and self._should_reconnect:
            try:
                await self._run_session()
                # Clean exit (stop was called)
                break
            except websockets.exceptions.ConnectionClosed as e:
                log.warning(f"WebSocket closed: {e}")
            except Exception:
                log.exception("Soniox session error")

            if not self._should_reconnect or not self._running:
                break

            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnect_attempts:
                log.error("Max reconnect attempts reached")
                self._emit_update(is_finished=True)
                break

            delay = min(2 ** self._reconnect_attempts, 30)
            log.info(
                f"Reconnecting in {delay}s "
                f"(attempt {self._reconnect_attempts})"
            )
            await asyncio.sleep(delay)

    async def _run_session(self) -> None:
        """Run a single STT WebSocket session."""
        config_msg = self._build_config()

        async with websockets.connect(SONIOX_WS_URL) as ws:
            self._ws = ws
            self._reconnect_attempts = 0
            log.info("Connected to Soniox")

            # Send config
            await ws.send(json.dumps(config_msg))

            # Run send and receive concurrently
            send_task = asyncio.create_task(self._send_audio(ws))
            recv_task = asyncio.create_task(self._receive_tokens(ws))

            try:
                await asyncio.gather(send_task, recv_task)
            except asyncio.CancelledError:
                pass
            finally:
                self._ws = None

    async def _send_audio(self, ws) -> None:
        """Read from audio_queue and send binary frames."""
        try:
            while self._running:
                try:
                    data = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self._audio_queue.get(timeout=0.1)
                    )
                except queue.Empty:
                    continue

                if data is None:
                    # Stop signal — send empty frame to finalize
                    await ws.send(b"")
                    log.debug("Sent finalize frame")
                    return

                await ws.send(data)

        except websockets.exceptions.ConnectionClosed:
            log.warning("Connection closed during send")
        except Exception:
            if self._running:
                log.exception("Error in send_audio")

    async def _receive_tokens(self, ws) -> None:
        """Receive and process token responses from Soniox."""
        try:
            async for message in ws:
                if not self._running:
                    break

                if isinstance(message, bytes):
                    continue  # Skip binary messages

                data = json.loads(message)
                self._process_response(data)

                if data.get("finished"):
                    log.info("Soniox session finished")
                    return

        except websockets.exceptions.ConnectionClosed:
            log.warning("Connection closed during receive")
        except Exception:
            if self._running:
                log.exception("Error in receive_tokens")

    def _process_response(self, data: dict) -> None:
        """Process a Soniox response message.

        Handles both legacy token format and stt-rt-v4 word-level format.
        """
        tokens = data.get("tokens", [])
        words = data.get("words", [])
        fw = data.get("fw", [])
        nfw = data.get("nfw", [])

        log.debug(f"Soniox resp tokens={len(tokens)} fw={len(fw)} nfw={len(nfw)}")

        is_endpoint = False

        # --- Format 1: stt-rt-v4 uses "fw" (final words) and "nfw" (non-final words) ---
        if fw or nfw:
            for word_obj in fw:
                # fw items can be strings or dicts with "text" key
                if isinstance(word_obj, str):
                    self._final_tokens.append(word_obj)
                elif isinstance(word_obj, dict):
                    self._final_tokens.append(word_obj.get("text", ""))
            # nfw = non-final (speculative) words
            pending_parts = []
            for word_obj in nfw:
                if isinstance(word_obj, str):
                    pending_parts.append(word_obj)
                elif isinstance(word_obj, dict):
                    pending_parts.append(word_obj.get("text", ""))
            self._pending_text = "".join(pending_parts)

        # --- Format 2: "words" array (some Soniox models) ---
        elif words:
            for word_obj in words:
                text = word_obj.get("text", "") if isinstance(word_obj, dict) else str(word_obj)
                is_final = word_obj.get("is_final", False) if isinstance(word_obj, dict) else False
                if is_final:
                    self._final_tokens.append(text)
                    self._pending_text = ""
                else:
                    self._pending_text = text

        # --- Format 3: legacy "tokens" array ---
        elif tokens:
            for token in tokens:
                text = token.get("text", "")
                is_final = token.get("is_final", False)
                if is_final:
                    self._final_tokens.append(text)
                    self._pending_text = ""
                else:
                    self._pending_text = text

        # Check for endpoint
        if data.get("endpoint_detected") or data.get("is_endpoint"):
            is_endpoint = True

        is_finished = data.get("finished", False)

        self._emit_update(
            is_endpoint=is_endpoint,
            is_finished=is_finished,
        )

    def _emit_update(self, is_endpoint: bool = False,
                     is_finished: bool = False) -> None:
        """Emit a TranscriptUpdate to the transcript queue."""
        update = TranscriptUpdate(
            final_text="".join(self._final_tokens).strip(),
            pending_text=self._pending_text,
            is_endpoint=is_endpoint,
            is_finished=is_finished,
        )
        self._transcript_queue.put(update)

    def _build_config(self) -> dict:
        """Build the Soniox config message."""
        config: dict = {
            "api_key": self._api_key,
            "model": self._model,
            "audio_format": "pcm_s16le",
            "sample_rate": 16000,
            "num_channels": 1,
            "language_hints": self._language_hints,
            "enable_endpoint_detection": self._endpoint_detection,
        }

        if self._context_terms:
            config["context"] = {
                "entries": [
                    {"phrases": self._context_terms}
                ]
            }

        return config
