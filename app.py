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
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587")) # Default is string, convert to int
SMTP_USERNAME  = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD  = os.getenv("SMTP_PASSWORD")

# Validate critical environment variables
if not OPENAI_API_KEY:
    logging.error("CRITICAL: OPENAI_API_KEY environment variable not set.")
    # Consider exiting or raising an error if essential for startup
if not SMTP_USERNAME or not SMTP_PASSWORD:
    logging.warning("SMTP_USERNAME or SMTP_PASSWORD environment variable not set. Email sending may fail.")

try:
    client = OpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    logging.error(f"Failed to initialize OpenAI client: {e}")
    client = None # Ensure client is None if initialization fails

# ------------------------------------------------------------------
# Helper: Send email
# ------------------------------------------------------------------
def send_email(subject: str, body: str):
    if not SMTP_USERNAME or not SMTP_PASSWORD or not SMTP_SERVER:
        logging.error("SMTP credentials or server not configured. Cannot send email.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = f"Sol Voice Assistant <no-reply@northernskindoctors.com.au>" # More descriptive From
    msg["To"]      = ADMIN_EMAIL
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
        logging.info(f"Email sent successfully to {ADMIN_EMAIL} with subject: {subject}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")
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
    call_sid      = request.values.get("CallSid", "UnknownCallSid") # Provide a default
    logging.info(f"Received POST to /voice. CallSid: {call_sid}. SpeechResult: '{speech_result}'")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    if speech_result: # Only add user message if there's actual input
        messages.append({"role": "user", "content": speech_result})
    # If speech_result is empty (e.g., initial call), GPT will use the system prompt
    # to generate the initial greeting.

    assistant_reply = "Sorry, I encountered an issue. I'll notify the team." # Default reply

    if not client:
        logging.error("OpenAI client is not initialized. Cannot process request.")
        # Fallback email might be appropriate here if you want to notify admin
        # send_email("Sol Critical Error - OpenAI Client", "OpenAI client failed to initialize.")
    else:
        try:
            logging.info(f"Sending messages to OpenAI: {messages}")
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.3, # Lower temperature for more deterministic responses
            )
            assistant_reply = response.choices[0].message.content
            logging.info(f"OpenAI response received: '{assistant_reply}'")
        except Exception as e:
            logging.error(f"OpenAI API call failed: {e}")
            logging.error(traceback.format_exc())
            assistant_reply = "Sorry, something went wrong on my end. I’ll send your message to the team."
            # Send an email about the error
            send_email(
                subject=f"[Sol Error] OpenAI API Failure - Call {call_sid}",
                body=f"An error occurred while communicating with OpenAI.\n\nCaller said: {speech_result}\nError: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            )

    vr = VoiceResponse()

    # Check for emergency phrases in assistant's reply (as per system prompt)
    if "000" in assistant_reply or "hang up and call 000" in assistant_reply.lower():
        vr.say(assistant_reply, voice="Polly.Brian", language="en-AU")
        # NOTE: Ensure "Polly.Brian" is available in your Twilio account or use a standard voice like "alice".
        # For emergency, we typically just say the message and hang up.
        # vr.hangup() # Optional: explicitly hang up
        logging.info(f"Emergency detected in reply. Saying: '{assistant_reply}' and ending interaction.")
        return Response(str(vr), mimetype="text/xml")

    # Check for fallback phrases (as per system prompt)
    if "didn’t quite catch that" in assistant_reply.lower() or \
       "sorry, something went wrong" in assistant_reply.lower(): # Catching our own error message too
        logging.info(f"Fallback detected. Sending email for CallSid: {call_sid}")
        send_email(
            subject=f"[NthSkinDocs] Fallback/Unclear Query from call {call_sid}",
            body=f"Caller said: {speech_result}\nAssistant reply: {assistant_reply}"
        )
        # For fallback, the bot says the message and then might gather again or hang up.
        # Your current logic gathers again, which is fine.

    # Standard interaction: Say the reply and gather more input
    vr.say(assistant_reply, voice="Polly.Brian", language="en-AU")
    # NOTE: Ensure "Polly.Brian" is available in your Twilio account.
    # Standard voices: "alice", "man", "woman". Language "en-AU" is good.
    gather = Gather(
        input="speech",
        action="/voice", # Send subsequent speech back to this same webhook
        method="POST",
        timeout=5,        # Seconds to wait for speech
        speechTimeout="auto" # Let Twilio determine end of speech
        # consider adding `actionOnEmptyResult="true"` if you want to handle silence
    )
    vr.append(gather)
    # To explicitly hang up after the assistant speaks and doesn't need more input:
    # vr.hangup() # Uncomment if the conversation should end here for certain intents

    logging.info(f"Responding with TwiML: {str(vr)}")
    return Response(str(vr), mimetype="text/xml")

# ------------------------------------------------------------------
# Run server locally (for development)
# ------------------------------------------------------------------
if __name__ == "__main__":
    # For production, use a WSGI server like Gunicorn or Waitress.
    # Example: gunicorn app:app
    # Ensure DEBUG is False in production.
    port = int(os.environ.get("PORT", 5000)) # Common for hosting platforms
    app.run(host="0.0.0.0", port=port, debug=False) # Set debug=False for production
```
# Example requirements.txt file (save this as requirements.txt in your project root)
# Flask==2.3.3  # Or your specific version
# openai==1.17.0 # Or your specific version
# twilio==8.9.1  # Or your specific version
# python-dotenv # Optional, for loading .env files locally
# gunicorn      # Optional, if using Gunicorn for deployment

# ------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
