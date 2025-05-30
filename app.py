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
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@northernskindoctors.com.au") # Ensure this is your desired admin email
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
client = None # Initialize client to None
try:
    if OPENAI_API_KEY:
        client = OpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized successfully during application startup.")
    else:
        # This log is already covered by the check above, but kept for explicitness if OPENAI_API_KEY becomes falsey later
        logging.error("OpenAI client NOT initialized at startup due to missing API key (OPENAI_API_KEY is not set).")
except Exception as e:
    logging.error(f"Failed to initialize OpenAI client during application startup: {e}")
    logging.error(traceback.format_exc()) # This will log the full traceback, e.g., for the 'proxies' error
    # client remains None
    
# ------------------------------------------------------------------
# Helper: Send email
# ------------------------------------------------------------------
def send_email(subject: str, body: str):
    if not SMTP_USERNAME or not SMTP_PASSWORD or not SMTP_SERVER:
        logging.error("SMTP credentials or server not configured. Cannot send email.")
        return

    # Use the globally defined ADMIN_EMAIL
    recipient_email = ADMIN_EMAIL
    if not recipient_email:
        logging.error("ADMIN_EMAIL is not configured. Cannot send email.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = f"Sol Voice Assistant <no-reply@northernskindoctors.com.au>" # Or your desired 'From' email
    msg["To"]      = recipient_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls() # Secure the connection
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
        logging.info(f"Email sent successfully to {recipient_email} with subject: {subject}")
    except Exception as e:
        logging.error(f"Failed to send email to {recipient_email}: {e}")
        logging.error(traceback.format_exc())

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

    # Default reply in case of issues before OpenAI call
    assistant_reply = "Sorry, I encountered an issue processing your request. I'll notify the team."

    if not client: # Check if the global client object is None
        logging.error("OpenAI client is not initialized. Cannot process request to OpenAI.")
        # Email is sent to notify admin about this critical issue
        send_email(
            subject=f"[Sol Critical Error] OpenAI Client Not Initialized - Call {call_sid}",
            body=f"Attempted to process a request, but OpenAI client is not initialized (client is None).\nCaller said: {speech_result}"
        )
        # The default 'assistant_reply' will be used
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

    # Fallback check - also check generic error messages we might set
    if "didn’t quite catch that" in assistant_reply.lower() or \
       "sorry, something went wrong" in assistant_reply.lower() or \
       "sorry, I encountered an issue" in assistant_reply.lower():
        logging.info(f"Fallback or error condition response. Sending email for CallSid: {call_sid} with reply: {assistant_reply}")
        # Check if an email was already sent for "client not initialized" to avoid duplicate generic emails
        # This is a simple check; more sophisticated state might be needed for complex scenarios
        if not ("OpenAI client is not initialized" in assistant_reply and not client) : # Avoid re-emailing for client init if already done
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
    # For production on Render, use a Start Command like: gunicorn app:app
    # The app.run below is primarily for local development.
    # Ensure debug=False for any production or publicly accessible instance.
    app.run(host="0.0.0.0", port=port, debug=False)
