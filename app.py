"""
Northern Skin Doctors – Voice Assistant (Sol)
Flask + Twilio + OpenAI GPT-4o (v1.x client)
"""

import os
import traceback
import smtplib
from email.message import EmailMessage

from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@northernskindoctors.com.au")
SMTP_SERVER    = os.getenv("SMTP_SERVER", "smtp.sendgrid.net")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME  = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD  = os.getenv("SMTP_PASSWORD")

client = OpenAI(api_key=OPENAI_API_KEY)

# ------------------------------------------------------------------
# Helper: Send email
# ------------------------------------------------------------------
def send_email(subject: str, body: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = "no-reply@northernskindoctors.com.au"
    msg["To"]      = ADMIN_EMAIL
    msg.set_content(body)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(msg)

# ------------------------------------------------------------------
# GPT-4o system prompt
# ------------------------------------------------------------------
SYSTEM_PROMPT = """You are Sol, the warm, friendly virtual receptionist \
for Northern Skin Doctors (NthSkinDocs). \
Voice: calm, welcoming, similar to “Brian” from ElevenLabs. \
Follow the call flow below exactly. \
If caller indicates an emergency, say: \
'Please hang up and call 000 immediately.' \
For unclear or urgent queries, say: \
'Sorry, I didn’t quite catch that. I’ll send your message to the team.' \
and email the transcript to admin@northernskindoctors.com.au.

Call-flow summary:
1. Greeting: 'Hello, you’ve reached Northern Skin Doctors. This is Sol, your virtual assistant. How can I assist you today?'
2. Intents:
   • Book Appointment → Ask type (skin check / cosmetic / laser), name, phone, date → email admin or give HotDoc link.
   • Cancel Appointment → Ask name + date → email admin + SMS confirmation.
   • Laser Info → '$250 out-of-pocket. Would you like to book one?'
   • FotoFinder → '$200. Would you like to book it?'
   • Results Request → 'We don’t give results over the phone. I’ll alert the team.'
   • Emergency → 'Please hang up and call 000 immediately.'
   • Speak to Someone → 'I’ll pass this to a team member now.'
3. Fallback: see above.
"""

# ------------------------------------------------------------------
# Flask app
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "✅ Sol is alive and ready. Use /voice for calls.", 200

@app.route("/voice", methods=["POST"])
def voice_webhook():
    speech_result = request.values.get("SpeechResult", "").strip()
    call_sid      = request.values.get("CallSid", "")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    if speech_result:
        messages.append({"role": "user", "content": speech_result})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.3,
        )
        assistant_reply = response.choices[0].message.content
    except Exception as e:
        traceback.print_exc()
        assistant_reply = "Sorry, something went wrong. I’ll send your message to the team."
        send_email("Sol Error", str(e))

    if "000" in assistant_reply or "hang up" in assistant_reply.lower():
        vr = VoiceResponse()
        vr.say(assistant_reply, voice="Polly.Brian", language="en-AU")
        return Response(str(vr), mimetype="text/xml")

    if "didn’t quite catch" in assistant_reply.lower():
        send_email(
            subject=f"[NthSkinDocs] Fallback from call {call_sid}",
            body=f"Caller said: {speech_result}\nAssistant reply: {assistant_reply}"
        )

    vr = VoiceResponse()
    vr.say(assistant_reply, voice="Polly.Brian", language="en-AU")
    gather = Gather(
        input="speech",
        action="/voice",
        method="POST",
        timeout=5,
        speechTimeout="auto"
    )
    vr.append(gather)
    return Response(str(vr), mimetype="text/xml")

# ------------------------------------------------------------------
# Run server locally
# ------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
