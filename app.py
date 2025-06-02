"""
Sol Voice Assistant - GPT-4o + Whisper STT + ElevenLabs TTS
Version: v3.6.4 (Complete Fixed Version)
"""

import os
import traceback
import logging
import smtplib
from email.message import EmailMessage
from flask import Flask, request, Response, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Say, Record
from twilio.rest import Client
from openai import OpenAI
import uuid
import requests
import time
import io

# ------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "vVnXvLYPFjIyE2YrjUBE")
RENDER_APP_BASE_URL = os.getenv("RENDER_EXTERNAL_URL")
AUDIO_HOST_URL = RENDER_APP_BASE_URL or os.getenv("AUDIO_HOST_URL_MANUAL", "http://localhost:5000")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "manager@northernskindoctors.com.au")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.sendgrid.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

# ------------------------------------------------------------------
# Environment Checks and Initialization
# ------------------------------------------------------------------
startup_warnings = []
if not OPENAI_API_KEY: 
    startup_warnings.append("CRITICAL STARTUP ERROR: OPENAI_API_KEY not set.")
if not ELEVENLABS_API_KEY: 
    startup_warnings.append("STARTUP WARNING: ELEVENLABS_API_KEY not set.")
if not ADMIN_EMAIL: 
    startup_warnings.append("STARTUP WARNING: ADMIN_EMAIL not set.")
if not (SMTP_SERVER and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD):
    startup_warnings.append("STARTUP WARNING: SMTP variables missing.")
if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
    startup_warnings.append("CRITICAL STARTUP ERROR: TWILIO credentials missing.")

for warning in startup_warnings:
    if "CRITICAL" in warning:
        logging.error(warning)
    else:
        logging.warning(warning)

# Initialize clients
client = None
twilio_client = None

if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized successfully at startup.")
    except Exception as e:
        logging.error(f"Failed to initialize OpenAI client: {e}")

if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logging.info("Twilio client initialized successfully at startup.")
    except Exception as e:
        logging.error(f"Failed to initialize Twilio client: {e}")

# Audio directory setup
TEMP_AUDIO_DIR_NAME = "temp_audio_files"
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TEMP_AUDIO_DIR_PATH = os.path.join(BASE_DIR, TEMP_AUDIO_DIR_NAME)
os.makedirs(TEMP_AUDIO_DIR_PATH, exist_ok=True)
logging.info(f"Using RENDER_EXTERNAL_URL for AUDIO_HOST_URL: {AUDIO_HOST_URL}")
logging.info(f"Temporary audio directory ensured at: {TEMP_AUDIO_DIR_PATH}")

SYSTEM_PROMPT = """
You are Sol, the emotionally intelligent AI receptionist for Northern Skin Doctors. 
You sound like a real person — warm, thoughtful, and precise. Never robotic.
- Always respond clearly and helpfully.
- If the caller wants to book, always ask for type of appointment, name, and preferred date.
- Never say you are an AI. Never say you're confused.
- Always ask follow-up questions to keep the conversation flowing naturally unless the user explicitly says they are done or hangs up.
- End helpful responses (like after taking booking details or answering a query) with: 'I'll pass this to the team to confirm by SMS. Thanks!'
"""

app = Flask(__name__)

# ------------------------------------------------------------------
# Utility Functions
# ------------------------------------------------------------------

def send_error_email(call_sid, error_msg):
    """Send error notification email to admin."""
    if not all([SMTP_SERVER, SMTP_USERNAME, SMTP_PASSWORD, ADMIN_EMAIL]):
        logging.warning("SMTP not configured, skipping error email.")
        return
    
    try:
        msg = EmailMessage()
        msg['Subject'] = f"Sol Voice Assistant Error - Call {call_sid}"
        msg['From'] = SMTP_USERNAME
        msg['To'] = ADMIN_EMAIL
        msg.set_content(f"Error occurred in call {call_sid}:\n\n{error_msg}")
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        
        logging.info(f"Error email sent for CallSid {call_sid} to {ADMIN_EMAIL}.")
    except Exception as e:
        logging.error(f"Failed to send error email: {e}")

def download_recording_with_retry(recording_url, call_sid, max_retries=3, initial_delay=2):
    """Download Twilio recording with exponential backoff retry."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
        logging.error(f"Call {call_sid}: Missing Twilio credentials for download")
        return None
    
    delay = initial_delay
    for attempt in range(max_retries):
        if attempt > 0:
            logging.info(f"Call {call_sid}: Retrying download in {delay} seconds...")
            time.sleep(delay)
        delay = min(delay * 2, 10)

        try:
            logging.info(f"Call {call_sid}: Attempting authenticated download (attempt {attempt+1})")
            auth_tuple = (TWILIO_ACCOUNT_SID.strip(), TWILIO_AUTH_TOKEN.strip())
            response = requests.get(recording_url, auth=auth_tuple, timeout=15)
            response.raise_for_status()
            
            if len(response.content) > 0:
                logging.info(f"Call {call_sid}: Successfully downloaded {len(response.content)} bytes")
                return response.content
            else:
                logging.warning(f"Call {call_sid}: Downloaded empty content")
                
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logging.error(f"Call {call_sid}: Unauthorized (401) - check Twilio credentials")
                break  # No point retrying auth errors
            elif e.response.status_code == 404:
                logging.warning(f"Call {call_sid}: Recording not ready yet (404), attempt {attempt+1}")
            else:
                logging.error(f"Call {call_sid}: HTTP error {e.response.status_code}: {e}")
        except Exception as e:
            logging.error(f"Call {call_sid}: Download error attempt {attempt+1}: {e}")

    logging.error(f"Call {call_sid}: All download attempts failed for {recording_url}")
    return None

def generate_elevenlabs_audio(text, call_sid):
    """Generate audio using ElevenLabs TTS."""
    if not ELEVENLABS_API_KEY:
        logging.warning(f"Call {call_sid}: ElevenLabs API key not set, skipping TTS")
        return None
    
    try:
        logging.info(f"Call {call_sid}: Requesting audio from ElevenLabs for: '{text[:50]}...'")
        
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY
        }
        
        data = {
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.0,
                "use_speaker_boost": True
            }
        }
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
        response = requests.post(url, json=data, headers=headers, timeout=30)
        response.raise_for_status()
        
        if len(response.content) > 0:
            logging.info(f"Call {call_sid}: Total bytes written for MP3 from ElevenLabs: {len(response.content)}")
            return response.content
        else:
            logging.error(f"Call {call_sid}: ElevenLabs returned empty audio")
            return None
            
    except Exception as e:
        logging.error(f"Call {call_sid}: ElevenLabs TTS error: {e}")
        return None

def save_audio_file(audio_content, call_sid, prefix="sol_audio"):
    """Save audio content to file and return URL."""
    try:
        unique_id = str(uuid.uuid4()).replace('-', '')
        filename = f"{prefix}_{unique_id}.mp3"
        file_path = os.path.join(TEMP_AUDIO_DIR_PATH, filename)
        
        with open(file_path, 'wb') as f:
            f.write(audio_content)
        
        audio_url = f"{AUDIO_HOST_URL}/{TEMP_AUDIO_DIR_NAME}/{filename}"
        logging.info(f"Call {call_sid}: Saved audio file: {file_path}. URL: {audio_url}")
        return audio_url
        
    except Exception as e:
        logging.error(f"Call {call_sid}: Failed to save audio file: {e}")
        return None

def say_fallback_with_gather(text, call_sid):
    """Generate fallback TwiML with Polly voice."""
    logging.info(f"Using say_fallback_with_gather for: '{text[:50]}...'")
    
    response = VoiceResponse()
    response.say(text, language="en-AU", voice="Polly.Brian")
    response.record(
        action="/handle_speech_input",
        method="POST",
        max_length=30,
        timeout=7,
        play_beep=False,
        trim="trim-silence"
    )
    
    twiml_str = str(response)
    logging.info(f"Fallback TwiML (with Record): {twiml_str}")
    return twiml_str

# ------------------------------------------------------------------
# Flask Routes
# ------------------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return "✅ Sol Voice v3.6.4 is live."

@app.route(f"/{TEMP_AUDIO_DIR_NAME}/<path:filename>")
def serve_audio(filename):
    """Serve audio files."""
    try:
        safe_filename = os.path.basename(filename)
        logging.info(f"Attempting to serve audio file: {safe_filename} from {TEMP_AUDIO_DIR_PATH}")
        return send_from_directory(TEMP_AUDIO_DIR_PATH, safe_filename, as_attachment=False)
    except FileNotFoundError:
        logging.error(f"Audio file not found: {filename}")
        return "File not found", 404
    except Exception as e:
        logging.error(f"Error serving audio file {filename}: {e}")
        return "Error serving file", 500

@app.route("/voice", methods=["POST"])
def handle_incoming_call():
    """Handle incoming Twilio voice calls."""
    call_sid = request.form.get('CallSid', 'unknown')
    logging.info(f"Initial call {call_sid} received at /voice.")
    
    try:
        greeting_text = (
            "Hello! You've reached Northern Skin Doctors. This is Sol, your virtual assistant. "
            "I'm here to help with appointments, questions about our services, or general inquiries. "
            "How can I assist you today?"
        )
        
        # Try ElevenLabs first
        audio_content = generate_elevenlabs_audio(greeting_text, call_sid)
        
        if audio_content:
            audio_url = save_audio_file(audio_content, call_sid)
            if audio_url:
                response = VoiceResponse()
                response.play(audio_url)
                response.record(
                    action="/handle_speech_input",
                    method="POST",
                    max_length=30,
                    timeout=7,
                    play_beep=False,
                    trim="trim-silence"
                )
                
                twiml_str = str(response)
                logging.info(f"Call {call_sid}: Responding from /voice with TwiML: {twiml_str}")
                return Response(twiml_str, mimetype="application/xml")
        
        # Fallback to Polly
        logging.warning(f"Call {call_sid}: ElevenLabs failed, using Polly fallback")
        fallback_twiml = say_fallback_with_gather(greeting_text, call_sid)
        return Response(fallback_twiml, mimetype="application/xml")
        
    except Exception as e:
        logging.error(f"Call {call_sid}: Error in /voice: {e}")
        send_error_email(call_sid, f"Error in /voice endpoint: {e}")
        
        # Emergency fallback
        emergency_response = VoiceResponse()
        emergency_response.say(
            "I'm sorry, there's a technical issue. Please call back later or visit our website.",
            language="en-AU", 
            voice="Polly.Brian"
        )
        return Response(str(emergency_response), mimetype="application/xml")

@app.route("/handle_speech_input", methods=["POST"])
def handle_speech_input():
    """Handle speech input from Twilio."""
    call_sid = request.form.get('CallSid', 'unknown')
    recording_url = request.form.get('RecordingUrl', '')
    recording_duration = request.form.get('RecordingDuration', '0')
    
    logging.info(f"Call {call_sid}: /handle_speech_input. RecordingURL: {recording_url}, Duration: {recording_duration}s")
    
    if not recording_url:
        logging.error(f"Call {call_sid}: No recording URL provided")
        send_error_email(call_sid, "No recording URL in speech input")
        fallback_text = "I didn't receive your message. Could you please try again?"
        return Response(say_fallback_with_gather(fallback_text, call_sid), mimetype="application/xml")
    
    try:
        # Download the recording
        audio_content = download_recording_with_retry(recording_url, call_sid)
        
        if not audio_content:
            logging.error(f"Call {call_sid}: Failed to download recording")
            send_error_email(call_sid, f"Failed to download recording: {recording_url}")
            fallback_text = "I had trouble getting your recording. Could you please repeat that?"
            return Response(say_fallback_with_gather(fallback_text, call_sid), mimetype="application/xml")
        
        # Transcribe with Whisper
        if not client:
            logging.error(f"Call {call_sid}: OpenAI client not initialized")
            send_error_email(call_sid, "OpenAI client not available")
            fallback_text = "I'm having trouble processing your request. Please try again."
            return Response(say_fallback_with_gather(fallback_text, call_sid), mimetype="application/xml")
        
        audio_file = io.BytesIO(audio_content)
        audio_file.name = "recording.wav"  # Whisper needs a filename
        
        logging.info(f"Call {call_sid}: Transcribing audio with Whisper...")
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="en"
        )
        
        user_text = transcription.text.strip()
        logging.info(f"Call {call_sid}: Transcribed: '{user_text}'")
        
        if not user_text:
            logging.warning(f"Call {call_sid}: Empty transcription")
            fallback_text = "I couldn't hear what you said clearly. Could you please repeat that?"
            return Response(say_fallback_with_gather(fallback_text, call_sid), mimetype="application/xml")
        
        # Generate response with GPT-4
        logging.info(f"Call {call_sid}: Generating response with GPT-4...")
        chat_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text}
            ],
            max_tokens=200,
            temperature=0.7
        )
        
        ai_response = chat_response.choices[0].message.content.strip()
        logging.info(f"Call {call_sid}: GPT-4 response: '{ai_response}'")
        
        # Generate audio response
        audio_content = generate_elevenlabs_audio(ai_response, call_sid)
        
        if audio_content:
            audio_url = save_audio_file(audio_content, call_sid)
            if audio_url:
                response = VoiceResponse()
                response.play(audio_url)
                response.record(
                    action="/handle_speech_input",
                    method="POST",
                    max_length=30,
                    timeout=7,
                    play_beep=False,
                    trim="trim-silence"
                )
                return Response(str(response), mimetype="application/xml")
        
        # Fallback to Polly
        logging.warning(f"Call {call_sid}: Using Polly fallback for response")
        return Response(say_fallback_with_gather(ai_response, call_sid), mimetype="application/xml")
        
    except Exception as e:
        logging.error(f"Call {call_sid}: Error in /handle_speech_input: {e}")
        logging.error(traceback.format_exc())
        send_error_email(call_sid, f"Error in speech input handler: {e}\n{traceback.format_exc()}")
        
        fallback_text = "I encountered an issue processing your request. Could you please try again?"
        return Response(say_fallback_with_gather(fallback_text, call_sid), mimetype="application/xml")

# ------------------------------------------------------------------
# Application Startup
# ------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
