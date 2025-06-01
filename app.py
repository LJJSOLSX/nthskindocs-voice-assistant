"""
Sol Voice Assistant - GPT-4o + Whisper STT + ElevenLabs TTS
Version: v3.5 (Enhanced ElevenLabs Debugging)
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
# TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are not strictly critical for app startup but for reliable recording download
if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN): startup_warnings.append("STARTUP INFO: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN missing. May be needed for downloading Twilio recordings if protected.")

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
    return "✅ Sol Voice v3.5 is live (Enhanced ElevenLabs Debugging)."

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
            headers={
                "Accept": "audio/mpeg", "xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"
            },
            json={
                "text": text_to_speak, "model_id": "eleven_monolingual_v1",
                "voice_settings": {"stability": 0.7, "similarity_boost": 0.75}
            },
            timeout=20,
            stream=True # Process as a stream
        )
        tts_response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        total_bytes_written = 0
        with open(audio_file_path, "wb") as f:
            for chunk in tts_response.iter_content(chunk_size=4096):
                if chunk:
                    f.write(chunk)
                    total_bytes_written += len(chunk)
        
        logging.info(f"Call {call_sid_for_log}: Total bytes written for MP3 from ElevenLabs: {total_bytes_written}")

        if total_bytes_written == 0:
            logging.error(f"Call {call_sid_for_log}: ElevenLabs returned 200 OK but with 0 bytes of audio data for text: '{text_to_speak[:80]}...'. Forcing Polly fallback.")
            try: # Attempt to remove the empty file
                if os.path.exists(audio_file_path): os.remove(audio_file_path)
                logging.info(f"Call {call_sid_for_log}: Removed potentially empty audio file: {audio_file_path}")
            except OSError as rm_err:
                logging.warning(f"Call {call_sid_for_log}: Could not remove empty/problematic audio file '{audio_file_path}': {rm_err}")
            return None, text_to_speak # Force fallback to Polly

        time.sleep(0.5) # User's requested delay
        logging.info(f"Call {call_sid_for_log}: Saved ElevenLabs MP3: {audio_file_path}. URL: {public_audio_url}")
        return public_audio_url, text_to_speak

    except requests.exceptions.HTTPError as http_err:
        logging.error(f"Call {call_sid_for_log}: HTTP error calling ElevenLabs: {http_err.response.status_code} - {http_err.response.text[:200]}", exc_info=True)
        notify_error(call_sid_for_log, f"TTS for: {text_to_speak[:50]}...", f"ElevenLabs HTTP Error: {http_err.response.status_code} - {http_err.response.text[:200]}")
        return None, text_to_speak
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Call {call_sid_for_log}: Network error calling ElevenLabs:", exc_info=True)
        notify_error(call_sid_for_log, f"TTS for: {text_to_speak[:50]}...", f"ElevenLabs Network Error: {req_err}")
        return None, text_to_speak
    except Exception as e:
        logging.error(f"Call {call_sid_for_log}: Unexpected error in text_to_elevenlabs_audio:", exc_info=True)
        notify_error(call_sid_for_log, f"TTS for: {text_to_speak[:50]}...", f"TTS Generation Error: {e}")
        return None, text_to_speak

@app.route("/voice", methods=["POST"])
def initial_voice_handler():
    call_sid = request.values.get("CallSid", "UnknownCallSid")
    logging.info(f"Initial call {call_sid} received at /voice.")

    if not client:
        error_msg = "OpenAI client not initialized. Cannot proceed with intelligent interaction."
        logging.error(f"Call {call_sid}: {error_msg}")
        notify_error(call_sid, "Initial Call", error_msg)
        return say_fallback("Our system is experiencing issues. Please try again later.")

    greeting_text = "Hello! You've reached Northern Skin Doctors. This is Sol, your virtual assistant. How can I assist you today?"
    public_audio_url, _ = text_to_elevenlabs_audio(greeting_text, call_sid)
    
    vr = VoiceResponse()
    if public_audio_url:
        vr.play(public_audio_url)
    else:
        logging.warning(f"Call {call_sid}: ElevenLabs failed for initial greeting, using Polly.")
        vr.say(greeting_text, voice="Polly.Brian", language="en-AU")

    record = Record(action="/handle_speech_input", method="POST", timeout=7, maxLength=30, playBeep=False, trim="trim-silence")
    vr.append(record)

    twiml_to_send = str(vr)
    logging.info(f"Call {call_sid}: Responding from /voice with TwiML: {twiml_to_send}")
    return Response(twiml_to_send, mimetype="text/xml")

@app.route("/handle_speech_input", methods=["POST"])
def handle_speech_input():
    call_sid = request.values.get("CallSid", "UnknownCallSid")
    recording_url = request.values.get("RecordingUrl")
    recording_duration_str = request.values.get("RecordingDuration", "0") # Default to "0" if missing
    
    try:
        recording_duration = int(recording_duration_str)
    except ValueError:
        logging.warning(f"Call {call_sid}: Invalid RecordingDuration value '{recording_duration_str}'. Defaulting to 0.")
        recording_duration = 0

    logging.info(f"Call {call_sid}: Received speech input at /handle_speech_input. Recording URL: {recording_url}, Duration: {recording_duration}s")

    if not client:
        error_msg = "OpenAI client not initialized. Cannot process speech."
        logging.error(f"Call {call_sid}: {error_msg}")
        notify_error(call_sid, f"RecordingURL: {recording_url}", error_msg)
        return say_fallback("Our system is having trouble. Please try again later.")

    speech_result = ""
    if recording_url:
        try:
            auth_tuple = None
            if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN: # Check if Twilio creds are available
                auth_tuple = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            
            audio_get_response = requests.get(recording_url, auth=auth_tuple, timeout=10)
            audio_get_response.raise_for_status()
            
            audio_content = io.BytesIO(audio_get_response.content)
            audio_content.name = f"recording_{call_sid}.wav" # Assume .wav, adjust if Twilio uses .mp3 by default for <Record>

            logging.info(f"Call {call_sid}: Transcribing audio ({len(audio_get_response.content)} bytes) using Whisper.")
            transcript_response = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_content
            )
            speech_result = transcript_response.text.strip()
            logging.info(f"Call {call_sid}: Whisper transcript: '{speech_result}'")
        except requests.exceptions.HTTPError as http_err:
            logging.error(f"Call {call_sid}: Failed to download audio from Twilio: {http_err.response.status_code} - {http_err.response.text[:200]}", exc_info=True)
            notify_error(call_sid, f"RecordingURL: {recording_url}", f"Twilio Audio Download HTTP Error: {http_err.response.status_code}")
            return say_fallback_with_gather("I had trouble retrieving what you said. Could you please repeat that?")
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Call {call_sid}: Failed to download audio from Twilio (Network): {req_err}", exc_info=True)
            notify_error(call_sid, f"RecordingURL: {recording_url}", f"Twilio Audio Download Network Error: {req_err}")
            return say_fallback_with_gather("I couldn't get what you said due to a network hiccup. Please say it again.")
        except Exception as e: # Catch OpenAI or other errors during transcription
            logging.error(f"Call {call_sid}: Failed to transcribe audio with Whisper or other error: {e}", exc_info=True)
            notify_error(call_sid, f"RecordingURL: {recording_url}", f"Whisper Transcription/Audio Handling Error: {e}")
            return say_fallback_with_gather("I had a bit of trouble understanding that. Could you say it again?")
    elif recording_duration == 0:
        logging.info(f"Call {call_sid}: No speech detected (0s duration or timeout).")
        speech_result = "" # Will be handled by "Is anyone there?"
    else: # No RecordingUrl but duration > 0 (should not happen with <Record>) or other issue
        logging.warning(f"Call {call_sid}: No RecordingUrl from Twilio, or issue with recording. Duration: {recording_duration}s.")
        speech_result = "" # Treat as empty

    system_prompt = """
    You are Sol, the emotionally intelligent AI receptionist for Northern Skin Doctors. 
    You sound like a real person — warm, thoughtful, and precise. Never robotic.
    - Always respond clearly and helpfully.
    - If the caller wants to book, always ask for type of appointment, name, and preferred date.
    - Never say you are an AI. Never say you’re confused.
    - Do not end the conversation without follow-up unless the caller explicitly says they are done or hangs up.
    - End helpful responses with: 'I'll pass this to the team to confirm by SMS. Thanks!'
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": speech_result or "Is anyone there?"} # Handle empty transcript
    ]
    
    gpt_reply_text = "I'm sorry, I'm having a little trouble formulating a response right now. Please try again shortly."
    try:
        logging.info(f"Call {call_sid}: Sending transcript to OpenAI GPT-4o.")
        openai_response = client.chat.completions.create(
            model="gpt-4o", messages=messages, temperature=0.4
        )
        gpt_reply_text = openai_response.choices[0].message.content.strip()
        logging.info(f"Call {call_sid}: GPT-4o text reply: '{gpt_reply_text}'")
    except Exception as e:
        logging.error(f"Call {call_sid}: Error calling OpenAI GPT-4o:", exc_info=True)
        notify_error(call_sid, speech_result, f"OpenAI GPT-4o Error: {traceback.format_exc()}")
        # gpt_reply_text uses the default error message

    public_audio_url, final_reply_text_for_fallback = text_to_elevenlabs_audio(gpt_reply_text, call_sid)

    vr = VoiceResponse()
    if public_audio_url:
        vr.play(public_audio_url)
    else:
        logging.warning(f"Call {call_sid}: ElevenLabs failed for GPT reply, using Polly for: '{final_reply_text_for_fallback[:80]}...'")
        vr.say(final_reply_text_for_fallback, voice="Polly.Brian", language="en-AU")

    if not ("hang up and call 000" in gpt_reply_text.lower()):
        record_next = Record(action="/handle_speech_input", method="POST", timeout=7, maxLength=30, playBeep=False, trim="trim-silence")
        vr.append(record_next)
    else:
        logging.info(f"Emergency message for {call_sid}. Not recording further input.")

    twiml_to_send = str(vr)
    logging.info(f"Call {call_sid}: Responding from /handle_speech_input with TwiML: {twiml_to_send}")
    return Response(twiml_to_send, mimetype="text/xml")

# ------------------------------------------------------------------
# Helper Functions (notify_error, say_fallback, say_fallback_with_gather)
# ------------------------------------------------------------------
def say_fallback(text_to_say):
    logging.info(f"Using say_fallback (no gather) for text: '{text_to_say[:80]}...'")
    vr = VoiceResponse()
    vr.say(text_to_say, voice="Polly.Brian", language="en-AU")
    # Logging TwiML for say_fallback
    twiml_to_send = str(vr)
    logging.info(f"Fallback TwiML (no gather): {twiml_to_send}")
    return Response(twiml_to_send, mimetype="text/xml")

def say_fallback_with_gather(text_to_say):
    logging.info(f"Using say_fallback_with_gather for text: '{text_to_say[:80]}...'")
    vr = VoiceResponse()
    vr.say(text_to_say, voice="Polly.Brian", language="en-AU")
    if not ("hang up and call 000" in text_to_say.lower()):
        record = Record(action="/handle_speech_input", method="POST", timeout=7, maxLength=30, playBeep=False, trim="trim-silence")
        vr.append(record)
    else:
        logging.info(f"Emergency message in say_fallback_with_gather. Not recording.")
    # Logging TwiML for say_fallback_with_gather
    twiml_to_send = str(vr)
    logging.info(f"Fallback TwiML (with Record): {twiml_to_send}")
    return Response(twiml_to_send, mimetype="text/xml")

def notify_error(call_sid, user_input_context, error_details_text):
    if not (SMTP_SERVER and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD and ADMIN_EMAIL):
        logging.error(f"SMTP settings or ADMIN_EMAIL missing. Cannot send error email for CallSid {call_sid}.")
        return
    msg = EmailMessage()
    msg["Subject"] = f"[Sol Voice Assistant Error] Call {call_sid}"
    msg["From"] = "Sol AI Assistant <no-reply@northernskindoctors.com.au>"
    msg["To"] = ADMIN_EMAIL
    msg.set_content(f"An error occurred with Sol.\n\nCallSid: {call_sid}\nContext/Input: '{user_input_context}'\n\nError:\n{error_details_text}")
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls(); smtp.login(SMTP_USERNAME, SMTP_PASSWORD); smtp.send_message(msg)
        logging.info(f"Error email sent for CallSid {call_sid} to {ADMIN_EMAIL}.")
    except Exception as e:
        logging.error(f"Failed to send error email for CallSid {call_sid}:", exc_info=True)

# ------------------------------------------------------------------
# Run App
# ------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    use_debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=use_debug_mode)
