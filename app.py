"""
Sol Voice Assistant - GPT-4o-Powered Natural Receptionist
Version: v2.6 (cleaned and improved)
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
# Logging Configuration (set up early)
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@northernskindoctors.com.au") # Corrected syntax
SMTP_SERVER    = os.getenv("SMTP_SERVER", "smtp.sendgrid.net")      # Corrected syntax
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))                 # Corrected syntax
SMTP_USERNAME  = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD  = os.getenv("SMTP_PASSWORD")

# Validate essential environment variables needed for core functionality at startup
if not OPENAI_API_KEY:
    logging.error("CRITICAL STARTUP ERROR: OPENAI_API_KEY environment variable is not set.")
if not ADMIN_EMAIL:
    logging.warning("STARTUP WARNING: ADMIN_EMAIL environment variable is not set. System emails may not have a recipient.")
if not (SMTP_SERVER and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD):
    logging.warning("STARTUP WARNING: One or more SMTP environment variables are missing. Email notifications will fail.")

# Initialize OpenAI client robustly
client = None  # Default to None
try:
    if OPENAI_API_KEY: # Only attempt if key seems to be present
        client = OpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized successfully at startup.")
    else:
        # This case is logged by the check above, client remains None.
        logging.error("OpenAI client not initialized at startup because OPENAI_API_KEY was not found in environment.")
except Exception as e:
    logging.error("CRITICAL STARTUP ERROR: Failed to initialize OpenAI client (e.g., invalid key, network issue):", exc_info=True)
    # client remains None, the error and traceback are logged.

# ------------------------------------------------------------------
# Flask App Setup
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "✅ Sol is live (v2.6 - stateless)."

@app.route("/voice", methods=["POST"])
def voice():
    speech_result = request.values.get("SpeechResult", "").strip()
    call_sid = request.values.get("CallSid", "UnknownCallSid")
    logging.info(f"Call {call_sid} — User said: '{speech_result}'")

    # CRITICAL: Check if OpenAI client was initialized successfully at startup
    if not client:
        error_message_for_log = "OpenAI client is not available (was not initialized at startup)."
        logging.error(f"{error_message_for_log} Cannot process OpenAI request for CallSid {call_sid}.")
        notify_error(call_sid, speech_result, error_message_for_log)
        # Use the 'say' helper to respond and end the call as system is misconfigured
        return say("I'm having trouble connecting to my thinking process right now. The team has been notified and will follow up with you shortly if needed.")

    # If client is initialized, proceed with normal logic
    system_prompt = """
    You are Sol, the intelligent and emotionally aware voice assistant for Northern Skin Doctors.
    You are calm, confident, and kind — never robotic. Speak like a human.
    Always acknowledge the caller naturally. Use warmth and helpfulness.

    - When someone says "I want to book a skin check", reply warmly: "Absolutely, happy to help with that — do you have a day in mind?"
    - If they say something vague like "I'm calling about my results", reply: "Of course — I’ll alert the team. We don’t give results over the phone."
    - If unsure what they mean, gently clarify.
    - Never say “I’m confused.” Never say “I’m a bot.” Never default to fallback unless you truly don’t understand.

    You can:
    - Book appointments (ask what type, name, phone, date)
    - Cancel appointments (ask name and date)
    - Handle laser enquiries ($250 out of pocket)
    - Explain FotoFinder ($200 for full-body AI skin check)
    - Deflect results requests (team will follow up)
    - Flag emergencies (say: “Please hang up and call 000 immediately.”)

    Finish helpful responses with: “I’ll pass this to the team to confirm by SMS. Thanks!”
    """
    # Note: Implementing true multi-turn memory robustly requires storing conversation history,
    # e.g., using a session or database, or carefully managing state via URL parameters.
    # This version is stateless turn-by-turn.
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": speech_result or "Hello"} # Send "Hello" if speech_result is empty for initial greeting
    ]

    reply_text = "Sorry, something went wrong. I’ll send your message to the team." # Default reply
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.4 # User's preferred temperature
        )
        reply_text = response.choices[0].message.content.strip()
        logging.info(f"Sol says for {call_sid}: '{reply_text}'")
    except Exception as e:
        logging.error(f"Error calling OpenAI for CallSid {call_sid}:", exc_info=True)
        # reply_text is already set to a default error message
        notify_error(call_sid, speech_result, traceback.format_exc())


    # Twilio VoiceResponse
    vr = VoiceResponse()
    vr.say(reply_text, voice="Polly.Brian", language="en-AU") # Ensure Polly.Brian is configured in Twilio
    
    # Only gather further input if it's not an emergency message directing to call 000
    if not ("hang up and call 000" in reply_text.lower()):
        gather = Gather(
            input="speech",
            action="/voice", # Loop back to this endpoint for the next turn
            method="POST",
            timeout=5,        # Seconds to wait for user speech
            speechTimeout="auto" # Let Twilio determine end of speech based on silence
        )
        # No extra <Say> prompts within Gather for a cleaner experience.
        # The assistant's main reply should naturally lead the user to speak.
        vr.append(gather)
    else:
        logging.info(f"Emergency message detected for {call_sid}. Not gathering further input.")
        # vr.hangup() # Optional: explicitly hang up after an emergency message

    return Response(str(vr), mimetype="text/xml")

# ------------------------------------------------------------------
# Email Error Notification Helper
# ------------------------------------------------------------------
def notify_error(call_sid, user_input, error_details):
    if not (SMTP_SERVER and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD and ADMIN_EMAIL):
        logging.error(f"SMTP settings or ADMIN_EMAIL missing. Cannot send error email for CallSid {call_sid}.")
        return
    
    msg = EmailMessage()
    msg["Subject"] = f"[Sol Voice Assistant Error] Call {call_sid}"
    msg["From"] = "Sol AI Assistant <no-reply@northernskindoctors.com.au>" # Consider making From email configurable
    msg["To"] = ADMIN_EMAIL
    msg.set_content(f"An error occurred with the Sol Voice Assistant.\n\nCallSid: {call_sid}\nUser said: '{user_input}'\n\nError Details:\n{error_details}")
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
        logging.info(f"Error notification email sent successfully for CallSid {call_sid} to {ADMIN_EMAIL}.")
    except Exception as e:
        logging.error(f"Failed to send error notification email for CallSid {call_sid}:", exc_info=True)

# ------------------------------------------------------------------
# Simple TwiML Response Helper (for messages without Gather)
# ------------------------------------------------------------------
def say(text_to_say):
    vr = VoiceResponse()
    vr.say(text_to_say, voice="Polly.Brian", language="en-AU")
    # This response does not include a Gather, so the call may end after this.
    return Response(str(vr), mimetype="text/xml")

# ------------------------------------------------------------------
# Run the App (Primarily for local development)
# ------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # For Render, ensure your Start Command uses a production WSGI server like Gunicorn (e.g., gunicorn app:app)
    # debug=True can be useful for local testing but MUST be False in production.
    use_debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true" # e.g., set FLASK_DEBUG=true in env for local
    app.run(host="0.0.0.0", port=port, debug=use_debug_mode)
