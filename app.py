"""
Sol Voice Assistant - GPT-4o + ElevenLabs Audio Playback
Version: v3.3 (Robust Initialization and ElevenLabs Integration)
"""

import os
import traceback
import logging
import smtplib
from email.message import EmailMessage
from flask import Flask, request, Response, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Play, Gather
from openai import OpenAI
import uuid
import requests
import time # For the user's requested sleep

# ------------------------------------------------------------------
# Logging Configuration (set up early)
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "vVnXvLYPFjIyE2YrjUBE") # Default "Rachel"

RENDER_APP_BASE_URL = os.getenv("RENDER_EXTERNAL_URL")
# Construct AUDIO_HOST_URL carefully. It should be the base public URL of your application.
if RENDER_APP_BASE_URL:
    AUDIO_HOST_URL = RENDER_APP_BASE_URL
    logging.info(f"Using RENDER_EXTERNAL_URL for AUDIO_HOST_URL: {AUDIO_HOST_URL}")
else:
    AUDIO_HOST_URL = os.getenv("AUDIO_HOST_URL_MANUAL", "http://localhost:5000") # Fallback for local or if not set
    logging.warning(f"RENDER_EXTERNAL_URL not found. Using manual/default AUDIO_HOST_URL: {AUDIO_HOST_URL}")


ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@northernskindoctors.com.au")
SMTP_SERVER    = os.getenv("SMTP_SERVER", "smtp.sendgrid.net")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME  = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD  = os.getenv("SMTP_PASSWORD")

# Validate essential environment variables needed for core functionality at startup
startup_warnings = []
if not OPENAI_API_KEY:
    startup_warnings.append("CRITICAL STARTUP ERROR: OPENAI_API_KEY environment variable is not set.")
if not ELEVENLABS_API_KEY:
    startup_warnings.append("STARTUP WARNING: ELEVENLABS_API_KEY environment variable is not set. ElevenLabs TTS will fail, falling back to Polly.")
if not ADMIN_EMAIL:
    startup_warnings.append("STARTUP WARNING: ADMIN_EMAIL environment variable is not set. System emails may not have a recipient.")
if not (SMTP_SERVER and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD):
    startup_warnings.append("STARTUP WARNING: One or more SMTP environment variables are missing. Email notifications will fail.")

for warning in startup_warnings:
    if "CRITICAL" in warning:
        logging.error(warning)
    else:
        logging.warning(warning)

# Initialize OpenAI client robustly
client = None  # Default to None
try:
    if OPENAI_API_KEY: # Only attempt if key seems to be present (not None or empty)
        client = OpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized successfully at startup.")
    else:
        # Error already logged by the check above, client remains None.
        logging.error("OpenAI client not initialized at startup because OPENAI_API_KEY was not found or was empty.")
except Exception as e:
    logging.error("CRITICAL STARTUP ERROR: Failed to initialize OpenAI client (e.g., invalid key format, network issue):", exc_info=True)
    # client remains None

# ------------------------------------------------------------------
# Temporary Audio File Setup
# ------------------------------------------------------------------
TEMP_AUDIO_DIR_NAME = "temp_audio_files"
BASE_DIR = os.path.abspath(os.path.dirname(__file__)) # Gets directory of this app.py file
TEMP_AUDIO_DIR_PATH = os.path.join(BASE_DIR, TEMP_AUDIO_DIR_NAME)

try:
    os.makedirs(TEMP_AUDIO_DIR_PATH, exist_ok=True)
    logging.info(f"Temporary audio directory ensured at: {TEMP_AUDIO_DIR_PATH}")
except OSError as e:
    logging.error(f"CRITICAL STARTUP ERROR: Could not create temporary audio directory '{TEMP_AUDIO_DIR_PATH}': {e}")
    # This could be fatal for ElevenLabs functionality if the directory cannot be created.

# ------------------------------------------------------------------
# Flask App Setup
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "✅ Sol Voice v3.3 is live (Robust Init + ElevenLabs)."

@app.route(f"/{TEMP_AUDIO_DIR_NAME}/<path:filename>") # Route to serve audio files
def serve_audio(filename):
    logging.info(f"Attempting to serve audio file: {filename} from directory: {TEMP_AUDIO_DIR_PATH}")
    try:
        # Basic security: Ensure filename is just a name and not trying to traverse directories.
        # os.path.basename will strip directory components.
        # However, send_from_directory is generally safe for serving from a designated directory.
        return send_from_directory(TEMP_AUDIO_DIR_PATH, os.path.basename(filename), as_attachment=False)
    except FileNotFoundError:
        logging.error(f"Audio file not found: {filename} in {TEMP_AUDIO_DIR_PATH}")
        return "File not found", 404
    except Exception as e:
        logging.error(f"Error serving audio file {filename}:", exc_info=True)
        return "Error serving file", 500

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
        # Use say_fallback (no gather) for critical system errors
        return say_fallback("I'm currently experiencing a system configuration issue and cannot assist. The team has been notified.")

    # If client is initialized, proceed
    system_prompt = """
    You are Sol, the emotionally intelligent AI receptionist for Northern Skin Doctors. 
    You sound like a real person — warm, thoughtful, and precise. Never robotic.

    If the caller wants to book, always ask:
    - What type of appointment?
    - Name and preferred date

    Always respond clearly. Do not end the conversation without follow-up unless the caller explicitly says they are done.
    End helpful responses with: 'I'll pass this to the team to confirm by SMS. Thanks!'
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": speech_result or "Hello"} # Send "Hello" if speech_result is empty
    ]

    reply_text = "I'm sorry, I encountered an unexpected issue. Our team has been alerted." # Default reply

    try:
        # 1. Get response from OpenAI
        logging.info(f"Call {call_sid}: Sending request to OpenAI.")
        openai_response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.4
        )
        reply_text = openai_response.choices[0].message.content.strip()
        logging.info(f"Call {call_sid}: Sol's text reply from OpenAI: '{reply_text}'")

        # 2. Attempt to generate audio with ElevenLabs
        if not (ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID):
            logging.warning(f"Call {call_sid}: ElevenLabs API key or Voice ID missing. Falling back to Polly voice for this turn.")
            return say_fallback_with_gather(reply_text) # Continue conversation with Polly

        filename = f"sol_audio_{uuid.uuid4().hex}.mp3"
        audio_file_path = os.path.join(TEMP_AUDIO_DIR_PATH, filename)
        public_audio_url = f"{AUDIO_HOST_URL}/{TEMP_AUDIO_DIR_NAME}/{filename}"

        logging.info(f"Call {call_sid}: Requesting audio from ElevenLabs for text: '{reply_text[:80]}...'")
        tts_response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={
                "Accept": "audio/mpeg",
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "text": reply_text,
                "model_id": "eleven_monolingual_v1", # Or your preferred model like eleven_turbo_v2
                "voice_settings": {"stability": 0.7, "similarity_boost": 0.75}
            },
            timeout=20 # Added a timeout for the external request
        )

        if tts_response.status_code == 200:
            with open(audio_file_path, "wb") as f:
                for chunk in tts_response.iter_content(chunk_size=4096): # standard chunk size
                    if chunk:
                        f.write(chunk)
            
            # Optional delay, added as per user's v3.2. May not always be necessary.
            # Consider if file access issues persist; root cause may be elsewhere.
            time.sleep(0.5) 
            
            logging.info(f"Call {call_sid}: Saved ElevenLabs MP3 to {audio_file_path}. Public URL: {public_audio_url}")
            
            # 3. Prepare TwiML response with <Play>
            vr = VoiceResponse()
            vr.play(public_audio_url)
            if not ("hang up and call 000" in reply_text.lower()):
                gather = Gather(input="speech", action="/voice", method="POST", timeout=5, speechTimeout="auto")
                vr.append(gather)
            else:
                logging.info(f"Emergency message for {call_sid}. Not gathering.")
            return Response(str(vr), mimetype="text/xml")
        else:
            logging.error(f"Call {call_sid}: Failed to generate ElevenLabs audio. Status: {tts_response.status_code}. Response: {tts_response.text[:200]}")
            notify_error(call_sid, f"Attempted to say: {reply_text}", f"ElevenLabs API Error: {tts_response.status_code} - {tts_response.text[:200]}")
            return say_fallback_with_gather(reply_text) # Fallback to Polly but continue conversation


    except requests.exceptions.RequestException as e: # Catch network errors for ElevenLabs
        logging.error(f"Network error calling ElevenLabs for CallSid {call_sid}:", exc_info=True)
        notify_error(call_sid, f"Attempted to say: {reply_text}", f"ElevenLabs Network Error: {e}")
        return say_fallback_with_gather(reply_text) # Fallback to Polly
    except Exception as e: # Catch any other unexpected exception in the main voice logic
        logging.error(f"General error in /voice handler for CallSid {call_sid}:", exc_info=True)
        notify_error(call_sid, speech_result, traceback.format_exc())
        return say_fallback_with_gather("I'm sorry, a general error occurred within our system. I've notified the team.")


# ------------------------------------------------------------------
# Helper: Fallback to Twilio's <Say> (Polly)
# ------------------------------------------------------------------
def say_fallback(text_to_say): # Used for critical errors where we don't gather further input
    logging.info(f"Using say_fallback (no gather) for text: '{text_to_say[:80]}...'")
    vr = VoiceResponse()
    vr.say(text_to_say, voice="Polly.Brian", language="en-AU") # Or your preferred Twilio voice
    return Response(str(vr), mimetype="text/xml")

def say_fallback_with_gather(text_to_say): # Used when an issue occurs (e.g. ElevenLabs) but conversation can continue
    logging.info(f"Using say_fallback_with_gather for text: '{text_to_say[:80]}...'")
    vr = VoiceResponse()
    vr.say(text_to_say, voice="Polly.Brian", language="en-AU")
    if not ("hang up and call 000" in text_to_say.lower()):
        gather = Gather(input="speech", action="/voice", method="POST", timeout=5, speechTimeout="auto")
        vr.append(gather)
    else:
        logging.info(f"Emergency message in say_fallback_with_gather. Not gathering.")
    return Response(str(vr), mimetype="text/xml")

# ------------------------------------------------------------------
# Email Error Notification Helper
# ------------------------------------------------------------------
def notify_error(call_sid, user_input_context, error_details_text):
    if not (SMTP_SERVER and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD and ADMIN_EMAIL):
        logging.error(f"SMTP settings or ADMIN_EMAIL missing. Cannot send error email for CallSid {call_sid}.")
        return
    
    msg = EmailMessage()
    msg["Subject"] = f"[Sol Voice Assistant Error] Call {call_sid}"
    msg["From"] = "Sol AI Assistant <no-reply@northernskindoctors.com.au>" # Consider making From email configurable
    msg["To"] = ADMIN_EMAIL
    msg.set_content(f"An error occurred with the Sol Voice Assistant.\n\nCallSid: {call_sid}\nUser Input/Context: '{user_input_context}'\n\nError Details:\n{error_details_text}")
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
        logging.info(f"Error notification email sent successfully for CallSid {call_sid} to {ADMIN_EMAIL}.")
    except Exception as e:
        logging.error(f"Failed to send error notification email for CallSid {call_sid}:", exc_info=True)

# ------------------------------------------------------------------
# Run the App (Primarily for local development)
# ------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    use_debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=use_debug_mode)
    use_debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    # For Render, ensure your Start Command uses a production WSGI server like Gunicorn (e.g., gunicorn app:app)
    app.run(host="0.0.0.0", port=port, debug=use_debug_mode)
