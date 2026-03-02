# Implementation Plan: Voice Input for Claude Code

## Context

Dự án `claude-voice` là app desktop floating overlay trên macOS, cho phép điều khiển Claude Code bằng giọng nói. Hiện tại chỉ có file `PROJECT-SPEC.md`, chưa có code nào. Plan này cover Phase 1 + Phase 2 (MVP + Polish).

## Quyết định thiết kế

### 1. Raw WebSocket thay vì Soniox SDK
Dùng `websockets` library trực tiếp thay vì Soniox Python SDK. Lý do: SDK có `receive_events()` blocking iterator khó kết hợp với `send_audio()` song song. Raw websocket cho phép chạy send loop + receive loop trên cùng asyncio event loop, dễ kiểm soát hơn.

### 2. Threading Architecture
```
Main Thread: CustomTkinter mainloop
    ├── Thread: Audio Capture (sounddevice callback → audio_queue)
    └── Thread: Soniox WebSocket (asyncio event loop)
            ├── Đọc audio_queue → gửi binary frames
            ├── Nhận tokens → transcript_queue
            └── Main thread poll transcript_queue via root.after(50ms)
```
Đơn giản hơn spec gốc (không cần bridge asyncio với Tkinter). Giao tiếp giữa threads qua `queue.Queue`.

### 3. tmux injection: `load-buffer` + `paste-buffer`
An toàn hơn `send-keys` trực tiếp, xử lý tốt ký tự đặc biệt (`"`, `$`, `` ` ``).

---

## Các bước implement (7 steps)

### Step 1: Project Scaffolding + Config Loader
**Files tạo mới:**
- `main.py` — entry point
- `requirements.txt` — dependencies
- `config.example.yaml` — config mẫu
- `.gitignore`
- `src/__init__.py`
- `src/config.py` — dataclass-based config loader, YAML + env var fallback

**Dependencies:** customtkinter, sounddevice, websockets, numpy, pyyaml, pynput

**Verify:** `python main.py` load config không lỗi

---

### Step 2: tmux Bridge
**File:** `src/tmux_bridge.py`

Class `TmuxBridge` với:
- `check_tmux_installed()` — kiểm tra tmux có trên PATH
- `list_sessions()` — liệt kê sessions với pane command
- `detect_claude_session()` — tìm session đang chạy claude
- `connect()` — auto-detect hoặc tạo session mới
- `send_text(text)` — `tmux load-buffer` + `paste-buffer` + `send-keys Enter`
- `is_connected()` — health check

**Verify:** Tạo tmux session, gửi text, verify bằng `tmux capture-pane -p`

---

### Step 3: Audio Capture
**File:** `src/audio_capture.py`

Class `AudioCapture`:
- PCM 16-bit, 16kHz, mono
- `sounddevice.RawInputStream` với callback
- `BLOCKSIZE = 1600` (100ms chunks)
- Audio bytes → `queue.Queue`
- `start()` / `stop()` / `is_recording()`

**Verify:** Record 2s audio, verify ~20 chunks x 3200 bytes

---

### Step 4: Soniox STT Client
**File:** `src/soniox_client.py`

Class `SonioxClient`:
- Raw `websockets` + asyncio trong background thread
- Config frame: api_key, model, audio_format="pcm_s16le", sample_rate=16000, language_hints, endpoint_detection, context terms
- `_send_audio()`: đọc audio_queue → binary frames
- `_receive_tokens()`: parse tokens, phân biệt final/non-final
- `TranscriptUpdate` dataclass → transcript_queue cho UI
- Empty frame `b""` để finalize

**Verify:** Record 3s speech, verify real-time transcript + final text

---

### Step 5: Floating Overlay UI
**File:** `src/overlay.py`

Class `OverlayWindow(ctk.CTk)`:
- `overrideredirect(True)` — borderless
- `attributes("-topmost", True)` — always on top
- `attributes("-alpha", 0.85)` — semi-transparent
- Draggable via `<ButtonPress-1>` + `<B1-Motion>`
- UI elements: status label, transcript label, mic button, close button
- `UIState` enum: IDLE, LISTENING, SENDING, ERROR, DISCONNECTED
- `set_state(state, message)` — update visual state
- `update_transcript(final_text, pending_text)` — real-time display
- Position: bottom-right (configurable)

**Verify:** Chạy standalone, verify drag, close, state transitions

---

### Step 6: App Orchestrator
**File:** `src/app.py`

Class `VoiceClaudeApp` — wires everything:
- Khởi tạo TmuxBridge, AudioCapture, SonioxClient
- Wire UI callbacks: `on_mic_toggle`, `on_close`
- `_start_listening()`: audio.start() → soniox.start()
- `_stop_listening()`: audio.stop() → soniox.stop()
- `_poll_transcript()`: poll transcript_queue mỗi 50ms, update UI
- `_send_text()`: auto-send sau delay hoặc chờ user confirm
- Error handling + graceful shutdown

**Verify:** End-to-end test: tmux session → overlay → nói → text xuất hiện trong tmux

---

### Step 7: Polish + Edge Cases
- tmux health check mỗi 5s
- Soniox reconnect on error
- Audio device error handling
- Graceful shutdown (thread join with timeout)
- Config validation (api_key required)

---

### Step 8: Global Hotkey (Phase 2)
**Thêm vào:** `src/app.py`

- Dùng `pynput.keyboard.GlobalHotKeys` để listen F5 (configurable)
- F5 toggle mic on/off (giống bấm nút MIC trên UI)
- Chạy trong background thread, gọi `_handle_mic_toggle()` qua `overlay.after()`

---

### Step 9: Auto-endpoint Detection + Auto-send (Phase 2)
**Modify:** `src/soniox_client.py`, `src/app.py`

- Soniox `enable_endpoint_detection: true` → nhận biết khi user ngừng nói
- Khi endpoint detected → tự động finalize + gửi text sau `auto_send_delay_ms` (500ms)
- User không cần bấm STOP — nói xong là tự gửi
- UI hiển thị countdown trước khi gửi, cho phép cancel

---

### Step 10: Reconnect Logic (Phase 2)
**Modify:** `src/soniox_client.py`, `src/tmux_bridge.py`, `src/app.py`

- Soniox WebSocket disconnect → auto-reconnect với exponential backoff
- tmux session mất → detect + hiển thị DISCONNECTED + nút Reconnect
- Network lag → buffer audio locally, hiển thị indicator

---

### Step 11: Draggable + Remember Position (Phase 2)
**Modify:** `src/overlay.py`, `src/config.py`

- Lưu vị trí window khi drag vào config file
- Khi khởi động lại, restore vị trí cuối cùng
- Double-click drag bar → snap về vị trí mặc định

---

## Thứ tự dependency

```
Step 1 (Scaffolding)
  ├── Step 2 (tmux) ─────────────┐
  ├── Step 3 (Audio) ──┐         │
  └── Step 5 (UI) ─────┤         │
                        │         │
              Step 4 (Soniox) ────┤
                                  │
                        Step 6 (App Orchestrator)
                                  │
                        Step 7 (Polish)
                                  │
                  ┌───────────────┼───────────────┐
                  │               │               │
            Step 8 (Hotkey)  Step 9 (Auto-send) Step 11 (Position)
                                  │
                            Step 10 (Reconnect)
```

Steps 2, 3, 5 có thể làm **song song**. Step 4 phụ thuộc Step 3. Step 6 tích hợp tất cả.
Steps 8, 9, 11 có thể làm **song song** sau Step 7. Step 10 phụ thuộc Step 9.

---

## Verification (end-to-end)
1. `brew install tmux` (nếu chưa có)
2. `tmux new -s voice-claude` → `claude`
3. Tạo `config.yaml` với Soniox API key
4. `pip install -r requirements.txt`
5. `python main.py`
6. Overlay xuất hiện → bấm MIC → nói → transcript real-time → text gửi vào Claude Code
