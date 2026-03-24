import os
from aiohttp import web
import aiohttp

# ── ENV ─────────────────────────────────────────────
STORE_NAME = os.environ.get("STORE_NAME", "Teleone")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://aaibot.onrender.com").rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ── AI RESPONSE ─────────────────────────────────────
async def get_reply(text):
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
                    "messages": [
                        {
                            "role": "system",
                            "content": "Tum Priya ho, ek friendly customer care executive. Hinglish me short jawab do (max 2 lines)."
                        },
                        {
                            "role": "user",
                            "content": text
                        }
                    ],
                    "max_tokens": 60
                }
            ) as resp:
                data = await resp.json()
                return data["choices"][0]["message"]["content"]

    except Exception as e:
        print("AI Error:", e)
        return "Ek second, main check karke batati hoon."

# ── START CALL ──────────────────────────────────────
async def voice_start(request):
    try:
        data = await request.post()
    except:
        data = {}

    caller = data.get("From", "unknown")
    print(f"📞 Call from {caller}")
    print("PUBLIC_URL:", PUBLIC_URL)

    greeting = f"Namaste! Main Priya bol rahi hoon {STORE_NAME} se. Kaise madad kar sakti hoon?"

    return web.Response(
        text=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="hi-IN">{greeting}</Say>
  <Gather input="speech" action="{PUBLIC_URL}/voice/respond" method="POST"
          language="hi-IN" speechTimeout="auto" timeout="6"/>
</Response>""",
        content_type="application/xml"
    )

# ── HANDLE RESPONSE ─────────────────────────────────
async def voice_respond(request):
    try:
        data = await request.post()
    except:
        data = {}

    speech_text = data.get("SpeechResult", "").strip()
    print(f"User: {speech_text}")

    if not speech_text:
        reply = "Mujhe samajh nahi aaya, please dobara bolein."
    else:
        reply = await get_reply(speech_text)

    return web.Response(
        text=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="hi-IN">{reply}</Say>
  <Gather input="speech" action="{PUBLIC_URL}/voice/respond" method="POST"
          language="hi-IN" speechTimeout="auto" timeout="6"/>
</Response>""",
        content_type="application/xml"
    )

# ── HEALTH CHECK ────────────────────────────────────
async def health(request):
    return web.json_response({"status": "ok"})

# ── APP ─────────────────────────────────────────────
def create_app():
    app = web.Application()
    app.router.add_post("/voice/start", voice_start)
    app.router.add_post("/voice/respond", voice_respond)
    app.router.add_get("/", health)
    return app

# ── RUN ─────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Server running...")
    web.run_app(create_app(), host="0.0.0.0", port=PORT)