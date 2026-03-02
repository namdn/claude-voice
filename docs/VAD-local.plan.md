# Plan: Thêm VAD (Voice Activity Detection) cho claude-voice

## Vấn đề

Hiện tại app gửi **tất cả** audio frames lên Soniox (kể cả silence) → tốn token API không cần thiết.

## Giải pháp

Thêm VAD client-side để lọc silence **trước khi** gửi lên Soniox. Chỉ gửi frames có giọng nói.

## Pipeline mới

```
Mic (100ms frames)
    │
    ▼
VAD filter ──── silence? → bỏ (không gửi)
    │
    speech? → queue → Soniox WebSocket
```

## Thư viện: webrtcvad

- Nhẹ (~100KB), pure C, chạy trong microseconds
- Hoạt động tốt với 16kHz PCM — đúng format hiện tại
- Frame 100ms (3200 bytes) chia đều thành 5 sub-frames 20ms (640 bytes)
- Nếu sau này cần chính xác hơn → swap sang silero-vad mà không đổi code bên ngoài

## Cơ chế chống mất âm

| Cơ chế | Mục đích | Mặc định |
|--------|----------|----------|
| **Pre-roll** | Giữ N frames silence trước khi speech bắt đầu, flush khi phát hiện giọng nói → không mất âm đầu câu | 3 frames (300ms) |
| **Hangover** | Sau khi hết speech, vẫn gửi thêm N frames → không cắt đuôi câu, xử lý pause tự nhiên | 5 frames (500ms) |
| **Speech threshold** | Tỷ lệ sub-frames phải là speech mới tính frame đó là speech | 0.6 (3/5 sub-frames) |

## Files đã thay đổi

### 1. `src/vad.py` — FILE MỚI
- Class `VoiceActivityDetector`
- `process(frame_bytes)` → trả về list frames cần gửi (rỗng nếu silence)
- Pre-roll buffer (deque), hangover counter
- `reset()` để clear state giữa các recording sessions
- `_detect_speech()` chia frame thành 5 sub-frames 20ms, majority vote

### 2. `src/audio_capture.py` — ĐÃ SỬA
- Import `VoiceActivityDetector` và `VADConfig`
- Nhận `vad_config` trong constructor → tạo `VoiceActivityDetector`
- `_audio_callback()`: gọi `vad.process()` → chỉ put frames có speech vào queue
- `stop()`: gọi `vad.reset()` trước khi drain queue

### 3. `src/config.py` — ĐÃ SỬA
- Thêm dataclass `VADConfig` (enabled, aggressiveness, pre_roll_frames, hangover_frames, speech_threshold)
- Thêm `vad: VADConfig` vào `AppConfig`
- Thêm load VAD config từ YAML
- Thêm validate: aggressiveness 0-3, speech_threshold 0.0-1.0

### 4. `src/app.py` — ĐÃ SỬA (1 chỗ)
- Truyền `config.vad` vào `AudioCapture(vad_config=config.vad)`

### 5. `requirements.txt` — ĐÃ SỬA
- Thêm `webrtcvad>=2.0.10`

### 6. `config.example.yaml` — ĐÃ SỬA
- Thêm section `vad:` với các tùy chọn

## Config mẫu

```yaml
vad:
  enabled: true
  aggressiveness: 2        # 0-3, cao hơn = lọc silence mạnh hơn
  pre_roll_frames: 3       # Giữ 300ms trước khi nói
  hangover_frames: 5       # Giữ 500ms sau khi ngừng nói
  speech_threshold: 0.6    # 3/5 sub-frames phải là speech
```

## Lưu ý

- **Push-to-talk vẫn hoạt động bình thường** — VAD chỉ lọc silence trong khi mic đang bật
- **`vad.enabled: false`** → pass-through, gửi tất cả frames như cũ
- **Thread safety** — OK vì chỉ có sounddevice callback thread gọi `process()`
- **Soniox không bị ảnh hưởng** — WebSocket vẫn kết nối, chỉ nhận ít frames hơn

## Kiểm tra

1. Bật app, im lặng 10 giây → kiểm tra log không có data gửi lên Soniox
2. Nói một câu → transcript hiện bình thường, không bị cắt đầu/đuôi
3. Nói có pause giữa câu → không bị ngắt giữa chừng
4. Set `vad.enabled: false` → hoạt động như cũ
