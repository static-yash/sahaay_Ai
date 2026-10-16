# 🌿 Solarpunk AI — Voice Chat Bot

Real-time AI voice & text chatbot powered by **Google Gemini 2.5 Flash** streaming,
built with FastAPI WebSockets and a bioluminescent Three.js frontend.

## Quick Start

### 1. Set your API key
```bash
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

### 2. Run the backend
```bash
uv run uvicorn main:app --reload --port 8000
```

### 3. Open the UI
Visit → http://localhost:8000

---

## 🚀 Deployment

The project is fully pre-configured for production deployment:
- **Docker**: Includes a hardened, lightweight non-root [`Dockerfile`](Dockerfile) and [`docker-compose.yml`](docker-compose.yml).
- **Cloud Platforms**: Ready-to-go configs for **Render** ([`render.yaml`](render.yaml)), **Railway** ([`railway.json`](railway.json)), **Fly.io** ([`fly.toml`](fly.toml)), and **Google Cloud Run**.
- **PaaS**: Standard [`Procfile`](Procfile) and universal [`requirements.txt`](requirements.txt).

👉 See [DEPLOYMENT.md](DEPLOYMENT.md) for full step-by-step instructions for each platform.

---

## WebSocket Message Protocol

### Client → Server
| `type`          | Payload fields          | Description                        |
|-----------------|-------------------------|------------------------------------|
| `text_message`  | `text: str`             | User typed message → triggers Gemini |
| `stt_final`     | `text: str`             | Final STT transcript → triggers Gemini |
| `stt_interim`   | `text: str`             | Partial STT (echoed as ghost text) |
| `speech_start`  | —                       | VAD onset → FSM → LISTENING        |
| `interrupt`     | —                       | Barge-in: cancel active generation |
| `set_state`     | `state: str`            | Dev FSM override                   |
| `clear_history` | —                       | Wipe rolling chat history          |
| `ping`          | —                       | Liveness check                     |

### Server → Client
| `type`          | Payload fields                          | Description                  |
|-----------------|-----------------------------------------|------------------------------|
| `connected`     | `session_id`, `model`, `message`        | Handshake on connect         |
| `fsm_state`     | `state: IDLE\|LISTENING\|THINKING\|SPEAKING` | FSM transition update   |
| `transcript`    | `role`, `text`, `isFinal`               | Streaming text chunks        |
| `tts_sentence`  | `text: str`                             | Sentence boundary for TTS    |
| `telemetry`     | `latency_ms`, `model`                   | Generation latency stats     |
| `interrupt_ack` | —                                       | Barge-in confirmed           |
| `pong`          | `ts: float`                             | Ping reply                   |
| `error`         | `message: str`                          | Error notification           |

## Global JS Hooks (frontend)
```js
window.setBotState('THINKING')                     // Local FSM + Three.js
window.pushTranscript('ai', 'Hello', isFinal)      // Append to ledger
```
