# Voice Input for Claude Code

> **⚠️ macOS only** — Ứng dụng này chỉ hỗ trợ macOS.

## Tổng quan dự án

Ứng dụng desktop nhỏ gọn (floating overlay) cho phép điều khiển Claude Code bằng giọng nói. Thay vì gõ bàn phím trong terminal, người dùng nói vào microphone, ứng dụng chuyển giọng nói thành text qua Soniox API rồi inject text đó vào Claude Code đang chạy trong tmux session.

### Mục tiêu

- Cung cấp voice input cho Claude Code — nói thay vì gõ
- Output vẫn hiển thị trong terminal Claude Code như bình thường
- Ứng dụng dạng floating overlay nhỏ, luôn nổi trên cùng, không che terminal
- Hỗ trợ tiếng Việt và mixed language (Việt-Anh)

### Không nằm trong scope

- Không làm TTS (text-to-speech) — không đọc output của Claude Code
- Không nhúng terminal vào app — Claude Code chạy trong terminal riêng
- Không dùng teammate mailbox / agent teams
- Không hỗ trợ Cowork (chưa có API để inject input)

---

## Kiến trúc

```
┌─────────────────────────────────────────────────────┐
│                    Terminal (tmux)                  │
│                                                     │
│  $ tmux new -s voice-claude                         │
│  $ claude                                           │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │ Claude Code interactive session               │  │
│  │ > output hiển thị bình thường ở đây           │  │
│  │                                               │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│         ┌──────────────────────────┐                │
│         │  🎤 "refactor main.py"   │  ← Floating    │
│         │  ● Đang nghe...   [✕]    │     Overlay    │
│         └──────────────────────────┘                │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Luồng dữ liệu

```
Microphone
    │
    ▼
Soniox WebSocket API (wss://stt-rt.soniox.com/transcribe-websocket)
    │
    ├── non-final tokens → hiển thị transcript real-time trên overlay
    │
    └── final tokens (khi nói xong) → text hoàn chỉnh
                                          │
                                          ▼
                                   tmux send-keys
                                          │
                                          ▼
                              Claude Code nhận text
                              (y hệt như gõ bàn phím)
```

---

## Các thành phần chính

### 1. Floating Overlay UI (Tkinter / CustomTkinter)

Cửa sổ nhỏ, luôn nổi trên cùng màn hình.

**Đặc điểm:**
- Always on top (`-topmost`)
- Borderless — không viền, không title bar (`overrideredirect`)
- Semi-transparent (`-alpha 0.85`)
- Draggable — kéo thả bằng chuột
- Kích thước nhỏ gọn: khoảng 350x100 pixels

**Các phần tử UI:**
- Nút microphone toggle (bấm để bắt đầu/dừng nghe)
- Vùng hiển thị transcript real-time
- Nút close (✕)
- Indicator trạng thái (đang nghe / idle / connected / error)
- (Tuỳ chọn) Nút Send thủ công hoặc chế độ auto-send

**Trạng thái của overlay:**

| Trạng thái | Hiển thị | Hành vi |
|------------|----------|---------|
| Idle | 🎤 "Bấm để nói" | Chờ user bấm mic |
| Listening | 🔴 "Đang nghe..." + transcript | Stream audio → Soniox |
| Sending | ⏳ "Đang gửi..." | tmux send-keys |
| Error | ⚠️ Thông báo lỗi | Hiển thị lỗi, cho phép retry |
| Disconnected | ❌ "Chưa kết nối tmux" | Yêu cầu kết nối |

### 2. Soniox STT Integration

Kết nối real-time tới Soniox Speech-to-Text API qua WebSocket.

**Config cần thiết:**
- API Key: lấy từ https://console.soniox.com
- Model: `stt-rt-preview` (hoặc `stt-rt-v4`)
- WebSocket URL: `wss://stt-rt.soniox.com/transcribe-websocket`

**Cấu hình Soniox:**

```python
config = {
    "api_key": SONIOX_API_KEY,
    "model": "stt-rt-preview",
    "audio_format": "s16le",          # PCM 16-bit signed little-endian
    "sample_rate": 16000,             # 16kHz
    "num_channels": 1,                # Mono
    "language_hints": ["vi", "en"],   # Tiếng Việt + tiếng Anh
    "enable_endpoint_detection": True, # Tự nhận biết khi nào nói xong
    "enable_language_identification": True,
    "context": {
        "general": [
            {"key": "domain", "value": "Software Development"},
            {"key": "topic", "value": "Programming and coding commands"}
        ],
        "terms": [
            "refactor", "debug", "commit", "push", "merge",
            "function", "component", "API", "deploy"
        ]
    }
}
```

**Xử lý token từ Soniox:**

```
Response từ Soniox:
{
    "tokens": [
        {"text": "refactor", "is_final": true, "confidence": 0.97},
        {"text": " ", "is_final": true},
        {"text": "main", "is_final": true},
        {"text": "dot", "is_final": false}   ← non-final, có thể thay đổi
    ]
}

- is_final: true  → text đã xác nhận, append vào final_tokens
- is_final: false → text tạm thời, hiển thị nhưng có thể thay đổi
- finished: true  → session kết thúc
```

**Flow audio:**

```
1. Mở microphone (sounddevice / pyaudio)
2. Kết nối WebSocket tới Soniox
3. Gửi config message (text frame)
4. Stream audio chunks (binary frames) liên tục
5. Nhận token responses → cập nhật transcript
6. Khi endpoint detected hoặc user bấm dừng:
   - Gửi empty frame để finalize
   - Chờ finished: true
   - Thu thập tất cả final tokens → text hoàn chỉnh
```

### 3. Claude Code Connection (tmux)

Giao tiếp với Claude Code thông qua tmux session.

**Tại sao dùng tmux:**
- Claude Code là interactive terminal app (dùng Ink/React renderer)
- Không thể pipe stdin đơn giản (Ink xử lý keyboard events đặc biệt)
- tmux send-keys inject keystroke ở tầng terminal emulator
- Claude Code nhận y hệt như người dùng gõ bàn phím
- Chính Anthropic dùng tmux cho agent teams spawn backend

**Kết nối tới Claude Code:**

```python
import subprocess

SESSION_NAME = "voice-claude"

def is_session_exists(session_name):
    """Kiểm tra tmux session có tồn tại không."""
    result = subprocess.run(
        ['tmux', 'has-session', '-t', session_name],
        capture_output=True
    )
    return result.returncode == 0

def create_session_with_claude(session_name):
    """Tạo tmux session mới và chạy claude."""
    subprocess.run(['tmux', 'new-session', '-d', '-s', session_name])
    subprocess.run(['tmux', 'send-keys', '-t', session_name, 'claude', 'Enter'])

def list_sessions():
    """Liệt kê tất cả tmux sessions."""
    result = subprocess.run(
        ['tmux', 'list-sessions', '-F', '#{session_name}'],
        capture_output=True, text=True
    )
    return result.stdout.strip().split('\n') if result.returncode == 0 else []

def detect_claude_session():
    """Tìm tmux session đang chạy claude."""
    result = subprocess.run(
        ['tmux', 'list-panes', '-a', '-F',
         '#{session_name} #{pane_current_command}'],
        capture_output=True, text=True
    )
    for line in result.stdout.strip().split('\n'):
        parts = line.split(' ', 1)
        if len(parts) == 2 and 'claude' in parts[1].lower():
            return parts[0]
    return None
```

**Gửi text vào Claude Code:**

```python
def send_to_claude(text, session_name):
    """Gửi text vào Claude Code qua tmux send-keys.
    
    Dùng tmux load-buffer + paste-buffer cho text phức tạp
    để tránh vấn đề escape ký tự đặc biệt.
    """
    # Cách an toàn: dùng buffer (xử lý tốt ký tự đặc biệt)
    process = subprocess.run(
        ['tmux', 'load-buffer', '-'],
        input=text.encode('utf-8')
    )
    subprocess.run(['tmux', 'paste-buffer', '-t', session_name])
    subprocess.run(['tmux', 'send-keys', '-t', session_name, 'Enter'])
```

**Logic kết nối khi app khởi động:**

```
App khởi động
    │
    ▼
Tìm tmux session đang chạy claude?
    │
    ├── TÌM THẤY → hiển thị tên session, hỏi dùng session này?
    │                  ├── Có → connect
    │                  └── Không → hỏi tạo mới?
    │
    └── KHÔNG TÌM THẤY → hỏi user:
                           ├── "Tạo session mới" → create_session_with_claude()
                           │   User tự mở terminal khác: tmux attach -t voice-claude
                           └── "Nhập tên session" → connect vào session có sẵn
```

---

## Tech Stack

### Ngôn ngữ: Python 3.10+

### Thư viện cần thiết

| Thư viện | Mục đích | Cài đặt |
|----------|----------|---------|
| `customtkinter` | UI overlay đẹp, modern | `pip install customtkinter` |
| `sounddevice` | Capture audio từ microphone | `pip install sounddevice` |
| `websockets` | Kết nối Soniox WebSocket API | `pip install websockets` |
| `numpy` | Xử lý audio buffer | `pip install numpy` |
| `keyboard` (tuỳ chọn) | Global hotkey | `pip install keyboard` |
| `pynput` (thay thế) | Global hotkey (không cần root trên macOS) | `pip install pynput` |

### Yêu cầu hệ thống

- **OS:** macOS (Apple Silicon hoặc Intel)
- **tmux:** `brew install tmux`
- **Claude Code:** Đã cài và hoạt động
- **Microphone:** Có mic hoạt động (built-in hoặc external)
- **Soniox API Key:** Đăng ký tại https://console.soniox.com
- **Python 3.10+:** `brew install python`

---

## Cấu trúc thư mục dự án

```
voice-claude/
├── README.md                  # Hướng dẫn cài đặt và sử dụng
├── requirements.txt           # Python dependencies
├── config.example.yaml        # File config mẫu
├── config.yaml                # Config thực tế (gitignore)
├── main.py                    # Entry point
├── src/
│   ├── __init__.py
│   ├── app.py                 # Main application class
│   ├── overlay.py             # Floating overlay UI (CustomTkinter)
│   ├── soniox_client.py       # Soniox WebSocket STT client
│   ├── tmux_bridge.py         # tmux connection & send-keys
│   ├── audio_capture.py       # Microphone capture (sounddevice)
│   └── config.py              # Config loader
├── assets/
│   ├── mic_on.png             # Icon microphone bật
│   ├── mic_off.png            # Icon microphone tắt
│   └── icon.png               # App icon
└── tests/
    ├── test_tmux_bridge.py
    ├── test_soniox_client.py
    └── test_audio_capture.py
```

---

## Config file

```yaml
# config.yaml

soniox:
  api_key: "YOUR_SONIOX_API_KEY"
  model: "stt-rt-preview"
  language_hints:
    - "vi"
    - "en"
  endpoint_detection: true

tmux:
  session_name: "voice-claude"    # Tên mặc định cho tmux session
  auto_create: true               # Tự tạo session nếu chưa có
  auto_run_claude: true           # Tự chạy claude trong session mới

overlay:
  width: 350
  height: 100
  opacity: 0.85
  position: "bottom-right"        # top-left, top-right, bottom-left, bottom-right
  theme: "dark"                   # dark hoặc light

voice:
  mode: "push_to_talk"            # push_to_talk hoặc auto_detect
  hotkey: "F5"                    # Global hotkey (cho push_to_talk)
  auto_send: true                 # Tự gửi khi nói xong (endpoint detected)
  auto_send_delay_ms: 500         # Chờ thêm sau endpoint trước khi gửi
```

---

## Luồng xử lý chi tiết

### Khởi động app

```
1. Load config.yaml
2. Khởi tạo UI overlay (CustomTkinter)
3. Kiểm tra tmux đã cài chưa → nếu chưa, báo lỗi
4. Tìm/tạo tmux session cho Claude Code
5. Khởi tạo audio capture (sounddevice)
6. Đăng ký global hotkey (nếu push_to_talk)
7. Hiển thị overlay ở trạng thái Idle
```

### Khi user bắt đầu nói (bấm mic hoặc hotkey)

```
1. UI chuyển sang trạng thái Listening
2. Mở WebSocket connection tới Soniox
3. Gửi config message
4. Bắt đầu capture audio từ mic
5. Stream audio chunks → Soniox (binary WebSocket frames)
6. Nhận token responses:
   - non-final tokens → cập nhật transcript trên UI (text tạm)
   - final tokens → append vào buffer (text xác nhận)
7. Endpoint detected (Soniox nhận biết ngừng nói):
   - Gửi empty frame để finalize
   - Chờ finished: true
   - Ghép tất cả final tokens → text hoàn chỉnh
```

### Khi có text hoàn chỉnh

```
1. Hiển thị text cuối cùng trên UI
2. Nếu auto_send: true
   - Chờ auto_send_delay_ms
   - Gửi qua tmux send-keys
3. Nếu auto_send: false
   - Hiển thị nút Send
   - User bấm Send hoặc Enter → gửi qua tmux send-keys
4. UI chuyển về trạng thái Idle
```

---

## Xử lý concurrency

App cần chạy song song nhiều tác vụ:
- UI loop (Tkinter mainloop) — main thread
- Audio capture — background thread
- Soniox WebSocket — asyncio hoặc background thread
- Hotkey listener — background thread

**Đề xuất:** Dùng `threading` cho audio capture và hotkey, `asyncio` cho WebSocket, giao tiếp với UI qua `queue.Queue` hoặc `root.after()`.

```
Main Thread (Tkinter)
    │
    ├── Thread: Audio Capture (sounddevice callback)
    │       └── Queue → audio chunks
    │
    ├── Thread: Soniox WebSocket (asyncio event loop)
    │       ├── Đọc audio chunks từ Queue
    │       ├── Gửi tới Soniox
    │       └── Nhận tokens → Queue → UI update
    │
    └── Thread: Hotkey Listener (pynput/keyboard)
            └── Trigger start/stop listening
```

---

## Edge cases và xử lý lỗi

| Tình huống | Xử lý |
|------------|--------|
| tmux chưa cài | Hiển thị hướng dẫn cài tmux |
| Claude Code chưa chạy trong tmux | Hỏi tạo session mới |
| tmux session bị mất giữa chừng | Phát hiện và thông báo, cho reconnect |
| Soniox WebSocket ngắt | Auto-reconnect, hiển thị trạng thái |
| Mic không hoạt động | Báo lỗi, hướng dẫn kiểm tra mic |
| Soniox API key hết hạn/sai | Báo lỗi cụ thể |
| Text chứa ký tự đặc biệt (`"`, `$`, `` ` ``) | Dùng tmux load-buffer thay vì send-keys trực tiếp |
| Nói quá dài (>300 phút) | Soniox giới hạn 300 phút/stream, tự reconnect |
| Network lag | Hiển thị indicator, buffer audio locally |

---

## Roadmap phát triển

### Phase 1: MVP (tuần 1-2)
- [ ] Floating overlay cơ bản (CustomTkinter)
- [ ] Kết nối Soniox WebSocket, hiển thị transcript
- [ ] tmux send-keys gửi text vào Claude Code
- [ ] Push-to-talk với nút trên UI

### Phase 2: Polish (tuần 3)
- [ ] Global hotkey (F5 hoặc tuỳ chỉnh)
- [ ] Auto-detect endpoint và auto-send
- [ ] Config file (YAML)
- [ ] Xử lý reconnect (Soniox, tmux)
- [ ] Draggable overlay, nhớ vị trí

### Phase 3: Nâng cao (tuần 4+)
- [ ] History — lưu lại các câu đã nói
- [ ] Edit trước khi send — cho phép sửa transcript
- [ ] Nhiều tmux session — chọn session để gửi
- [ ] Custom wake word — "Hey Claude" để bắt đầu nghe
- [ ] Tray icon — thu nhỏ vào system tray

---

## Tham khảo

- **Soniox API Docs:** https://soniox.com/docs/stt/get-started
- **Soniox WebSocket API:** https://soniox.com/docs/stt/api-reference/websocket-api
- **Soniox Real-time Transcription:** https://soniox.com/docs/stt/rt/real-time-transcription
- **Soniox Python SDK Examples:** https://github.com/soniox/soniox-examples
- **Claude Code Docs:** https://code.claude.com/docs
- **tmux Manual:** https://man7.org/linux/man-pages/man1/tmux.1.html
- **CustomTkinter:** https://github.com/TomSchimansky/CustomTkinter
- **sounddevice:** https://python-sounddevice.readthedocs.io