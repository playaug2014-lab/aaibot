"""
server.py — Teleone Voice Bot (Priya) — Full RAG + Sarvam TTS Version
=======================================================================
Run: python server.py
Requires: ./store_index (built by rag_setup.py)
ENV vars: OPENAI_API_KEY, SARVAM_API_KEY, PORT, PUBLIC_URL, STORE_NAME
"""

import os, json, base64, asyncio, traceback
import aiohttp
from aiohttp import web

# ─── Optional: load .env file ──────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── ENV ───────────────────────────────────────────────────────────────────
STORE_NAME      = os.environ.get("STORE_NAME", "Teleone")
PUBLIC_URL      = os.environ.get("PUBLIC_URL", "https://aaibot.onrender.com").rstrip("/")
PORT            = int(os.environ.get("PORT", "8080"))
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
SARVAM_API_KEY  = os.environ.get("SARVAM_API_KEY", "")

# ─── Load ChromaDB / RAG ───────────────────────────────────────────────────
try:
    import chromadb
    from openai import OpenAI

    _chroma = chromadb.PersistentClient(path="./store_index")
    _collection = _chroma.get_collection("mystore")
    _embed_client = OpenAI(api_key=OPENAI_API_KEY)
    RAG_READY = True
    print("✅ RAG index loaded —", _collection.count(), "chunks")
except Exception as e:
    RAG_READY = False
    print(f"⚠️  RAG not available ({e}) — answers will be generic")


# ─── RAG: retrieve relevant product knowledge ──────────────────────────────
def rag_retrieve(query: str, n: int = 4) -> str:
    """Return top-n relevant chunks from the store index."""
    if not RAG_READY:
        return ""
    try:
        resp = _embed_client.embeddings.create(
            input=[query],
            model="text-embedding-3-small"
        )
        embedding = resp.data[0].embedding
        results = _collection.query(
            query_embeddings=[embedding],
            n_results=n
        )
        docs = results.get("documents", [[]])[0]
        return "\n---\n".join(docs)
    except Exception as e:
        print("RAG error:", e)
        return ""


# ─── Sarvam TTS: Hindi text → WAV audio bytes ─────────────────────────────
async def sarvam_tts(text: str) -> bytes | None:
    """
    Call Sarvam AI text-to-speech API.
    Returns raw WAV bytes or None on failure.
    Docs: https://docs.sarvam.ai/api-reference-docs/text-to-speech
    """
    if not SARVAM_API_KEY:
        print("⚠️  SARVAM_API_KEY not set — no audio")
        return None

    # Sarvam supports up to ~500 chars per chunk; truncate safely
    text = text[:480]

    payload = {
        "inputs": [text],
        "target_language_code": "hi-IN",
        "speaker": "anushka",        # female voice — change to "meera" etc if preferred
        "pitch": 0,
        "pace": 1.1,
        "loudness": 1.5,
        "speech_sample_rate": 8000,  # 8 kHz — works great on phone calls
        "enable_preprocessing": True,
        "model": "bulbul:v1"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={
                    "api-subscription-key": SARVAM_API_KEY,
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"Sarvam TTS error {resp.status}: {body[:200]}")
                    return None
                data = await resp.json()
                # Sarvam returns base64-encoded WAV in data.audios[0]
                b64 = data.get("audios", [None])[0]
                if not b64:
                    print("Sarvam: no audio in response")
                    return None
                return base64.b64decode(b64)
    except asyncio.TimeoutError:
        print("Sarvam TTS timeout")
        return None
    except Exception as e:
        print(f"Sarvam TTS exception: {e}")
        return None


# ─── GPT: generate Priya's Hinglish reply ─────────────────────────────────
SYSTEM_PROMPT = """
Tum Priya ho — {store} ki friendly aur expert customer care agent.
Tum sirf {store} ke products ke baare mein jawab deti ho.

Rules:
1. Hinglish mein jawab do (Hindi + thoda English) — jaise ek dost bolta hai.
2. 2-3 short sentences mein jawab do — zyada nahi.
3. Product ke fayde, price aur link batao agar relevant ho.
4. Agar customer ka sawaal {store} se related nahi hai, politely bolo: 
   "Maafi chahti hoon, main sirf {store} ke products ke baare mein help kar sakti hoon."
5. Kabhi bhi competitor products recommend mat karo.
6. Agar product info available ho (CONTEXT mein), usi se jawab do — guess mat karo.
7. Hamesha warm aur helpful raho — "ji", "zaroor", "bilkul" use karo.
""".strip().replace("{store}", STORE_NAME)


async def get_reply(user_text: str) -> str:
    """RAG-augmented GPT reply in Hinglish."""
    # 1. Retrieve relevant product context
    context = rag_retrieve(user_text)

    # 2. Build messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if context:
        messages.append({
            "role": "system",
            "content": f"CONTEXT — {STORE_NAME} product knowledge:\n{context}"
        })

    messages.append({"role": "user", "content": user_text})

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": messages,
                    "max_tokens": 120,
                    "temperature": 0.4
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("GPT error:", e)
        return "Ek second, thodi problem aa gayi. Dobara try karein."


# ─── Twilio Voice Webhooks ─────────────────────────────────────────────────

async def voice_start(request):
    """Twilio calls this when a new call comes in."""
    try:
        data = await request.post()
    except Exception:
        data = {}

    caller = data.get("From", "unknown")
    print(f"📞 Incoming call from {caller}")

    greeting = (
        f"Namaste! Main Priya bol rahi hoon {STORE_NAME} se. "
        "Aap hamare kisi bhi product ke baare mein pooch sakte hain. "
        "Bataiye, main aapki kya madad kar sakti hoon?"
    )

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="hi-IN" voice="Polly.Aditi">{greeting}</Say>
  <Gather input="speech"
          action="{PUBLIC_URL}/voice/respond"
          method="POST"
          language="hi-IN"
          speechTimeout="auto"
          timeout="8"
          enhanced="true"/>
  <Say language="hi-IN" voice="Polly.Aditi">Koi awaaz nahi aayi. Dobara call karein. Dhanyawaad!</Say>
</Response>"""

    return web.Response(text=twiml, content_type="application/xml")


async def voice_respond(request):
    """Twilio calls this after capturing user speech."""
    try:
        data = await request.post()
    except Exception:
        data = {}

    speech_text = data.get("SpeechResult", "").strip()
    confidence  = float(data.get("Confidence", "0") or "0")
    caller      = data.get("From", "unknown")

    print(f"🗣️  [{caller}] Said: '{speech_text}' (conf={confidence:.2f})")

    if not speech_text or confidence < 0.3:
        reply = "Mujhe clearly samajh nahi aaya. Kya aap dobara bol sakte hain?"
    else:
        reply = await get_reply(speech_text)

    print(f"🤖 Priya: {reply}")

    # Try Sarvam TTS first (better Hindi voice)
    audio_bytes = await sarvam_tts(reply)

    if audio_bytes:
        # Encode to base64 and serve as a Play URL isn't straightforward with Twilio
        # For phone calls, best approach is to use <Say> with Polly.Aditi + serve audio via URL
        # OR use Twilio's <Play> with a hosted audio file.
        # Since Render can serve files, we save and serve the audio temporarily.
        audio_b64 = base64.b64encode(audio_bytes).decode()
        # Store in memory cache and serve via /audio/<id> endpoint
        audio_id = str(id(audio_bytes))[-8:]
        _audio_cache[audio_id] = audio_bytes
        audio_url = f"{PUBLIC_URL}/audio/{audio_id}"
        print(f"🔊 Sarvam audio ready: {audio_url}")

        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Play>{audio_url}</Play>
  <Gather input="speech"
          action="{PUBLIC_URL}/voice/respond"
          method="POST"
          language="hi-IN"
          speechTimeout="auto"
          timeout="8"
          enhanced="true"/>
  <Say language="hi-IN" voice="Polly.Aditi">Koi sawaal ho toh dobara bolein. Dhanyawaad!</Say>
</Response>"""
    else:
        # Fallback: Twilio's built-in Polly Hindi voice
        safe_reply = reply.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="hi-IN" voice="Polly.Aditi">{safe_reply}</Say>
  <Gather input="speech"
          action="{PUBLIC_URL}/voice/respond"
          method="POST"
          language="hi-IN"
          speechTimeout="auto"
          timeout="8"
          enhanced="true"/>
  <Say language="hi-IN" voice="Polly.Aditi">Koi sawaal ho toh bolein. Main yahan hoon.</Say>
</Response>"""

    return web.Response(text=twiml, content_type="application/xml")


# ─── Audio cache: serve Sarvam WAV files to Twilio ────────────────────────
_audio_cache: dict[str, bytes] = {}


async def serve_audio(request):
    """Serve cached Sarvam TTS audio so Twilio can Play it."""
    audio_id = request.match_info.get("audio_id", "")
    audio_bytes = _audio_cache.get(audio_id)
    if not audio_bytes:
        return web.Response(status=404, text="Audio not found")
    # Remove from cache after serving (one-time use)
    _audio_cache.pop(audio_id, None)
    return web.Response(
        body=audio_bytes,
        content_type="audio/wav",
        headers={"Content-Disposition": "inline"}
    )


# ─── WebSocket endpoint (for the web chat widget in index.html) ────────────
async def ws_handler(request):
    """
    Handles the browser voice widget (index.html).
    Receives: { type: 'audio', data: <base64>, mime: '...' }
            | { type: 'text',  text: '...' }
            | { type: 'ready' }
    Sends:    { type: 'transcript', text: '...' }
            | { type: 'thinking' }
            | { type: 'response', text: '...', audio: <base64> }
            | { type: 'error', message: '...' }
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    print("🌐 WebSocket client connected")

    async def send(obj):
        await ws.send_str(json.dumps(obj))

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                mtype = data.get("type", "")

                if mtype == "ready":
                    # Send greeting
                    greeting_text = (
                        f"Namaste! Main Priya hoon, {STORE_NAME} ki customer care se. "
                        "Aap hamare products ke baare mein Hindi ya Hinglish mein pooch sakte hain! 😊"
                    )
                    await send({"type": "thinking"})
                    audio_bytes = await sarvam_tts(greeting_text)
                    audio_b64 = base64.b64encode(audio_bytes).decode() if audio_bytes else ""
                    await send({"type": "response", "text": greeting_text, "audio": audio_b64})

                elif mtype == "text":
                    user_text = data.get("text", "").strip()
                    if not user_text:
                        continue
                    await send({"type": "thinking"})
                    reply = await get_reply(user_text)
                    audio_bytes = await sarvam_tts(reply)
                    audio_b64 = base64.b64encode(audio_bytes).decode() if audio_bytes else ""
                    await send({"type": "response", "text": reply, "audio": audio_b64})

                elif mtype == "audio":
                    # Browser sent recorded audio — transcribe with Whisper
                    raw_b64 = data.get("data", "")
                    mime     = data.get("mime", "audio/webm")
                    if not raw_b64:
                        continue

                    await send({"type": "processing"})

                    audio_bytes_in = base64.b64decode(raw_b64)
                    ext = "webm" if "webm" in mime else "ogg" if "ogg" in mime else "wav"

                    # Transcribe with OpenAI Whisper
                    transcript = await whisper_transcribe(audio_bytes_in, ext)

                    if not transcript:
                        await send({"type": "no_speech"})
                        continue

                    await send({"type": "transcript", "text": transcript})
                    await send({"type": "thinking"})

                    reply = await get_reply(transcript)
                    audio_bytes_out = await sarvam_tts(reply)
                    audio_b64 = base64.b64encode(audio_bytes_out).decode() if audio_bytes_out else ""
                    await send({"type": "response", "text": reply, "audio": audio_b64})

            except Exception as e:
                traceback.print_exc()
                await send({"type": "error", "message": str(e)})

        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
            break

    print("🌐 WebSocket client disconnected")
    return ws


async def whisper_transcribe(audio_bytes: bytes, ext: str) -> str:
    """Send audio to OpenAI Whisper for Hindi/Hinglish transcription."""
    try:
        import io
        form = aiohttp.FormData()
        form.add_field("model", "whisper-1")
        form.add_field("language", "hi")
        form.add_field(
            "file",
            io.BytesIO(audio_bytes),
            filename=f"audio.{ext}",
            content_type=f"audio/{ext}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                data=form,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"Whisper error {resp.status}: {body[:200]}")
                    return ""
                result = await resp.json()
                return result.get("text", "").strip()
    except Exception as e:
        print(f"Whisper exception: {e}")
        return ""


# ─── Health check ──────────────────────────────────────────────────────────
async def health(request):
    return web.json_response({
        "status": "ok",
        "rag": RAG_READY,
        "sarvam": bool(SARVAM_API_KEY),
        "store": STORE_NAME
    })


# ─── App setup ─────────────────────────────────────────────────────────────
def create_app():
    app = web.Application(client_max_size=20 * 1024 * 1024)  # 20 MB for audio uploads
    app.router.add_get("/",                    health)
    app.router.add_post("/voice/start",        voice_start)
    app.router.add_post("/voice/respond",      voice_respond)
    app.router.add_get("/audio/{audio_id}",    serve_audio)
    app.router.add_get("/ws",                  ws_handler)
    return app


if __name__ == "__main__":
    print("=" * 52)
    print(f"  🚀 {STORE_NAME} Voice Bot — Priya")
    print(f"  Port: {PORT}  |  Public URL: {PUBLIC_URL}")
    print(f"  RAG: {'✅' if RAG_READY else '❌ (run rag_setup.py first)'}  |  Sarvam TTS: {'✅' if SARVAM_API_KEY else '❌'}")
    print("=" * 52)
    web.run_app(create_app(), host="0.0.0.0", port=PORT)