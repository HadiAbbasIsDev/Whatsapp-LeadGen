#!/usr/bin/env python3
"""
Transcribe the most recent voice/audio message from a WhatsApp user.
Looks for the newest audio file in ~/.openclaw/media/inbound/, caches it locally,
rejects messages longer than 2 minutes, then runs OpenRouter transcription.
Optionally accepts --phone to tag the transcription file.
"""

import os
import sys
import json
import subprocess
import hashlib
import shutil
import base64
import wave
import argparse
import time

INBOUND_DIR = os.path.expanduser("~/.openclaw/media/inbound")
MEDIA_STORE_INBOUND_DIR = os.path.expanduser("~/.openclaw/media/inbound")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_cache")
AUDIO_CACHE_DIR = os.path.join(CACHE_DIR, "audio")
TRANSCRIPT_CACHE_DIR = os.path.join(CACHE_DIR, "transcripts")
OPENROUTER_TRANSCRIPTION_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
MODEL = "openai/whisper-large-v3-turbo"
MAX_DURATION_SECONDS = 120
MAX_AUDIO_AGE_SECONDS = 5 * 60

AUDIO_EXTENSIONS = {".ogg", ".opus", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".webm", ".oga"}


def load_dotenv():
    """Load simple KEY=VALUE lines from the repo .env when the caller did not export them."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(repo_root, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        return


def has_audio_stream(path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "audio" in result.stdout


def iter_inbound_candidates():
    seen = set()
    for directory in (INBOUND_DIR, MEDIA_STORE_INBOUND_DIR):
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if path in seen or not os.path.isfile(path):
                continue
            seen.add(path)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if time.time() - stat.st_mtime > MAX_AUDIO_AGE_SECONDS:
                continue
            yield stat.st_mtime, path


def find_latest_audio():
    """Find the most recently modified audio file from OpenClaw's inbound media store."""
    candidates = sorted(iter_inbound_candidates(), reverse=True)
    for _, path in candidates:
        ext = os.path.splitext(path)[1].lower()
        if ext in AUDIO_EXTENSIONS or has_audio_stream(path):
            return path
    return None


def print_error(message, code="transcription_failed", **extra):
    payload = {"status": "error", "code": code, "message": message}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(1)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def cache_audio(audio_path, cache_key):
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    ext = os.path.splitext(audio_path)[1].lower() or ".audio"
    cached_path = os.path.join(AUDIO_CACHE_DIR, f"{cache_key}{ext}")
    if not os.path.exists(cached_path):
        shutil.copy2(audio_path, cached_path)
    return cached_path


def duration_with_ffprobe(audio_path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def duration_with_wave(audio_path):
    try:
        with wave.open(audio_path, "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return frames / float(rate) if rate else None
    except (wave.Error, EOFError, OSError):
        return None


def get_duration_seconds(audio_path):
    duration = duration_with_ffprobe(audio_path)
    if duration is not None:
        return duration
    return duration_with_wave(audio_path)


def prepare_wav(cached_audio_path, cache_key):
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    ext = os.path.splitext(cached_audio_path)[1].lower()
    if ext == ".wav":
        return cached_audio_path

    wav_path = os.path.join(AUDIO_CACHE_DIR, f"{cache_key}.wav")
    if os.path.exists(wav_path):
        return wav_path

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        cached_audio_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        wav_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print_error(
            "Could not prepare voice message for transcription",
            code="audio_conversion_failed",
            detail=result.stderr.strip()[:500],
        )
    return wav_path


def transcribe_with_openrouter(wav_path):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print_error("OPENROUTER_API_KEY is not set", code="missing_api_key")

    try:
        import requests
    except ImportError:
        print_error("Python package 'requests' is not installed", code="missing_dependency")

    with open(wav_path, "rb") as f:
        base64_audio = base64.b64encode(f.read()).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = os.environ.get("OPENROUTER_HTTP_REFERER")
    title = os.environ.get("OPENROUTER_APP_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-OpenRouter-Title"] = title

    try:
        response = requests.post(
            url=OPENROUTER_TRANSCRIPTION_URL,
            headers=headers,
            data=json.dumps(
                {
                    "model": MODEL,
                    "input_audio": {
                        "data": base64_audio,
                        "format": "wav",
                    },
                }
            ),
            timeout=90,
        )
    except requests.RequestException as e:
        print_error(str(e), code="openrouter_request_failed")

    if response.status_code >= 400:
        print_error(
            "OpenRouter transcription failed",
            code="openrouter_error",
            detail=response.text[:500],
            status_code=response.status_code,
        )

    try:
        result = response.json()
    except ValueError:
        print_error("OpenRouter returned non-JSON response", code="openrouter_bad_response")

    text = (result.get("text") or "").strip()
    if not text:
        print_error("No transcription produced", code="empty_transcript")
    return text


def transcribe(audio_path, phone=None):
    """Cache, validate, and transcribe the audio file."""
    os.makedirs(TRANSCRIPT_CACHE_DIR, exist_ok=True)

    cache_key = file_hash(audio_path)
    cached_audio_path = cache_audio(audio_path, cache_key)
    txt_path = os.path.join(TRANSCRIPT_CACHE_DIR, f"{cache_key}.txt")

    duration_seconds = get_duration_seconds(cached_audio_path)
    if duration_seconds is None:
        print_error(
            "Could not determine voice message duration",
            code="duration_unknown",
            audio_cache_file=cached_audio_path,
        )

    if duration_seconds > MAX_DURATION_SECONDS:
        print_error(
            "Voice message is longer than 2 minutes",
            code="audio_too_long",
            duration_seconds=round(duration_seconds, 2),
            max_duration_seconds=MAX_DURATION_SECONDS,
            audio_cache_file=cached_audio_path,
        )

    if os.path.exists(txt_path):
        with open(txt_path, "r") as f:
            text = f.read().strip()
        print(f"TRANSCRIPT: {text}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "status": "ok",
                    "text": text,
                    "cache_file": txt_path,
                    "audio_cache_file": cached_audio_path,
                    "duration_seconds": round(duration_seconds, 2),
                    **({"phone": phone} if phone else {}),
                },
                ensure_ascii=False,
            )
        )
        return

    wav_path = prepare_wav(cached_audio_path, cache_key)
    text = transcribe_with_openrouter(wav_path)

    with open(txt_path, "w") as f:
        f.write(text)

    print(f"TRANSCRIPT: {text}", file=sys.stderr)
    result = {
        "status": "ok",
        "text": text,
        "cache_file": txt_path,
        "audio_cache_file": cached_audio_path,
        "processed_audio_file": wav_path,
        "duration_seconds": round(duration_seconds, 2),
    }

    if phone:
        result["phone"] = phone

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Transcribe latest WhatsApp voice message")
    parser.add_argument("--phone", type=str, help="Customer phone number for logging")
    parser.add_argument("--audio", type=str, help="Explicit audio file path from the inbound media context")
    args = parser.parse_args()

    audio_path = args.audio or find_latest_audio()
    if not audio_path:
        print_error("No audio file found in inbound directory", code="no_audio_found")
    if not os.path.exists(audio_path):
        print_error("Audio file path does not exist", code="audio_not_found", audio_path=audio_path)

    transcribe(audio_path, phone=args.phone)
