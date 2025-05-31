"""
Sol Voice Assistant - GPT-4o + ElevenLabs Audio Playback
Version: v3.1 (ElevenLabs with critical fixes)
"""

import os
import traceback
import logging
import smtplib
from email.message import EmailMessage
from flask import Flask, request, Response, send_from_directory # Added send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Play, Gather
from openai import OpenAI
import uuid
import requests
import pathlib # For path manipulation, though os.path is also fine

# ------------------------------------------------------------------
# Logging Configuration (set up early)
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "vVnXvLYPFjIyE2YrjUBE") # Default if not set

# Ensure AUDIO_HOST_URL ends with /audio for consistency if building URLs manually,
# or construct full URL carefully. Here, we'll serve from /audio route.
# The base URL for your Render service will be prepended by Render itself.
# So, AUDIO_HOST_URL should be the full public base URL of your app.
# For example: "https://your-app-name.onrender.com"
# The /audio part will be handled by the Flask route.
RENDER_APP_BASE_URL = os.getenv("RENDER_EXTERNAL_URL") # Render sets this automatically for services with a public URL
if not RENDER_APP_BASE_URL:
    logging.warning("RENDER_EXTERNAL_URL not found. AUDIO_HOST_URL might be incorrect if not manually set.")
    # Fallback or require manual setting if RENDER_EXTERNAL_URL isn't available/suitable
    # For local testing, you might use http://localhost:5000 or your ngrok URL
    AUDIO_HOST_URL = os.getenv("AUDIO_HOST_URL_MANUAL", "http://localhost:5000") # For local testing
else:
    AUDIO_HOST_URL = RENDER_APP_BASE_URL


ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@northernskindoctors.com.au") # Corrected syntax
SMTP_SERVER    = os.getenv("SMTP_SERVER", "smtp.sendgrid.net")      # Corrected syntax
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))                 # Corrected syntax
SMTP_USERNAME  = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD  = os.getenv("SMTP_PASSWORD")

# Validate essential environment variables
if not OPENAI_API_KEY:
    logging.error("CRITICAL STARTUP ERROR: OPENAI_API_KEY environment variable is not set.")
if not ELEVENLABS_API_KEY:
    logging.warning("STARTUP WARNING: ELEVENLABS_API_KEY environment variable is not set. ElevenLabs TTS will fail.")
if not ADMIN_EMAIL:
    logging.warning("STARTUP WARNING: ADMIN_EMAIL environment variable is not set. System emails may not have a recipient.")
if not (SMTP_SERVER and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD):
    logging.warning("STARTUP WARNING: One or more SMTP environment variables are missing. Email notifications will fail.")

# Initialize OpenAI client robustly
client = None  # Default to None
try:
    if OPENAI_API_KEY:
        client = OpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized successfully at startup.")
    else:
        logging.error("OpenAI client not initialized at startup because OPENAI_API_KEY was not found in environment.")
except Exception as e:
    logging.error("CRITICAL STARTUP ERROR: Failed to initialize OpenAI client (e.g., invalid key, network issue):", exc_info=True)

# ------------------------------------------------------------------
# Temporary Audio File Setup
# ------------------------------------------------------------------
TEMP_AUDIO_DIR_NAME = "temp_audio_files" # Directory name
# Get the absolute path to the directory where app.py is located
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TEMP_AUDIO_DIR_PATH = os.path.join(BASE_DIR, TEMP_AUDIO_DIR_NAME)

try:
    os.makedirs(TEMP_AUDIO_DIR_PATH, exist_ok=True)
    logging.info(f"Temporary audio directory set to: {TEMP_AUDIO_DIR_PATH}")
except OSError as e:
    logging.error(f"CRITICAL STARTUP ERROR: Could not create temporary audio directory '{TEMP_AUDIO_DIR_PATH}': {e}")
    # This is likely a fatal error for ElevenLabs functionality

# ------------------------------------------------------------------
# Flask App Setup
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "✅ Sol Voice v3.1 is live (ElevenLabs with critical fixes)."

@app.route(f"/{TEMP_AUDIO_DIR_NAME}/<path:filename>") # Route to serve audio files
def serve_audio(filename):
    logging.info(f"Attempting to serve audio file: {filename} from {TEMP_AUDIO_DIR_PATH}")
    try:
        return send_from_directory(TEMP_AUDIO_DIR_PATH, filename, as_attachment=False)
    except FileNotFoundError:
        logging.error(f"Audio file not found: {filename} in {TEMP_AUDIO_DIR_PATH}")
        return "File not found", 404
    except Exception as e:
        logging.error(f"Error serving audio file {filename}: {e}", exc_info=True)
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
        return say_fallback("I'm having trouble connecting to my thinking process right now. The team has been notified.")

    # If client is initialized, proceed
    system_prompt = """
    You are Sol, the emotionally intelligent AI receptionist for Northern Skin Doctors. 
    You sound like a real person — warm, thoughtful, and precise. Never robotic.
    - Always respond clearly and helpfully.
    - If the caller wants to book, ask for type, name, and preferred date.
    - Never say you are an AI. Never say you’re confused.
    - End responses with: 'I'll pass this to the team to confirm by SMS. Thanks!'
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": speech_result or "Hello"}
    ]

    reply_text = "Sorry, an unexpected issue occurred. The team has been alerted." # Default
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

        # 2. Generate audio with ElevenLabs
        if not (ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID):
            logging.warning(f"Call {call_sid}: ElevenLabs API key or Voice ID missing. Falling back to Polly voice.")
            return say_fallback(reply_text) # Use the helper that includes Gather for normal flow

        filename = f"sol_audio_{uuid.uuid4().hex}.mp3"
        audio_file_path = os.path.join(TEMP_AUDIO_DIR_PATH, filename)
        # Construct the full public URL for Twilio to access the audio file
        # Ensure AUDIO_HOST_URL is your app's public base URL (e.g., https://your-app.onrender.com)
        public_audio_url = f"{AUDIO_HOST_URL}/{TEMP_AUDIO_DIR_NAME}/{filename}"

        logging.info(f"Call {call_sid}: Requesting audio from ElevenLabs for text: '{reply_text[:50]}...'")
        tts_response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={
                "Accept": "audio/mpeg",
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "text": reply_text,
                "model_id": "eleven_monolingual_v1", # Or your preferred model
                "voice_settings": {"stability": 0.7, "similarity_boost": 0.75}
            }
        )

        if tts_response.status_code == 200:
            with open(audio_file_path, "wb") as f:
                for chunk in tts_response.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
            logging.info(f"Call {call_sid}: Saved ElevenLabs MP3 to {audio_file_path}. Public URL: {public_audio_url}")
            
            # 3. Prepare TwiML response with <Play>
            vr = VoiceResponse()
            vr.play(public_audio_url)
            # Only gather if not an emergency message
            if not ("hang up and call 000" in reply_text.lower()):
                gather = Gather(input="speech", action="/voice", method="POST", timeout=5, speechTimeout="auto")
                vr.append(gather)
            else:
                logging.info(f"Emergency message for {call_sid}. Not gathering.")
            return Response(str(vr), mimetype="text/xml")
        else:
            logging.error(f"Call {call_sid}: Failed to generate ElevenLabs audio. Status: {tts_response.status_code}. Response: {tts_response.text}")
            notify_error(call_sid, reply_text, f"ElevenLabs API Error: {tts_response.status_code} - {tts_response.text}")
            # Fallback to Polly if ElevenLabs fails, but still try to gather for next turn
            return say_fallback_with_gather(reply_text)


    except Exception as e: # Catch any other exception in the main voice logic
        logging.error(f"General error in /voice handler for CallSid {call_sid}:", exc_info=True)
        notify_error(call_sid, speech_result, traceback.format_exc())
        # Use say_fallback which will respond with Polly and then potentially Gather for next turn.
        return say_fallback_with_gather("I'm sorry, a general error occurred. I've notified the team.")


# ------------------------------------------------------------------
# Helper: Fallback to Twilio's <Say> (Polly) if ElevenLabs fails or for critical errors
# ------------------------------------------------------------------
def say_fallback(text_to_say): # Used for critical errors where we don't gather
    logging.info(f"Using say_fallback (no gather) for text: '{text_to_say[:50]}...'")
    vr = VoiceResponse()
    vr.say(text_to_say, voice="Polly.Brian", language="en-AU") # Or your preferred Twilio voice
    # This response does not include a Gather intentionally for critical unrecoverable states for the call
    return Response(str(vr), mimetype="text/xml")

def say_fallback_with_gather(text_to_say): # Used when ElevenLabs fails but conversation can continue
    logging.info(f"Using say_fallback_with_gather for text: '{text_to_say[:50]}...'")
    vr = VoiceResponse()
    vr.say(text_to_say, voice="Polly.Brian", language="en-AU")
    # Only gather if not an emergency message
    if not ("hang up and call 000" in text_to_say.lower()):
        gather = Gather(input="speech", action="/voice", method="POST", timeout=5, speechTimeout="auto")
        vr.append(gather)
    else:
        logging.info(f"Emergency message in say_fallback_with_gather. Not gathering.")
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
    msg["From"] = "Sol AI Assistant <no-reply@northernskindoctors.com.au>"
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
# Run the App (Primarily for local development)
# ------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    use_debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    # For Render, ensure your Start Command uses a production WSGI server like Gunicorn (e.g., gunicorn app:app)
    app.run(host="0.0.0.0", port=port, debug=use_debug_mode)
