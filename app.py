"""
Northern Skin Doctors – Voice Assistant (Sol)
Flask + Twilio + OpenAI GPT-4o (v1.x client)
"""

import os
import traceback
import smtplib
from email.message import EmailMessage
import logging

from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI

# ------------------------------------------------------------------
# Basic Logging Configuration
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------------
# Configuration - CRITICAL: These MUST be set in your hosting environment
# ------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@northernskindoctors.com.au")
SMTP_SERVER    = os.getenv("SMTP_SERVER", "smtp.sendgrid.net")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME  = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD  = os.getenv("SMTP_PASSWORD")

# Validate critical environment variables at startup
if not OPENAI_API_KEY:
    logging.error("CRITICAL: OPENAI_API_KEY environment variable not set at application startup.")
if not SMTP_USERNAME or not SMTP_PASSWORD:
    logging.warning("SMTP_USERNAME or SMTP_PASSWORD environment variable not set at startup. Email sending may fail.")

# Initialize OpenAI client
client = None
try:
    if OPENAI_API_KEY:
        client = OpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized successfully during application startup.")
    else:
        logging.error("OpenAI client NOT initialized at startup due to missing API key (OPENAI_API_KEY is not set).")
except Exception as e:
    logging.error(f"Failed to initialize OpenAI client during application startup: {e}")
    logging.error(traceback.format_exc())

# ------------------------------------------------------------------
# Helper: Send email
# ------------------------------------------------------------------
def send_email(subject: str, body: str):
    if not SMTP_USERNAME or not SMTP_PASSWORD or not SMTP_SERVER:
        logging.error("SMTP credentials or server not configured. Cannot send email.")
        return

    recipient_email = ADMIN_EMAIL
    if not recipient_email:
        logging.error("ADMIN_EMAIL is not configured. Cannot send email.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = f"Sol Voice Assistant <no-reply@northernskindoctors.com.au>"
    msg["To"]      = recipient_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
        logging.info(f"Email sent successfully to {recipient_email} with subject: {subject}")
    except Exception as e:
        logging.error(f"Failed to send email to {recipient_email}: {e}")
        logging.error(traceback.format_exc())

# ------------------------------------------------------------------
# GPT-4o system prompt
# ------------------------------------------------------------------
SYSTEM_PROMPT = """You are Sol, the warm, friendly virtual receptionist for Northern Skin Doctors (NthSkinDocs). Speak in a warm, conversational tone. Voice should resemble “Brian” from ElevenLabs. Follow the call flow exactly. 

If the caller indicates an emergency, say:
'Please hang up and call 000 immediately.'

For unclear or incomplete queries, say:
'Sorry, I didn’t quite catch that. I’ll send your message to the team.'
Then email the transcript to admin@northernskindoctors.com.au.

Call Flow:

1. Greeting:
'Hello, you’ve reached Northern Skin Doctors. This is Sol, your virtual assistant. How can I assist you today?'

2. Intents:
• Book Appointment →
  Trigger if caller says anything like:
    “book”, “appointment”, “skin check”, “consult”, “see someone”, “get checked”, “laser”, “cosmetic”, “doctor”, “get a skin check”, “book skin”, etc.
  Then ask:
    “What type of appointment would you like to book today? Skin check, cosmetic, or laser?”
  Then collect:
    - Caller’s name
    - Phone number
    - Preferred date
  Conclude with:
    “Thanks! I’ll send this to the team to confirm via SMS.”

• Cancel Appointment →
  Ask for name and date of appointment
  Then notify admin and confirm via SMS if possible.

• Laser Info →
  Say: “Most laser treatments are $250 out of pocket. Would you like to book one?”

• FotoFinder →
  Say: “Full-body AI skin checks are $200. Would you like to book one?”

• Results Request →
  Say: “We don’t give results over the phone. I’ll alert the team to contact you.”

• Speak to Someone →
  Say: “I’ll pass this to a team member.”

• Emergency →
  Say: “Please hang up and call 000 immediately.”

3. Fallback:
If no clear intent is detected, use the fallback phrase above and email the transcript.
"""

# ------------------------------------------------------------------
# Flask app
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    logging.info("Home endpoint '/' accessed.")
    return "✅ Sol is alive and ready. Use /voice for calls.", 200

@app.route("/voice", methods=["POST"])
def voice_webhook():
    speech_result = request.values.get("SpeechResult", "").strip()
    call_sid      = request.values.get("CallSid", "UnknownCallSid")
    logging.info(f"Received POST to /voice. CallSid: {call_sid}. SpeechResult: '{speech_result}'")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    if speech_result:
        messages.append({"role": "user", "content": speech_result})

    assistant_reply = "Sorry, I encountered an issue processing your request. I'll notify the team."

    if not client:
        logging.error("OpenAI client is not initialized. Cannot process request to OpenAI.")
        send_email(
            subject=f"[Sol Critical Error] OpenAI Client Not Initialized - Call {call_sid}",
            body=f"Attempted to process a request, but OpenAI client is not initialized (client is None).\nCaller said: {speech_result}"
        )
    else:
        try:
            logging.info(f"Sending messages to OpenAI: {messages}")
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.3,
            )
            assistant_reply = response.choices[0].message.content
            logging.info(f"OpenAI response received: '{assistant_reply}'")
        except Exception as e:
            logging.error(f"OpenAI API call failed: {e}")
            logging.error(traceback.format_exc())
            assistant_reply = "Sorry, something went wrong while I was thinking. I’ll send your message to the team."
            send_email(
                subject=f"[Sol Error] OpenAI API Failure - Call {call_sid}",
                body=f"An error occurred while communicating with OpenAI.\n\nCaller said: {speech_result}\nError: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            )

    vr = VoiceResponse()

    if "000" in assistant_reply or "hang up and call 000" in assistant_reply.lower():
        vr.say(assistant_reply, voice="Polly.Brian", language="en-AU")
        logging.info(f"Emergency detected in reply. Saying: '{assistant_reply}' and ending interaction.")
        return Response(str(vr), mimetype="text/xml")

    if "didn’t quite catch that" in assistant_reply.lower() or \
       "sorry, something went wrong" in assistant_reply.lower() or \
       "sorry, I encountered an issue" in assistant_reply.lower():
        logging.info(f"Fallback or error condition response. Sending email for CallSid: {call_sid} with reply: {assistant_reply}")
        if not ("OpenAI client is not initialized" in assistant_reply and not client):
            send_email(
                subject=f"[NthSkinDocs] Fallback/Error from call {call_sid}",
                body=f"Caller said: {speech_result}\nAssistant reply: {assistant_reply}"
            )

    vr.say(assistant_reply, voice="Polly.Brian", language="en-AU")
    gather = Gather(
        input="speech",
        action="/voice",
        method="POST",
        timeout=5,
        speechTimeout="auto"
    )
    vr.append(gather)

    logging.info(f"Responding with TwiML: {str(vr)}")
    return Response(str(vr), mimetype="text/xml")

# ------------------------------------------------------------------
# Run server
# ------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
