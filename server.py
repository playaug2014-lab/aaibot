import os
from aiohttp import web


# ENV
STORE_NAME = os.environ.get("STORE_NAME", "Teleone")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://aaibot.onrender.com").rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))

# SIMPLE REPLY
async def get_reply(text):
    return f"Aapne kaha: {text}"

# START CALL
async def voice_start(request):
    try:
        data = await request.post()
    except:
        data = {}

    caller = data.get("From", "unknown")
    print(f"📞 Call from {caller}")

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

# HANDLE RESPONSE
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

# HEALTH CHECK
async def health(request):
    return web.json_response({"status": "ok"})

# APP
def create_app():
    app = web.Application()
    app.router.add_post("/voice/start", voice_start)
    app.router.add_post("/voice/respond", voice_respond)
    app.router.add_get("/", health)
    return app

# RUN
if __name__ == "__main__":
    print("🚀 Server running...")
    web.run_app(create_app(), host="0.0.0.0", port=PORT)