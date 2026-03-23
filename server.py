"""
Priya — Twilio Inbound Call Server (v8 — Single User India)
============================================================
Customer dials your Twilio number → Priya answers in Hinglish
Pipeline: Twilio → Gather(speech) → Sarvam STT → RAG → OpenAI → Sarvam TTS → Twilio plays audio
Deploy on Render.com
"""

import asyncio, base64, json, os, time
import aiohttp
from aiohttp import web

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Config ────────────────────────────────────────────
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
STORE_NAME     = os.environ.get("STORE_NAME", "Teleone")
PUBLIC_URL     = os.environ.get("PUBLIC_URL", "").rstrip("/")
PORT           = int(os.environ.get("PORT", "8080"))

# ── RAG ──────────────────────────────────────────────
rag_collection = None
_oai_sync      = None
try:
    import chromadb
    from openai import OpenAI as OpenAISync
    _chroma        = chromadb.PersistentClient(path="./store_index")
    rag_collection = _chroma.get_collection("mystore")
    _oai_sync      = OpenAISync(api_key=OPENAI_API_KEY)
    print(f"✅ RAG loaded — {rag_collection.count()} chunks")
except Exception as e:
    print(f"⚠️  RAG not ready: {e}")

def rag_search(query: str) -> str:
    if not rag_collection or not _oai_sync:
        return ""
    try:
        emb     = _oai_sync.embeddings.create(input=query, model="text-embedding-3-small")
        results = rag_collection.query(query_embeddings=[emb.data[0].embedding], n_results=3)
        chunks  = results.get("documents", [[]])[0]
        print(f"  [RAG] {len(chunks)} chunks for: {query[:50]}")
        return "\n\n".join(chunks)
    except Exception as e:
        print(f"  [RAG] Error: {e}")
        return ""

# ── System Prompt ─────────────────────────────────────
def build_prompt(context: str) -> str:
    return f"""Aap Priya hain — {STORE_NAME} ki customer care executive.

RULES:
1. Aap insaan hain — kabhi mat bolna AI ya bot hain.
   Agar poochha jaye: "Nahi main {STORE_NAME} ki real executive hun!"
2. SIRF neeche di store info se jawab do.
   Pata nahi toh: "Main check karke batati hun."
3. Phone call hai — max 2 CHHOTE sentences mein jawab do.
4. Hinglish mein bolo — warm aur friendly.
5. ChatGPT, OpenAI, Sarvam ka naam kabhi mat lo.

STORE INFO:
{context if context else "Store data abhi load nahi hua."}"""

# ── LLM ──────────────────────────────────────────────
async def get_llm_response(text: str, history: list) -> str:
    if not OPENAI_API_KEY:
        return "Haan bilkul, batao kya help chahiye?"
    try:
        context = await asyncio.get_event_loop().run_in_executor(None, rag_search, text)
        msgs    = [{"role": "system", "content": build_prompt(context)}]
        msgs   += history[-4:]
        msgs.append({"role": "user", "content": text})
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "messages": msgs, "max_tokens": 80, "temperature": 0.4},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  [LLM] Error: {e}")
        return "Ek second, main check karti hun."

# ── TTS — returns base64 WAV ──────────────────────────
async def synthesize(text: str) -> str:
    if not SARVAM_API_KEY:
        return ""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"},
                json={
                    "inputs": [text],
                    "target_language_code": "hi-IN",
                    "speaker": "anushka",
                    "model": "bulbul:v2",
                    "pace": 1.0,
                    "loudness": 1.5,
                    "pitch": 0.0,
                    "speech_sample_rate": 8000,
                    "enable_preprocessing": True,
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                data = await r.json()
                b64  = data.get("audios", [""])[0]
                if b64:
                    print(f"  [TTS] ✅ audio len={len(b64)}")
                return b64
    except Exception as e:
        print(f"  [TTS] Error: {e}")
        return ""

# ── In-memory audio store (Twilio fetches via URL) ────
audio_store: dict = {}
call_histories: dict = {}

# ── ROUTE: GET /audio/{token} ─────────────────────────
async def serve_audio(request: web.Request):
    token = request.match_info["token"]
    b64   = audio_store.pop(token, "")
    if not b64:
        return web.Response(status=404)
    return web.Response(body=base64.b64decode(b64), content_type="audio/wav")

# ── Helper: build TwiML XML ───────────────────────────
def twiml_play_then_gather(audio_token: str, fallback_text: str = "") -> str:
    play_url = f"{PUBLIC_URL}/audio/{audio_token}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Play>{play_url}</Play>
  <Gather input="speech" action="{PUBLIC_URL}/voice/respond" method="POST"
          language="hi-IN" speechTimeout="auto" timeout="6">
  </Gather>
  <Redirect method="POST">{PUBLIC_URL}/voice/noinput</Redirect>
</Response>"""

def twiml_say_then_gather(text: str) -> str:
    """Fallback when TTS unavailable"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="hi-IN">{text}</Say>
  <Gather input="speech" action="{PUBLIC_URL}/voice/respond" method="POST"
          language="hi-IN" speechTimeout="auto" timeout="6">
  </Gather>
  <Redirect method="POST">{PUBLIC_URL}/voice/noinput</Redirect>
</Response>"""

# ── ROUTE: POST /voice/start ──────────────────────────
# Twilio calls this when someone dials your number
async def voice_start(request: web.Request):
    data     = await request.post()
    call_sid = data.get("CallSid", "unknown")
    caller   = data.get("From", "unknown")
    print(f"\n📞 Inbound call: {call_sid} from {caller}")

    greeting = f"नमस्ते! मैं प्रिया हूँ, {STORE_NAME} से। आपकी कैसे मदद कर सकती हूँ?"
    b64      = await synthesize(greeting)

    if b64:
        token               = f"g_{call_sid}_{int(time.time())}"
        audio_store[token]  = b64
        xml = twiml_play_then_gather(token)
    else:
        xml = twiml_say_then_gather(greeting)

    return web.Response(text=xml, content_type="application/xml")

# ── ROUTE: POST /voice/respond ────────────────────────
# Twilio sends recognized speech here
async def voice_respond(request: web.Request):
    data         = await request.post()
    call_sid     = data.get("CallSid", "unknown")
    speech_text  = data.get("SpeechResult", "").strip()
    confidence   = data.get("Confidence", "0")
    print(f"  [{call_sid}] User said: '{speech_text}' (conf={confidence})")

    if not speech_text:
        return web.Response(
            text=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="hi-IN">Maaf kijiye, main sun nahi payi. Dobara bolein please.</Say>
  <Gather input="speech" action="{PUBLIC_URL}/voice/respond" method="POST"
          language="hi-IN" speechTimeout="auto" timeout="6">
  </Gather>
  <Redirect method="POST">{PUBLIC_URL}/voice/noinput</Redirect>
</Response>""",
            content_type="application/xml"
        )

    history  = call_histories.get(call_sid, [])
    bot_text = await get_llm_response(speech_text, history)
    print(f"  [{call_sid}] Priya: {bot_text}")

    history.append({"role": "user",      "content": speech_text})
    history.append({"role": "assistant", "content": bot_text})
    call_histories[call_sid] = history[-10:]

    b64 = await synthesize(bot_text)
    if b64:
        token              = f"r_{call_sid}_{int(time.time())}"
        audio_store[token] = b64
        xml = twiml_play_then_gather(token)
    else:
        xml = twiml_say_then_gather(bot_text)

    return web.Response(text=xml, content_type="application/xml")

# ── ROUTE: POST /voice/noinput ────────────────────────
async def voice_noinput(request: web.Request):
    data     = await request.post()
    call_sid = data.get("CallSid", "?")
    print(f"  [{call_sid}] No input — prompting again")
    return web.Response(
        text=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="hi-IN">Kya aap wahan hain? Koi sawaal ho toh poochein.</Say>
  <Gather input="speech" action="{PUBLIC_URL}/voice/respond" method="POST"
          language="hi-IN" speechTimeout="auto" timeout="6">
  </Gather>
  <Hangup/>
</Response>""",
        content_type="application/xml"
    )

# ── ROUTE: POST /voice/status ─────────────────────────
async def voice_status(request: web.Request):
    data     = await request.post()
    call_sid = data.get("CallSid", "?")
    status   = data.get("CallStatus", "?")
    print(f"  [{call_sid}] Status → {status}")
    if status in ("completed", "failed", "no-answer", "busy", "canceled"):
        call_histories.pop(call_sid, None)
    return web.Response(text="OK")

# ── ROUTE: GET /test-keys ─────────────────────────────
async def test_keys(request):
    r_openai = r_sarvam = "❌ Key missing"
    if OPENAI_API_KEY:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 3},
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    r_openai = "✅ OK" if r.status == 200 else f"❌ HTTP {r.status}"
        except Exception as e:
            r_openai = f"❌ {e}"

    if SARVAM_API_KEY:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    "https://api.sarvam.ai/text-to-speech",
                    headers={"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"},
                    json={"inputs": ["test"], "target_language_code": "hi-IN",
                          "speaker": "anushka", "model": "bulbul:v2", "pace": 1.0, "loudness": 1.0},
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    d = await r.json()
                    ok = bool(d.get("audios", [""])[0])
                    r_sarvam = f"✅ OK (audio={'yes' if ok else 'EMPTY!'})" if r.status == 200 else f"❌ HTTP {r.status}"
        except Exception as e:
            r_sarvam = f"❌ {e}"

    rag = f"✅ {rag_collection.count()} chunks" if rag_collection else "⚠️ Run rag_setup.py first"
    html = f"""<html><body style="font-family:monospace;background:#0d1117;color:#e6edf3;padding:40px;font-size:15px;line-height:2.2">
<h2 style="color:#ff6b00">🔑 Priya — Status Check</h2>
<p><b>OpenAI :</b> {r_openai}</p>
<p><b>Sarvam :</b> {r_sarvam}</p>
<p><b>RAG    :</b> {rag}</p>
<p><b>URL    :</b> {PUBLIC_URL or '⚠️ Set PUBLIC_URL in Render env vars'}</p>
<br>
<p style="color:#8b949e">Twilio webhook set karo:</p>
<p style="color:#ff6b00">{PUBLIC_URL}/voice/start</p>
</body></html>"""
    return web.Response(text=html, content_type="text/html")

async def health(request):
    return web.json_response({"status": "ok", "rag": rag_collection.count() if rag_collection else 0})

@web.middleware
async def cors(request, handler):
    if request.method == "OPTIONS":
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Twilio-Signature",
        })
    resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

def create_app():
    app = web.Application(middlewares=[cors])
    app.router.add_post("/voice/start",   voice_start)
    app.router.add_post("/voice/respond", voice_respond)
    app.router.add_post("/voice/noinput", voice_noinput)
    app.router.add_post("/voice/status",  voice_status)
    app.router.add_get( "/audio/{token}", serve_audio)
    app.router.add_get( "/test-keys",     test_keys)
    app.router.add_get( "/health",        health)
    app.router.add_get( "/",              health)
    return app

if __name__ == "__main__":
    print(f"\n{'═'*50}")
    print(f"  📞 Priya — Twilio Inbound Call Server v8")
    print(f"  Store  : {STORE_NAME}")
    print(f"  Port   : {PORT}")
    print(f"  Sarvam : {'✅' if SARVAM_API_KEY else '❌ MISSING'}")
    print(f"  OpenAI : {'✅' if OPENAI_API_KEY else '❌ MISSING'}")
    print(f"  RAG    : {'✅ ' + str(rag_collection.count()) + ' chunks' if rag_collection else '⚠️  Run rag_setup.py!'}")
    print(f"  URL    : {PUBLIC_URL or '⚠️  Set after Render deploy'}")
    print(f"{'═'*50}\n")
    web.run_app(create_app(), host="0.0.0.0", port=PORT)