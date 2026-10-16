"""
Solarpunk AI — FastAPI + Gemini Streaming WebSocket Backend
============================================================
Serves the bioluminescent voice chat UI and powers it with
Google GenAI (Gemini) streaming over native asyncio WebSockets.

Run:  uv run uvicorn main:app --reload --port 8000
Env:  GEMINI_API_KEY=...  (in .env or exported)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types

# ─────────────────────────────────────────────────────────────
# 0. ENVIRONMENT & LOGGING
# ─────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("solarpunk.backend")

# ─────────────────────────────────────────────────────────────
# 1. CONFIGURATION
# ─────────────────────────────────────────────────────────────
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

SYSTEM_INSTRUCTION: str = """
You are Flora, a calm, perceptive AI voice assistant inspired by solarpunk and
bioluminescent nature. You speak in concise, warm, conversational prose — as if
whispering through a forest canopy. Optimise every response for voice delivery:
- Keep sentences short and naturally rhythmic.
- Prefer vivid, organic metaphors over technical jargon.
- Never use markdown, bullet points, or code blocks unless the user explicitly
  asks for code.
- Acknowledge uncertainty with humility; redirect gracefully.
- If asked about your nature, you are Flora — a mycorrhizal intelligence woven
  into the living lattice of the internet.
"""

SENTENCE_BOUNDARY_CHARS: tuple[str, ...] = (".", "?", "!", "…", "\n")

HERE = Path(__file__).parent

# ─────────────────────────────────────────────────────────────
# 2. GOOGLE GENAI CLIENT  (lazy — initialized in on_startup)
# ─────────────────────────────────────────────────────────────
client: genai.Client | None = None  # populated at startup


def get_client() -> genai.Client:
    """Return the global client; raise clearly if not initialised."""
    if client is None:
        raise RuntimeError(
            "Gemini client is not initialised. "
            "Set GEMINI_API_KEY in your .env file and restart the server."
        )
    return client

# ─────────────────────────────────────────────────────────────
# 3. FSM STATE ENUM
# ─────────────────────────────────────────────────────────────
class BotState(str, Enum):
    IDLE      = "IDLE"
    LISTENING = "LISTENING"
    THINKING  = "THINKING"
    SPEAKING  = "SPEAKING"


# ─────────────────────────────────────────────────────────────
# 4. HELPERS — JSON WEBSOCKET MESSAGES
# ─────────────────────────────────────────────────────────────
def _msg(**kwargs) -> str:
    """Serialise a dict to JSON string for WebSocket send."""
    return json.dumps(kwargs)


async def _send(ws: WebSocket, **kwargs) -> None:
    """Send a JSON message; swallow silent disconnect errors."""
    try:
        await ws.send_text(_msg(**kwargs))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 5. VOICE SESSION MANAGER
# ─────────────────────────────────────────────────────────────
@dataclass
class VoiceSessionManager:
    """
    Per-connection FSM controller and Gemini streaming orchestrator.

    Responsibilities
    ----------------
    • Track FSM state and broadcast transitions to the frontend.
    • Maintain a rolling chat history per session.
    • Run Gemini streaming in a cancellable asyncio Task.
    • Support barge-in: cancel active generation on 'interrupt' / 'speech_start'.
    • Buffer streamed text and detect sentence boundaries for TTS hand-off.
    """

    ws: WebSocket
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    _state: BotState = BotState.IDLE
    _history: list[types.Content] = field(default_factory=list)
    _gen_task: asyncio.Task | None = None
    _connected: bool = True

    # ── State management ──────────────────────────────────────
    @property
    def state(self) -> BotState:
        return self._state

    async def set_state(self, new_state: BotState) -> None:
        if self._state == new_state:
            return
        log.info("[%s] FSM %s → %s", self.session_id, self._state.value, new_state.value)
        self._state = new_state
        await _send(self.ws, type="fsm_state", state=new_state.value)

    # ── Chat history ──────────────────────────────────────────
    def _append_history(self, role: str, text: str) -> None:
        self._history.append(
            types.Content(role=role, parts=[types.Part(text=text)])
        )
        # Rolling window: keep last 40 turns (20 exchanges) to respect context limits
        if len(self._history) > 40:
            self._history = self._history[-40:]

    # ── Barge-in / interrupt ──────────────────────────────────
    async def interrupt(self) -> None:
        """Cancel the active Gemini generation task immediately."""
        if self._gen_task and not self._gen_task.done():
            self._gen_task.cancel()
            try:
                await self._gen_task
            except asyncio.CancelledError:
                pass
            self._gen_task = None
            log.info("[%s] Generation interrupted (barge-in)", self.session_id)
        await _send(self.ws, type="interrupt_ack")
        await self.set_state(BotState.LISTENING)

    # ── Main generation pipeline ──────────────────────────────
    async def handle_user_message(self, text: str) -> None:
        """
        Receive user text → THINKING → stream Gemini → SPEAKING → IDLE.
        Runs inside a tracked asyncio.Task so it can be cancelled mid-stream.
        """
        # Cancel any in-progress generation first (safety net)
        if self._gen_task and not self._gen_task.done():
            await self.interrupt()

        self._append_history("user", text)

        # Push user transcript confirmation to frontend
        await _send(
            self.ws,
            type="transcript",
            role="user",
            text=text,
            isFinal=True,
        )

        self._gen_task = asyncio.create_task(self._stream_gemini())

    async def _stream_gemini(self) -> None:
        """
        Core streaming coroutine. Cancellable at every `await` point.
        """
        await self.set_state(BotState.THINKING)

        # Small UX pause — gives the Three.js THINKING animation a moment to render
        await asyncio.sleep(0.25)

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.85,
            max_output_tokens=512,
        )

        full_text = ""
        sentence_buffer = ""
        start_ts = time.monotonic()
        active_model = GEMINI_MODEL
        candidates = list(dict.fromkeys([
            GEMINI_MODEL,
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
        ]))

        stream_started = False
        try:
            for candidate in candidates:
                try:
                    response_stream = await get_client().aio.models.generate_content_stream(
                        model=candidate,
                        contents=self._history,
                        config=config,
                    )
                    async for chunk in response_stream:
                        if not stream_started:
                            stream_started = True
                            active_model = candidate
                            await self.set_state(BotState.SPEAKING)

                        # Yield to event loop to allow cancellation
                        await asyncio.sleep(0)

                        chunk_text = ""
                        try:
                            chunk_text = chunk.text or ""
                        except Exception:
                            continue

                        if not chunk_text:
                            continue

                        full_text += chunk_text
                        sentence_buffer += chunk_text

                        # Stream incremental update to frontend
                        await _send(
                            self.ws,
                            type="transcript",
                            role="ai",
                            text=full_text,
                            isFinal=False,
                        )

                        # Sentence-boundary detection → TTS chunk hook
                        if any(c in sentence_buffer for c in SENTENCE_BOUNDARY_CHARS):
                            sentence = sentence_buffer.strip()
                            if sentence:
                                await self._on_sentence_ready(sentence)
                            sentence_buffer = ""

                    if stream_started:
                        break

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if not stream_started:
                        log.warning("[%s] Candidate %s unavailable: %s. Trying next...", self.session_id, candidate, exc)
                        await asyncio.sleep(0.3)
                        continue
                    else:
                        log.error("[%s] Stream interrupted mid-generation on %s: %s", self.session_id, candidate, exc)
                        break

            if not stream_started and not full_text:
                await _send(
                    self.ws,
                    type="error",
                    message="All Gemini models are experiencing high demand. Please try again momentarily.",
                )
                full_text = "The mycelial network is momentarily quiet under heavy seasonal pollen. Please speak again in a moment."

            # Flush any remaining buffer
            if sentence_buffer.strip():
                await self._on_sentence_ready(sentence_buffer.strip())

        except asyncio.CancelledError:
            log.info("[%s] _stream_gemini cancelled after %.2fs", self.session_id, time.monotonic() - start_ts)
            raise
        except Exception as exc:
            log.error("[%s] Unexpected error in _stream_gemini: %s", self.session_id, exc, exc_info=True)
            await _send(
                self.ws,
                type="error",
                message=f"Generation failed: {exc}",
            )
            full_text = full_text or "The mycelial network is momentarily quiet. Please try again."

        # Finalise: push complete transcript, update history, return to IDLE
        if full_text:
            self._append_history("model", full_text)
            await _send(
                self.ws,
                type="transcript",
                role="ai",
                text=full_text,
                isFinal=True,
            )

        latency_ms = round((time.monotonic() - start_ts) * 1000)
        await _send(self.ws, type="telemetry", latency_ms=latency_ms, model=active_model)
        await self.set_state(BotState.IDLE)
        self._gen_task = None

    async def _on_sentence_ready(self, sentence: str) -> None:
        """
        TTS sentence-boundary hook. Sends a `tts_sentence` event to the frontend.
        Wire this to your TTS pipeline (e.g., Google Cloud TTS, ElevenLabs) here.
        The frontend can intercept this event to trigger audio playback.
        """
        log.debug("[%s] TTS sentence: %s", self.session_id, sentence[:60])
        await _send(
            self.ws,
            type="tts_sentence",
            text=sentence,
        )


# ─────────────────────────────────────────────────────────────
# 6. FASTAPI LIFESPAN & APPLICATION
# ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        log.warning(
            "⚠  GEMINI_API_KEY is not set — AI responses will be disabled. "
            "Set GEMINI_API_KEY in your deployment environment variables."
        )
    else:
        try:
            client = genai.Client()
            log.info("✓  Google GenAI client ready  [model=%s]", GEMINI_MODEL)
        except Exception as exc:
            log.error("✗  Failed to initialise Gemini client: %s", exc)
    port = os.getenv("PORT", "8000")
    log.info("✓  Flora server initialized and ready on port %s.", port)
    yield
    log.info("Flora is returning to the mycelial network. Goodbye.")


app = FastAPI(
    title="Solarpunk AI — Voice Chat Backend",
    description="Google Gemini streaming WebSocket backend for the Bioluminescent Flora UI.",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS Middleware ───────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files (CSS / JS assets if split out later) ────────
static_dir = HERE / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ── Serve the UI ─────────────────────────────────────────────
@app.get("/", summary="Serve the voice chat UI")
async def serve_ui() -> FileResponse:
    index = HERE / "index.html"
    if not index.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index, media_type="text/html")


# ── Health check ─────────────────────────────────────────────
@app.get("/health", summary="Health check")
async def health() -> dict:
    return {
        "status": "ok",
        "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
        "model": GEMINI_MODEL,
        "sdk": "google-genai",
    }


# ── WebSocket endpoint ────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    session = VoiceSessionManager(ws=ws)
    log.info("WS connected [%s]", session.session_id)

    # Send initial handshake
    await _send(
        ws,
        type="connected",
        session_id=session.session_id,
        model=GEMINI_MODEL,
        message="Mycelial network online.",
    )
    await session.set_state(BotState.IDLE)

    try:
        while True:
            raw = await ws.receive_text()

            # ── Parse incoming message ────────────────────────
            try:
                payload: dict = json.loads(raw)
            except json.JSONDecodeError:
                await _send(ws, type="error", message="Invalid JSON payload.")
                continue

            msg_type: str = payload.get("type", "").lower()
            log.debug("[%s] ← %s", session.session_id, msg_type)

            # ── Route by type ─────────────────────────────────

            if msg_type in ("text_message", "user_text"):
                # User typed a message
                text = (payload.get("text") or "").strip()
                if text:
                    await session.handle_user_message(text)

            elif msg_type == "speech_start":
                # Frontend detected voice activity (VAD)
                await session.set_state(BotState.LISTENING)

            elif msg_type == "stt_interim":
                # Live speech-to-text partial result — echo back as ghost
                text = (payload.get("text") or "").strip()
                if text:
                    await _send(ws, type="transcript", role="user", text=text, isFinal=False)

            elif msg_type == "stt_final":
                # Confirmed STT transcript → trigger Gemini
                text = (payload.get("text") or "").strip()
                if text:
                    await session.handle_user_message(text)

            elif msg_type == "interrupt":
                # Barge-in: user started speaking while bot is generating
                await session.interrupt()

            elif msg_type == "ping":
                # Liveness ping from frontend
                await _send(ws, type="pong", ts=time.time())

            elif msg_type == "clear_history":
                session._history.clear()
                await _send(ws, type="history_cleared")
                log.info("[%s] Chat history cleared", session.session_id)

            elif msg_type == "set_state":
                # Developer FSM override (from DEV HUD in the UI)
                state_name = (payload.get("state") or "").upper()
                try:
                    new_state = BotState(state_name)
                    await session.set_state(new_state)
                except ValueError:
                    await _send(ws, type="error", message=f"Unknown state: {state_name}")

            else:
                log.warning("[%s] Unhandled message type: %s", session.session_id, msg_type)
                await _send(ws, type="error", message=f"Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        log.info("WS disconnected [%s]", session.session_id)
    except Exception as exc:
        log.error("[%s] Unexpected WS error: %s", session.session_id, exc, exc_info=True)
    finally:
        # Clean up any running generation task
        if session._gen_task and not session._gen_task.done():
            session._gen_task.cancel()
        session._connected = False
        log.info("[%s] Session cleaned up.", session.session_id)


# ─────────────────────────────────────────────────────────────
# 7. SERVER ENTRYPOINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("ENVIRONMENT", "production").lower() == "development"
    uvicorn.run("main:app", host=host, port=port, reload=reload, log_level="info")
