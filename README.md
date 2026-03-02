# Voice Input for Claude Code

Floating overlay cho macOS cho phép điều khiển Claude Code bằng giọng nói. Nói vào mic thay vì gõ bàn phím — app chuyển giọng nói thành text (qua Soniox API) rồi gửi thẳng vào Claude Code đang chạy trong tmux.

> **macOS only** — Chỉ hỗ trợ macOS (Apple Silicon và Intel).

## Demo

```
┌─────────────────────────────────────────────────────┐
│                    Terminal (tmux)                  │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │ Claude Code interactive session               │  │
│  │ > output hiển thị bình thường ở đây           │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│         ┌──────────────────────────┐                │
│         │  "refactor main.py"      │  Floating      │
│         │  Đang nghe...     [x]    │  Overlay       │
│         └──────────────────────────┘                │
└─────────────────────────────────────────────────────┘
```

## Yêu cầu

- **macOS** (Apple Silicon hoặc Intel)
- **Python 3.10+** — `brew install python`
- **tmux** — `brew install tmux`
- **Claude Code** — đã cài và hoạt động
- **Microphone** — built-in hoặc external
- **Soniox API Key** — đăng ký tại [console.soniox.com](https://console.soniox.com)

## Cài đặt

```bash
# Clone repo
git clone https://github.com/namdn/claude-voice.git
cd claude-voice

# Tạo virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Cài dependencies
pip install -r requirements.txt

# Tạo config
cp config.example.yaml config.yaml
```

Mở `config.yaml` và điền Soniox API key:

```yaml
soniox:
  api_key: "YOUR_SONIOX_API_KEY"
```

## Sử dụng

### 1. Chạy Claude Code trong tmux

```bash
tmux new -s voice-claude
claude
```

### 2. Chạy app (ở terminal khác)

```bash
source .venv/bin/activate
python main.py
```

App sẽ tự tìm tmux session đang chạy Claude Code. Nếu chưa có, app sẽ hỏi tạo mới.

### 3. Nói

- **Push-to-talk (mặc định):** Bấm nút mic trên overlay hoặc nhấn phím `F5`
- Nói xong, app tự nhận biết và gửi text vào Claude Code
- Hỗ trợ tiếng Việt, tiếng Anh, và mixed (Việt-Anh)

## Cách hoạt động

```
Microphone → VAD filter → Soniox STT API (WebSocket) → Transcript → tmux send-keys → Claude Code
```

1. App bật mic và capture audio frames
2. **VAD (Voice Activity Detection)** lọc silence — chỉ gửi frames có giọng nói lên Soniox, tiết kiệm API token
3. Soniox trả về transcript real-time (hiển thị trên overlay)
4. Khi nói xong (endpoint detected), text hoàn chỉnh được gửi vào Claude Code qua `tmux send-keys`
5. Claude Code nhận text y hệt như người dùng gõ bàn phím

## VAD (Voice Activity Detection)

VAD client-side dùng **webrtcvad** để lọc silence trước khi gửi audio lên Soniox.

```
Mic (100ms frames)
    │
    ▼
VAD filter ──── silence? → bỏ (không gửi)
    │
    speech? → queue → Soniox WebSocket
```

### Cơ chế chống mất âm

| Cơ chế | Mô tả | Mặc định |
|--------|-------|----------|
| **Pre-roll** | Giữ N frames silence trước khi speech bắt đầu → không mất âm đầu câu | 3 frames (300ms) |
| **Hangover** | Sau khi hết speech, vẫn gửi thêm N frames → không cắt đuôi câu | 5 frames (500ms) |
| **Speech threshold** | Tỷ lệ sub-frames phải là speech mới tính frame đó là speech | 0.6 (3/5) |

### Cấu hình VAD

```yaml
vad:
  enabled: true
  aggressiveness: 2        # 0-3, cao hơn = lọc silence mạnh hơn
  pre_roll_frames: 3       # Giữ 300ms trước khi nói
  hangover_frames: 5       # Giữ 500ms sau khi ngừng nói
  speech_threshold: 0.6    # 3/5 sub-frames phải là speech
```

Đặt `vad.enabled: false` để tắt VAD và gửi tất cả frames như cũ.

## Cấu hình

Xem [`config.example.yaml`](config.example.yaml) để biết đầy đủ các tùy chọn.

| Nhóm | Tùy chọn | Mô tả | Mặc định |
|------|----------|-------|----------|
| `soniox` | `api_key` | Soniox API key | — |
| `soniox` | `model` | STT model | `stt-rt-preview` |
| `soniox` | `language_hints` | Ngôn ngữ | `["vi", "en"]` |
| `tmux` | `session_name` | Tên tmux session | `voice-claude` |
| `tmux` | `auto_create` | Tự tạo session | `true` |
| `overlay` | `position` | Vị trí overlay | `bottom-right` |
| `overlay` | `opacity` | Độ trong suốt | `0.85` |
| `overlay` | `theme` | Giao diện | `dark` |
| `voice` | `mode` | Chế độ | `push_to_talk` |
| `voice` | `hotkey` | Phím tắt | `F5` |
| `voice` | `auto_send` | Tự gửi khi nói xong | `true` |
| `vad` | `enabled` | Bật/tắt VAD | `false` |
| `vad` | `aggressiveness` | Mức lọc silence (0-3) | `2` |

## Cấu trúc dự án

```
claude-voice/
├── main.py                 # Entry point
├── config.yaml             # Config (gitignored)
├── config.example.yaml     # Config mẫu
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

## Xử lý lỗi

| Tình huống | Xử lý |
|------------|-------|
| tmux chưa cài | Hiện hướng dẫn cài tmux |
| Claude Code chưa chạy trong tmux | Hỏi tạo session mới |
| tmux session mất kết nối | Tự phát hiện, cho reconnect |
| Soniox WebSocket ngắt | Auto-reconnect |
| Mic không hoạt động | Báo lỗi, hướng dẫn kiểm tra |

## License

MIT
