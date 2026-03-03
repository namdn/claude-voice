"""Floating overlay UI using CustomTkinter — glassmorphism design."""

import enum
import json
import logging
import math
from pathlib import Path
from typing import Callable

import customtkinter as ctk

log = logging.getLogger(__name__)

POSITION_CACHE = Path(".window_position.json")


class UIState(enum.Enum):
    SETUP = "setup"
    IDLE = "idle"
    LISTENING = "listening"
    SENDING = "sending"
    ERROR = "error"
    DISCONNECTED = "disconnected"


# ---------------------------------------------------------------------------
# Theme system
# ---------------------------------------------------------------------------

THEMES = {
    "dark": {
        "bg": "#0f0f1a",
        "card": "#1a1a2e",
        "border": "#2a2a4a",
        "border_glow": "#6366f1",
        "text_primary": "#e2e8f0",
        "text_secondary": "#94a3b8",
        "accent": "#818cf8",
        "accent_hover": "#6366f1",
        "red": "#f87171",
        "red_hover": "#ef4444",
        "green": "#4ade80",
        "orange": "#fbbf24",
        "muted": "#475569",
        "close_bg": "#1e1e3a",
        "close_hover": "#7f1d1d",
        "appearance_mode": "dark",
    },
    "light": {
        "bg": "#f8fafc",
        "card": "#ffffff",
        "border": "#e2e8f0",
        "border_glow": "#6366f1",
        "text_primary": "#1e293b",
        "text_secondary": "#64748b",
        "accent": "#6366f1",
        "accent_hover": "#4f46e5",
        "red": "#ef4444",
        "red_hover": "#dc2626",
        "green": "#22c55e",
        "orange": "#f59e0b",
        "muted": "#94a3b8",
        "close_bg": "#f1f5f9",
        "close_hover": "#fecaca",
        "appearance_mode": "light",
    },
}


def _state_styles(t: dict) -> dict:
    """Build per-state styles from a theme palette."""
    return {
        UIState.SETUP: {
            "status_text": "Setup",
            "status_color": t["orange"],
            "mic_color": t["muted"],
            "mic_hover": t["muted"],
            "mic_text": "\U0001f3a4",
        },
        UIState.IDLE: {
            "status_text": "Bam de noi",
            "status_color": t["green"],
            "mic_color": t["accent"],
            "mic_hover": t["accent_hover"],
            "mic_text": "\U0001f3a4",
        },
        UIState.LISTENING: {
            "status_text": "Dang nghe...",
            "status_color": t["red"],
            "mic_color": t["red"],
            "mic_hover": t["red_hover"],
            "mic_text": "\u25a0",
        },
        UIState.SENDING: {
            "status_text": "Dang gui...",
            "status_color": t["orange"],
            "mic_color": t["muted"],
            "mic_hover": t["muted"],
            "mic_text": "\u2022\u2022\u2022",
        },
        UIState.ERROR: {
            "status_text": "Loi!",
            "status_color": t["red"],
            "mic_color": t["muted"],
            "mic_hover": t["muted"],
            "mic_text": "\U0001f3a4",
        },
        UIState.DISCONNECTED: {
            "status_text": "Chua ket noi tmux",
            "status_color": t["red"],
            "mic_color": t["muted"],
            "mic_hover": t["muted"],
            "mic_text": "\U0001f3a4",
        },
    }


# ---------------------------------------------------------------------------
# Color utilities
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _interpolate_color(c1: str, c2: str, t: float) -> str:
    """Linear interpolation between two hex colors, t in [0, 1]."""
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    t = max(0.0, min(1.0, t))
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return _rgb_to_hex(r, g, b)


# ---------------------------------------------------------------------------
# OverlayWindow
# ---------------------------------------------------------------------------

class OverlayWindow(ctk.CTk):
    """Floating overlay window for voice input with glassmorphism design."""

    def __init__(self, width: int = 300, height: int = 44,
                 opacity: float = 0.85, position: str = "bottom-right",
                 theme: str = "dark"):
        super().__init__()
        self.title("claude-voice")

        self._width = width
        self._height = height
        self._expanded_height = 70  # When transcript is showing
        self._position = position
        self._state = UIState.IDLE
        self._drag_x = 0
        self._drag_y = 0

        # Theme
        self._theme_name = theme if theme in THEMES else "dark"
        self._t = THEMES[self._theme_name]
        self._styles = _state_styles(self._t)

        self._setup_height = 72  # Height when in SETUP state

        # Callbacks (set by app orchestrator)
        self.on_mic_toggle: Callable | None = None
        self.on_close: Callable | None = None
        self.on_reconnect: Callable | None = None
        self.on_session_select: Callable[[str], None] | None = None

        # Animation state
        self._pulse_job: str | None = None
        self._pulse_phase = 0.0
        self._transition_job: str | None = None

        # --- Window setup ---
        ctk.set_appearance_mode(self._t["appearance_mode"])
        ctk.set_default_color_theme("blue")

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 1.0)
        self.configure(fg_color=self._t["card"])

        self.geometry(f"{width}x{height}")
        self._set_initial_position()

        # --- Main frame (glassmorphism card) ---
        self._frame = ctk.CTkFrame(
            self, fg_color=self._t["card"], corner_radius=12,
            border_width=2, border_color=self._t["muted"],
        )
        self._frame.pack(fill="both", expand=True, padx=0, pady=0)

        # Make draggable
        self._frame.bind("<ButtonPress-1>", self._on_drag_start)
        self._frame.bind("<B1-Motion>", self._on_drag_motion)

        # --- Single row: [MIC] [dot] status ... [X] ---
        main_row = ctk.CTkFrame(self._frame, fg_color="transparent")
        main_row.pack(fill="x", padx=6, pady=4)

        self._mic_btn = ctk.CTkButton(
            main_row, text="\U0001f3a4", width=28, height=28,
            font=("", 12),
            fg_color=self._t["accent"],
            hover_color=self._t["accent_hover"],
            text_color="#ffffff",
            corner_radius=14,
            border_width=1,
            border_color=self._t["accent"],
            command=self._handle_mic_toggle,
        )
        self._mic_btn.pack(side="left")

        self._status_dot = ctk.CTkLabel(
            main_row, text="\u25cf", width=10, font=("", 8),
            text_color=self._t["green"],
        )
        self._status_dot.pack(side="left", padx=(6, 0))

        self._status_label = ctk.CTkLabel(
            main_row, text="claude-voice",
            font=("SF Pro Display", 11),
            text_color=self._t["text_secondary"],
            anchor="w",
        )
        self._status_label.pack(side="left", padx=(3, 0), fill="x", expand=True)

        for w in (main_row, self._status_label):
            w.bind("<ButtonPress-1>", self._on_drag_start)
            w.bind("<B1-Motion>", self._on_drag_motion)

        self._close_btn = ctk.CTkButton(
            main_row, text="\u2715", width=20, height=20,
            font=("", 10), fg_color=self._t["close_bg"],
            hover_color=self._t["close_hover"],
            text_color=self._t["text_secondary"],
            corner_radius=10,
            command=self._handle_close,
        )
        self._close_btn.pack(side="right")

        # --- Transcript row (hidden by default, expands window) ---
        self._transcript_label = ctk.CTkLabel(
            self._frame, text="",
            font=("SF Mono", 10),
            text_color=self._t["text_primary"],
            anchor="w", wraplength=width - 20,
        )
        # Not packed — shown only when there's text

        # --- Setup row (dropdown + Connect button, hidden by default) ---
        self._setup_row = ctk.CTkFrame(self._frame, fg_color="transparent")
        self._tmux_label = ctk.CTkLabel(
            self._setup_row, text="tmux:",
            font=("SF Pro Display", 11),
            text_color=self._t["text_secondary"],
        )
        self._tmux_label.pack(side="left", padx=(0, 4))
        self._session_var = ctk.StringVar(value="")
        self._session_dropdown = ctk.CTkOptionMenu(
            self._setup_row,
            variable=self._session_var,
            values=[""],
            width=180, height=26,
            font=("SF Pro Display", 11),
            fg_color=self._t["border"],
            button_color=self._t["border"],
            button_hover_color=self._t["muted"],
            dropdown_fg_color=self._t["bg"],
            dropdown_hover_color=self._t["border"],
            dropdown_text_color=self._t["text_primary"],
            text_color=self._t["text_primary"],
            corner_radius=6,
        )
        self._session_dropdown._dropdown_menu._corner_radius = 4
        self._session_dropdown.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._connect_btn = ctk.CTkButton(
            self._setup_row, text="Connect", width=70, height=26,
            font=("SF Pro Display", 11),
            fg_color=self._t["accent"],
            hover_color=self._t["accent_hover"],
            text_color="#ffffff",
            corner_radius=6,
            command=self._handle_connect,
        )
        self._connect_btn.pack(side="right")
        # Not packed yet — shown only during SETUP state

        # --- Countdown label (hidden by default) ---
        self._countdown_label = ctk.CTkLabel(
            self._frame, text="",
            font=("SF Mono", 9),
            text_color=self._t["orange"],
        )

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def show_setup(self, sessions: list[str]) -> None:
        """Show the setup dropdown with a list of tmux sessions."""
        if sessions:
            self._session_var.set(sessions[0])
            self._session_dropdown.configure(values=sessions)
        else:
            self._session_var.set("")
            self._session_dropdown.configure(values=[""])
        self._setup_row.pack(fill="x", padx=8, pady=(0, 3))
        self.geometry(f"{self._width}x{self._setup_height}")
        self._mic_btn.configure(state="disabled")

    def hide_setup(self) -> None:
        """Hide the setup dropdown (after connecting)."""
        self._setup_row.pack_forget()
        self.geometry(f"{self._width}x{self._height}")
        self._mic_btn.configure(state="normal")

    def set_state(self, state: UIState, message: str | None = None) -> None:
        """Update the visual state of the overlay with smooth transition."""
        prev_state = self._state
        self._state = state
        style = self._styles[state]

        status_text = message or style["status_text"]
        self._status_label.configure(text=status_text)

        # Smooth color transition for status dot
        if prev_state != state:
            prev_color = self._styles[prev_state]["status_color"]
            new_color = style["status_color"]
            self._animate_color_transition(
                self._status_dot, "text_color",
                prev_color, new_color,
            )
        else:
            self._status_dot.configure(text_color=style["status_color"])

        self._mic_btn.configure(
            text=style["mic_text"],
            fg_color=style["mic_color"],
            hover_color=style["mic_hover"],
        )

        # Enable/disable mic button
        if state in (UIState.SENDING, UIState.SETUP):
            self._mic_btn.configure(state="disabled")
        else:
            self._mic_btn.configure(state="normal")

        # Pulse management
        if state == UIState.LISTENING:
            self._start_pulse()
        else:
            self._stop_pulse()

        # Border glow during listening
        if state == UIState.LISTENING:
            self._frame.configure(border_color=self._t["border_glow"])
        else:
            self._frame.configure(border_color=self._t["border"])

        self._hide_countdown()

    def update_transcript(self, final_text: str = "",
                          pending_text: str = "") -> None:
        """Update the transcript display. Expand window when text present."""
        display = final_text
        if pending_text:
            if display:
                display += " "
            display += pending_text
        if len(display) > 60:
            display = "..." + display[-57:]

        if display:
            self._transcript_label.configure(text=display)
            if not self._transcript_label.winfo_ismapped():
                self._transcript_label.pack(fill="x", padx=8, pady=(0, 4))
                self.geometry(f"{self._width}x{self._expanded_height}")
        else:
            if self._transcript_label.winfo_ismapped():
                self._transcript_label.pack_forget()
                self.geometry(f"{self._width}x{self._height}")

    def show_countdown(self, seconds_left: float) -> None:
        """Show auto-send countdown."""
        self._countdown_label.configure(
            text=f"Gui trong {seconds_left:.1f}s..."
        )
        self._countdown_label.pack(padx=10, pady=(0, 4))

    def clear_transcript(self) -> None:
        self._transcript_label.configure(text="")
        self._transcript_label.pack_forget()
        self.geometry(f"{self._width}x{self._height}")
        self._hide_countdown()

    def save_position(self) -> None:
        """Save current window position to disk."""
        try:
            pos = {"x": self.winfo_x(), "y": self.winfo_y()}
            POSITION_CACHE.write_text(json.dumps(pos))
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Mic pulse animation
    # -----------------------------------------------------------------------

    def _start_pulse(self) -> None:
        """Start pulsing the mic button border."""
        self._pulse_phase = 0.0
        self._pulse_tick()

    def _stop_pulse(self) -> None:
        """Stop mic button pulse."""
        if self._pulse_job:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None
        self._mic_btn.configure(border_width=1, border_color=self._t["accent"])

    def _pulse_tick(self) -> None:
        """Animate mic button border width sinusoidally."""
        if self._state != UIState.LISTENING:
            return
        self._pulse_phase += 0.1
        # Sinusoidal 1 -> 4
        t = (math.sin(self._pulse_phase) + 1) / 2  # 0 to 1
        bw = int(1 + t * 3)
        glow_color = _interpolate_color(self._t["red"], self._t["red_hover"], t)
        self._mic_btn.configure(border_width=bw, border_color=glow_color)
        self._pulse_job = self.after(50, self._pulse_tick)

    # -----------------------------------------------------------------------
    # Smooth color transitions
    # -----------------------------------------------------------------------

    def _animate_color_transition(
        self, widget, prop: str, from_color: str, to_color: str,
        duration_ms: int = 200, steps: int = 12,
    ) -> None:
        """Animate a widget color property over duration_ms."""
        if self._transition_job:
            self.after_cancel(self._transition_job)
            self._transition_job = None

        step_ms = max(1, duration_ms // steps)
        self._transition_step = 0
        self._transition_steps = steps

        def tick():
            self._transition_step += 1
            t = self._transition_step / self._transition_steps
            color = _interpolate_color(from_color, to_color, t)
            widget.configure(**{prop: color})
            if self._transition_step < self._transition_steps:
                self._transition_job = self.after(step_ms, tick)
            else:
                self._transition_job = None

        tick()

    # -----------------------------------------------------------------------
    # Private — window management
    # -----------------------------------------------------------------------

    def _set_initial_position(self) -> None:
        if POSITION_CACHE.exists():
            try:
                pos = json.loads(POSITION_CACHE.read_text())
                self.geometry(f"+{pos['x']}+{pos['y']}")
                return
            except Exception:
                pass

        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        margin = 20

        positions = {
            "bottom-right": (
                screen_w - self._width - margin,
                screen_h - self._height - margin - 50,
            ),
            "bottom-left": (margin, screen_h - self._height - margin - 50),
            "top-right": (screen_w - self._width - margin, margin),
            "top-left": (margin, margin),
        }
        x, y = positions.get(self._position, positions["bottom-right"])
        self.geometry(f"+{x}+{y}")

    def _on_drag_start(self, event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag_motion(self, event) -> None:
        x = self.winfo_x() + event.x - self._drag_x
        y = self.winfo_y() + event.y - self._drag_y
        self.geometry(f"+{x}+{y}")

    def _handle_mic_toggle(self) -> None:
        if self.on_mic_toggle:
            self.on_mic_toggle()

    def _handle_connect(self) -> None:
        session = self._session_var.get()
        if session and self.on_session_select:
            self.on_session_select(session)

    def _handle_close(self) -> None:
        self.save_position()
        if self.on_close:
            self.on_close()
        else:
            self.destroy()

    def _hide_countdown(self) -> None:
        self._countdown_label.pack_forget()
