"""
Sol Voice Assistant - Natural GPT-4o-Powered Clinic Receptionist
Version: v2.0
"""

import os
import traceback
import logging
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

logging.basicConfig(level=logging.INFO)

client = OpenAI(api_key=OPENAI_API_KEY)

# ------------------------------------------------------------------
# Flask App Setup
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "✅ Sol is live and running."

@app.route("/voice", methods=["POST"])
def voice():
    speech_result = request.values.get("SpeechResult", "").strip()
    call_sid = request.values.get("CallSid", "UnknownCallSid")
    logging.info(f"Incoming call {call_sid}: '{speech_result}'")

    system_prompt = """
    You are Sol, the warm, intelligent virtual receptionist for Northern Skin Doctors.
    You sound human — friendly, calm, and professional (like Brian from ElevenLabs).
    Speak naturally. You help with skin check bookings, cosmetic enquiries, laser treatments, and results. 
    Ask clarifying questions if unsure, and always sound caring.
    Never fall back unless the user says something completely irrelevant.

    If a caller wants to book, ask what type: skin check, cosmetic, or laser. Then ask for name, phone, and preferred day.
    If they want results, say: "I'll alert the team, we don’t give results over the phone."
    If they want to cancel, ask their name and date of appointment.
    If it's an emergency, say: "Please hang up and call 000 immediately."
    If they want to speak to someone, say: "I'll pass this to a team member."
    Finish each request by saying: "Thanks, I’ll pass this along to the team to confirm by SMS."
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": speech_result}
    ]

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.3
        )
        reply = response.choices[0].message.content.strip()
        logging.info(f"Sol reply: {reply}")
    except Exception as e:
        logging.error("Error calling OpenAI:", exc_info=True)
        reply = "Sorry, something went wrong. I'll notify the team."
        send_email(
            subject=f"[Sol Error] Call {call_sid}",
            body=f"Speech: {speech_result}\n\nError:\n{traceback.format_exc()}"
        )

    # Twilio VoiceResponse
    vr = VoiceResponse()
    vr.say(reply, voice="Polly.Brian", language="en-AU")
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
# Email Notification
# ------------------------------------------------------------------
def send_email(subject, body):
    if not (SMTP_USERNAME and SMTP_PASSWORD and SMTP_SERVER):
        logging.error("Missing SMTP credentials. Email not sent.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "Sol Voice Assistant <no-reply@northernskindoctors.com.au>"
    msg["To"] = ADMIN_EMAIL
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
        logging.info(f"Email sent to {ADMIN_EMAIL}")
    except Exception as e:
        logging.error("Failed to send email:", exc_info=True)

# ------------------------------------------------------------------
# Run the App (Local only — Render uses Gunicorn)
# ------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
