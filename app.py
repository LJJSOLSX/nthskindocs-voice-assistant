"""
Sol Voice Assistant - GPT-4o + Whisper STT + ElevenLabs TTS
Version: v3.5.2 (Fixed f-string SyntaxError for logging)
"""

import os
import traceback
import logging
import smtplib
from email.message import EmailMessage
from flask import Flask, request, Response, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Play, Record
from openai import OpenAI
import uuid
import requests
import time
import io # For BytesIO

# ------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "vVnXvLYPFjIyE2YrjUBE") # Default "Rachel"

RENDER_APP_BASE_URL = os.getenv("RENDER_EXTERNAL_URL")
if RENDER_APP_BASE_URL:
    AUDIO_HOST_URL = RENDER_APP_BASE_URL
    logging.info(f"Using RENDER_EXTERNAL_URL for AUDIO_HOST_URL: {AUDIO_HOST_URL}")
else:
    AUDIO_HOST_URL = os.getenv("AUDIO_HOST_URL_MANUAL", "http://localhost:5000")
    logging.warning(f"RENDER_EXTERNAL_URL not found. Using manual/default AUDIO_HOST_URL: {AUDIO_HOST_URL}")

ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@northernskindoctors.com.au")
SMTP_SERVER    = os.getenv("SMTP_SERVER", "smtp.sendgrid.net")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME  = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD  = os.getenv("SMTP_PASSWORD")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

# Validate essential environment variables
startup_warnings = []
if not OPENAI_API_KEY: startup_warnings.append("CRITICAL STARTUP ERROR: OPENAI_API_KEY not set.")
if not ELEVENLABS_API_KEY: startup_warnings.append("STARTUP WARNING: ELEVENLABS_API_KEY not set. ElevenLabs TTS will fail.")
if not ADMIN_EMAIL: startup_warnings.append("STARTUP WARNING: ADMIN_EMAIL not set.")
if not (SMTP_SERVER and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD): startup_warnings.append("STARTUP WARNING: SMTP variables missing.")
if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN): startup_warnings.append("STARTUP INFO: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN missing. Authenticated download of Twilio recordings may fail.")

for warning in startup_warnings:
    if "CRITICAL" in warning: logging.error(warning)
    else: logging.warning(warning)

# Initialize OpenAI client
client = None
try:
    if OPENAI_API_KEY:
        client = OpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized successfully at startup.")
    else:
        logging.error("OpenAI client not initialized at startup: OPENAI_API_KEY missing.")
except Exception as e:
    logging.error("CRITICAL STARTUP ERROR: Failed to initialize OpenAI client:", exc_info=True)

# Temporary Audio File Setup
TEMP_AUDIO_DIR_NAME = "temp_audio_files"
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TEMP_AUDIO_DIR_PATH = os.path.join(BASE_DIR, TEMP_AUDIO_DIR_NAME)
try:
    os.makedirs(TEMP_AUDIO_DIR_PATH, exist_ok=True)
    logging.info(f"Temporary audio directory ensured at: {TEMP_AUDIO_DIR_PATH}")
except OSError as e:
    logging.error(f"CRITICAL STARTUP ERROR: Could not create audio directory '{TEMP_AUDIO_DIR_PATH}': {e}")

# ------------------------------------------------------------------
# Flask App
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "✅ Sol Voice v3.5.2 is live (SyntaxError Fix)."

@app.route(f"/{TEMP_AUDIO_DIR_NAME}/<path:filename>")
def serve_audio(filename):
    logging.info(f"Attempting to serve audio file: {filename} from {TEMP_AUDIO_DIR_PATH}")
    try:
        return send_from_directory(TEMP_AUDIO_DIR_PATH, os.path.basename(filename), as_attachment=False)
    except FileNotFoundError:
        logging.error(f"Audio file not found during serve_audio: {filename}")
        return "File not found", 404
    except Exception as e:
        logging.error(f"Error serving audio file {filename}:", exc_info=True)
        return "Error serving file", 500

# --- Text-to-Speech (ElevenLabs) Helper ---
def text_to_elevenlabs_audio(text_to_speak, call_sid_for_log=""):
    if not (ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID):
        logging.warning(f"Call {call_sid_for_log}: ElevenLabs API key or Voice ID missing. Cannot generate custom audio.")
        return None, text_to_speak

    filename = f"sol_audio_{uuid.uuid4().hex}.mp3"
    audio_file_path = os.path.join(TEMP_AUDIO_DIR_PATH, filename)
    public_audio_url = f"{AUDIO_HOST_URL}/{TEMP_AUDIO_DIR_NAME}/{filename}"

    logging.info(f"Call {call_sid_for_log}: Requesting audio from ElevenLabs for: '{text_to_speak[:80]}...'")
    try:
        tts_response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={"Accept": "audio/mpeg", "xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={"text": text_to_speak, "model_id": "eleven_monolingual_v1", "voice_settings": {"stability": 0.7, "similarity_boost": 0.75}},
            timeout=20, stream=True
        )
        tts_response.raise_for_status()
        total_bytes_written = 0
        with open(audio_file_path, "wb") as f:
            for chunk in tts_response.iter_content(chunk_size=4096):
                if chunk: f.write(chunk); total_bytes_written += len(chunk)
        logging.info(f"Call {call_sid_for_log}: Total bytes written for MP3 from ElevenLabs: {total_bytes_written}")
        if total_bytes_written == 0:
            logging.error(f"Call {call_sid_for_log}: ElevenLabs returned 200 OK but 0 bytes audio for: '{text_to_speak[:80]}...'. Forcing Polly.")
            if os.path.exists(audio_file_path):
                try: os.remove(audio_file_path); logging.info(f"Removed empty file: {audio_file_path}")
                except OSError as rm_err: logging.warning(f"Could not remove empty file '{audio_file_path}': {rm_err}")
            return None, text_to_speak
        time.sleep(0.5)
        logging.info(f"Call {call_sid_for_log}: Saved ElevenLabs MP3: {audio_file_path}. URL: {public_audio_url}")
        return public_audio_url, text_to_speak
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"Call {call_sid_for_log}: HTTP error ElevenLabs: {http_err.response.status_code} - {http_err.response.text[:200]}", exc_info=True)
        notify_error(call_sid_for_log, f"TTS for: {text_to_speak[:50]}...", f"ElevenLabs HTTP Err: {http_err.response.status_code}..")
        return None, text_to_speak
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Call {call_sid_for_log}: Network error ElevenLabs:", exc_info=True)
        notify_error(call_sid_for_log, f"TTS for: {text_to_speak[:50]}...", f"ElevenLabs Net Err: {req_err}")
        return None, text_to_speak
    except Exception as e:
        logging.error(f"Call {call_sid_for_log}: Unexpected error in text_to_elevenlabs_audio:", exc_info=True)
        notify_error(call_sid_for_log, f"TTS for: {text_to_speak[:50]}...", f"TTS Gen Err: {e}")
        return None, text_to_speak

@app.route("/voice", methods=["POST"])
def initial_voice_handler():
    call_sid = request.values.get("CallSid", "UnknownCallSid")
    logging.info(f"Initial call {call_sid} received at /voice.")
    if not client:
        error_msg = "OpenAI client not initialized. Cannot proceed."
        logging.error(f"Call {call_sid}: {error_msg}")
        notify_error(call_sid, "Initial Call", error_msg)
        return say_fallback("Our system is experiencing issues. Please try again later.")
    greeting_text = "Hello! You've reached Northern Skin Doctors. This is Sol, your virtual assistant. How can I assist you today?"
    public_audio_url, _ = text_to_elevenlabs_audio(greeting_text, call_sid)
    vr = VoiceResponse()
    if public_audio_url: vr.play(public_audio_url)
    else: logging.warning(f"Call {call_sid}: ElevenLabs failed for initial greeting, using Polly."); vr.say(greeting_text, voice="Polly.Brian", language="en-AU")
    record = Record(action="/handle_speech_input", method="POST", timeout=7, maxLength=30, playBeep=False, trim="trim-silence")
    vr.append(record)
    twiml_to_send = str(vr); logging.info(f"Call {call_sid}: Responding from /voice with TwiML: {twiml_to_send}"); return Response(twiml_to_send, mimetype="text/xml")

@app.route("/handle_speech_input", methods=["POST"])
def handle_speech_input():
    call_sid = request.values.get("CallSid", "UnknownCallSid")
    recording_url = request.values.get("RecordingUrl")
    recording_duration_str = request.values.get("RecordingDuration", "0")
    try: recording_duration = int(recording_duration_str)
    except ValueError: logging.warning(f"Call {call_sid}: Invalid RecordingDuration '{recording_duration_str}'. Defaulting to 0."); recording_duration = 0
    logging.info(f"Call {call_sid}: /handle_speech_input. RecordingURL: {recording_url}, Duration: {recording_duration}s")

    if not client: 
        error_msg = "OpenAI client not initialized. Cannot process speech."
        logging.error(f"Call {call_sid}: {error_msg}")
        notify_error(call_sid, f"RecordingURL: {recording_url}", error_msg)
        return say_fallback("Our system is having trouble. Please try again later.")

    speech_result = ""
    if recording_url:
        try:
            auth_tuple = None
            if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
                auth_tuple = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                logging.info(f"Call {call_sid}: Attempting to download Twilio recording WITH authentication.")
            else:
                logging.warning(f"Call {call_sid}: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not set. Attempting UNauthenticated download of Twilio recording.")
            audio_get_response = requests.get(recording_url, auth=auth_tuple, timeout=10)
            audio_get_response.raise_for_status()
            audio_content = io.BytesIO(audio_get_response.content); audio_content.name = f"rec_{call_sid}.wav"
            logging.info(f"Call {call_sid}: Transcribing audio ({len(audio_get_response.content)} bytes) via Whisper.")
            transcript_response = client.audio.transcriptions.create(model="whisper-1", file=audio_content)
            speech_result = transcript_response.text.strip()
            logging.info(f"Call {call_sid}: Whisper transcript: '{speech_result}'")
        except requests.exceptions.HTTPError as http_err:
            logging.error(f"Call {call_sid}: Failed Twilio audio download: {http_err.response.status_code} - {http_err.response.text[:200]}", exc_info=True)
            notify_error(call_sid, f"RecURL: {recording_url}", f"Twilio Audio DL HTTP Err: {http_err.response.status_code}")
            return say_fallback_with_gather("I had trouble retrieving what you said. Could you please repeat that?")
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Call {call_sid}: Failed Twilio audio download (Net): {req_err}", exc_info=True)
            notify_error(call_sid, f"RecURL: {recording_url}", f"Twilio Audio DL Net Err: {req_err}")
            return say_fallback_with_gather("Couldn't get what you said due to a network hiccup. Please say it again.")
        except Exception as e:
            logging.error(f"Call {call_sid}: Whisper transcription/audio handling error: {e}", exc_info=True)
            notify_error(call_sid, f"RecURL: {recording_url}", f"Whisper/Audio Err: {e}")
            return say_fallback_with_gather("I had a bit of trouble understanding that. Could you say it again?")
    elif recording_duration == 0: logging.info(f"Call {call_sid}: No speech detected (0s duration)."); speech_result = ""
    else: logging.warning(f"Call {call_sid}: No RecordingUrl. Duration: {recording_duration}s."); speech_result = ""
    
    system_prompt = """
    You are Sol, the emotionally intelligent AI receptionist for Northern Skin Doctors. 
    You sound like a real person — warm, thoughtful, and precise. Never robotic.
    - Always respond clearly and helpfully.
    - If the caller wants to book, always ask for type of appointment, name, and preferred date.
    - Never say you are an AI. Never say you’re confused.
    - Do not end the conversation without follow-up unless the caller explicitly says they are done or hangs up.
    - End helpful responses with: 'I'll pass this to the team to confirm by SMS. Thanks!'
    """
    
    # *** SYNTAX ERROR FIX APPLIED HERE ***
    user_message_for_gpt = speech_result or "Is anyone there?" # Prepare the message content
    logging.info(f"Call {call_sid}: Sending to OpenAI GPT-4o: '{user_message_for_gpt}'") # Log it cleanly

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message_for_gpt} # Use the prepared message
    ]
    
    gpt_reply_text = "I'm sorry, I'm having a little trouble formulating a response. Please try again shortly."
    try:
        openai_response = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.4)
        gpt_reply_text = openai_response.choices[0].message.content.strip()
        logging.info(f"Call {call_sid}: GPT-4o reply: '{gpt_reply_text}'")
    except Exception as e:
        logging.error(f"Call {call_sid}: Error calling OpenAI GPT-4o:", exc_info=True)
        notify_error(call_sid, user_message_for_gpt, f"OpenAI GPT-4o Error: {traceback.format_exc()}")

    public_audio_url, fallback_text = text_to_elevenlabs_audio(gpt_reply_text, call_sid)
    vr = VoiceResponse()
    if public_audio_url: vr.play(public_audio_url)
    else: logging.warning(f"Call {call_sid}: ElevenLabs failed for GPT reply, using Polly for: '{fallback_text[:80]}...'"); vr.say(fallback_text, voice="Polly.Brian", language="en-AU")
    
    if not ("hang up and call 000" in gpt_reply_text.lower()):
        record_next = Record(action="/handle_speech_input", method="POST", timeout=7, maxLength=30, playBeep=False, trim="trim-silence")
        vr.append(record_next)
    else: logging.info(f"Emergency message for {call_sid}. Not recording.")
    
    twiml_to_send = str(vr); logging.info(f"Call {call_sid}: Responding from /handle_speech_input with TwiML: {twiml_to_send}"); return Response(twiml_to_send, mimetype="text/xml")

# ------------------------------------------------------------------
# Helper Functions (notify_error, say_fallback, say_fallback_with_gather)
# ------------------------------------------------------------------
def say_fallback(text_to_say):
    logging.info(f"Using say_fallback (no gather) for: '{text_to_say[:80]}...'")
    vr = VoiceResponse(); vr.say(text_to_say, voice="Polly.Brian", language="en-AU")
    twiml_to_send = str(vr); logging.info(f"Fallback TwiML (no gather): {twiml_to_send}"); return Response(twiml_to_send, mimetype="text/xml")

def say_fallback_with_gather(text_to_say):
    logging.info(f"Using say_fallback_with_gather for: '{text_to_say[:80]}...'")
    vr = VoiceResponse(); vr.say(text_to_say, voice="Polly.Brian", language="en-AU")
    if not ("hang up and call 000" in text_to_say.lower()):
        record = Record(action="/handle_speech_input", method="POST", timeout=7, maxLength=30, playBeep=False, trim="trim-silence")
        vr.append(record)
    else: logging.info(f"Emergency in say_fallback_with_gather. Not recording.")
    twiml_to_send = str(vr); logging.info(f"Fallback TwiML (with Record): {twiml_to_send}"); return Response(twiml_to_send, mimetype="text/xml")

def notify_error(call_sid, context, error_details):
    if not (SMTP_SERVER and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD and ADMIN_EMAIL):
        logging.error(f"SMTP/AdminEmail missing. No error email for CallSid {call_sid}.")
        return
    msg = EmailMessage(); msg["Subject"] = f"[Sol Voice Error] Call {call_sid}"; msg["From"] = "Sol AI <no-reply@northernskindoctors.com.au>"; msg["To"] = ADMIN_EMAIL
    msg.set_content(f"Error with Sol.\n\nCallSid: {call_sid}\nContext: '{context}'\n\nError:\n{error_details}")
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp: smtp.starttls(); smtp.login(SMTP_USERNAME, SMTP_PASSWORD); smtp.send_message(msg)
        logging.info(f"Error email sent for CallSid {call_sid} to {ADMIN_EMAIL}.")
    except Exception as e: logging.error(f"Failed to send error email for CallSid {call_sid}:", exc_info=True)

# ------------------------------------------------------------------
# Run App
# ------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    use_debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=use_debug_mode)
