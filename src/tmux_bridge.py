"""tmux bridge for injecting text into Claude Code sessions."""

import logging
import shutil
import subprocess

log = logging.getLogger(__name__)


class TmuxBridge:
    """Manages connection to a tmux session running Claude Code."""

    def __init__(self, session_name: str = "voice-claude",
                 auto_create: bool = True, auto_run_claude: bool = True):
        self._session_name = session_name
        self._auto_create = auto_create
        self._auto_run_claude = auto_run_claude
        self._connected = False

    @property
    def session_name(self) -> str:
        return self._session_name

    @property
    def is_connected(self) -> bool:
        return self._connected and self._session_exists(self._session_name)

    # --- Public API ---

    def check_tmux_installed(self) -> bool:
        """Check if tmux is available on PATH."""
        return shutil.which("tmux") is not None

    def list_sessions(self) -> list[dict]:
        """List all tmux sessions with their pane commands."""
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}\t#{pane_current_command}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return []

        sessions = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                sessions.append({
                    "session": parts[0],
                    "command": parts[1],
                })
        return sessions

    def detect_claude_session(self) -> str | None:
        """Find a tmux session running claude."""
        for entry in self.list_sessions():
            if "claude" in entry["command"].lower():
                return entry["session"]
        return None

    def connect(self) -> bool:
        """Connect to an existing claude session or create a new one.

        Returns True on success.
        """
        if not self.check_tmux_installed():
            log.error("tmux is not installed")
            return False

        # Try auto-detect first
        detected = self.detect_claude_session()
        if detected:
            self._session_name = detected
            self._connected = True
            log.info(f"Connected to existing session: {detected}")
            return True

        # Check if configured session exists
        if self._session_exists(self._session_name):
            self._connected = True
            log.info(f"Connected to session: {self._session_name}")
            return True

        # Auto-create if enabled
        if self._auto_create:
            return self._create_session()

        log.error("No tmux session found and auto_create is disabled")
        return False

    def send_text(self, text: str) -> bool:
        """Send text and press Enter."""
        return self.type_text(text) and self.send_enter()

    def type_text(self, text: str) -> bool:
        """Type text into the tmux pane WITHOUT pressing Enter.

        Uses load-buffer + paste-buffer for safe handling of special characters.
        """
        if not self.is_connected:
            log.error("Not connected to tmux session")
            return False

        try:
            result = subprocess.run(
                ["tmux", "load-buffer", "-"],
                input=text.encode("utf-8"),
                capture_output=True,
            )
            if result.returncode != 0:
                log.error(f"load-buffer failed: {result.stderr.decode()}")
                return False

            result = subprocess.run(
                ["tmux", "paste-buffer", "-t", self._session_name],
                capture_output=True,
            )
            if result.returncode != 0:
                log.error(f"paste-buffer failed: {result.stderr.decode()}")
                return False

            log.info(f"Typed {len(text)} chars to {self._session_name}")
            return True

        except Exception:
            log.exception("Failed to type text to tmux")
            return False

    def send_enter(self) -> bool:
        """Press Enter in the tmux pane to submit."""
        if not self.is_connected:
            log.error("Not connected to tmux session")
            return False

        try:
            result = subprocess.run(
                ["tmux", "send-keys", "-t", self._session_name, "Enter"],
                capture_output=True,
            )
            if result.returncode != 0:
                log.error(f"send-keys failed: {result.stderr.decode()}")
                return False

            log.info(f"Pressed Enter in {self._session_name}")
            return True

        except Exception:
            log.exception("Failed to send Enter to tmux")
            return False

    def health_check(self) -> bool:
        """Check if the session is still alive."""
        if not self._connected:
            return False
        alive = self._session_exists(self._session_name)
        if not alive:
            self._connected = False
        return alive

    # --- Private ---

    def _session_exists(self, name: str) -> bool:
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True,
        )
        return result.returncode == 0

    def _create_session(self) -> bool:
        """Create a new tmux session, optionally running claude."""
        try:
            result = subprocess.run(
                ["tmux", "new-session", "-d", "-s", self._session_name],
                capture_output=True,
            )
            if result.returncode != 0:
                log.error(
                    f"Failed to create session: {result.stderr.decode()}"
                )
                return False

            if self._auto_run_claude:
                subprocess.run(
                    ["tmux", "send-keys", "-t", self._session_name,
                     "claude", "Enter"],
                    capture_output=True,
                )
                log.info(
                    f"Created session '{self._session_name}' and started claude"
                )
            else:
                log.info(f"Created session '{self._session_name}'")

            self._connected = True
            return True

        except Exception:
            log.exception("Failed to create tmux session")
            return False
