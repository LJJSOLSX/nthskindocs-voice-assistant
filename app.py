"""
Sol Voice Assistant - Natural GPT-4o-Powered Clinic Receptionist
Version: v2.1 (with startup and client initialization fixes)
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
        # This case is logged above, but client remains None.
        logging.error("OpenAI client not initialized at startup because OPENAI_API_KEY was not found in environment.")
except Exception as e:
    logging.error("CRITICAL STARTUP ERROR: Failed to initialize OpenAI client:", exc_info=True)
    # client remains None, the error and traceback are logged.

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
    logging.info(f"Incoming call {call_sid} to /voice. SpeechResult: '{speech_result}'")

    # CRITICAL: Check if OpenAI client was initialized successfully at startup
    if not client:
        logging.error(f"OpenAI client is not available for CallSid {call_sid} (was not initialized at startup). Cannot process OpenAI request.")
        reply_text = "I am currently experiencing a system configuration issue and cannot assist fully right now. The team has been notified."
        
        # Attempt to send an email about this critical failure
        send_email(
            subject=f"[Sol Critical System Error] OpenAI Client Not Initialized - Call {call_sid}",
            body=(
                f"CallSid: {call_sid}\n"
                f"Caller said: {speech_result}\n\n"
                "Attempted to process a voice request, but the OpenAI client is not available. "
                "This usually means it failed to initialize when the application started, "
                "likely due to an issue with the OPENAI_API_KEY or a network problem preventing connection to OpenAI at startup."
            )
        )
        
        vr_error = VoiceResponse()
        vr_error.say(reply_text, voice="Polly.Brian", language="en-AU")
        # Consider vr_error.hangup() here if no further interaction is possible
        return Response(str(vr_error), mimetype="text/xml")

    # If client is initialized, proceed with normal logic
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
        {"role": "user", "content": speech_result if speech_result else "Hello"} # Send "Hello" if speech_result is empty for initial greeting
    ]

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.3
        )
        reply_text = response.choices[0].message.content.strip()
        logging.info(f"Sol reply for {call_sid}: {reply_text}")
    except Exception as e:
        logging.error(f"Error calling OpenAI for CallSid {call_sid}:", exc_info=True)
        reply_text = "Sorry, something went wrong while I was trying to understand that. I'll notify the team."
        send_email(
            subject=f"[Sol OpenAI API Error] Call {call_sid}",
            body=(
                f"CallSid: {call_sid}\n"
                f"Caller said: {speech_result}\n\n"
                f"An error occurred while calling the OpenAI API:\n{traceback.format_exc()}"
            )
        )

    # Twilio VoiceResponse
    vr = VoiceResponse()
    vr.say(reply_text, voice="Polly.Brian", language="en-AU") # Ensure Polly.Brian is configured in Twilio
    
    # Only gather if it's not an emergency message
    if not ("hang up and call 000" in reply_text):
        gather = Gather(
            input="speech",
            action="/voice",
            method="POST",
            timeout=5, # Seconds to wait for speech
            speechTimeout="auto" # Let Twilio determine end of speech based on silence
        )
        vr.append(gather)
    else:
        logging.info(f"Emergency message detected for {call_sid}. Not gathering further input.")
        # vr.hangup() # Optionally explicitly hang up after an emergency message

    return Response(str(vr), mimetype="text/xml")

# ------------------------------------------------------------------
# Email Notification
# ------------------------------------------------------------------
def send_email(subject, body):
    if not (SMTP_USERNAME and SMTP_PASSWORD and SMTP_SERVER and ADMIN_EMAIL): # Added ADMIN_EMAIL check
        logging.error("Missing SMTP credentials or ADMIN_EMAIL. Email not sent. Subject: " + subject)
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "Sol Voice Assistant <no-reply@northernskindoctors.com.au>" # Consider making From email configurable
    msg["To"] = ADMIN_EMAIL
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
        logging.info(f"Email sent to {ADMIN_EMAIL} with subject: {subject}")
    except Exception as e:
        logging.error(f"Failed to send email for subject '{subject}':", exc_info=True)

# ------------------------------------------------------------------
# Run the App (Primarily for local development)
# ------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # For Render, ensure your Start Command uses a production WSGI server like Gunicorn (e.g., gunicorn app:app)
    # debug=True can be useful for local testing but MUST be False in production.
    use_debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=use_debug_mode)
