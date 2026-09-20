"""Microphone recorder: 44.1 kHz mono WAV to data/audio/<session_id>_audio.wav.

Modified 2026-09 (iteration 5): file named from the shared session id; stops on the
orchestrator's stop signal so the SoundFile context closes and the WAV header is
finalised (being terminated left 44-byte files); Whisper transcription is off unless
PLAYSMART_TRANSCRIBE=1 and needs `poetry install --with transcription`.
"""

import sounddevice as sd
import soundfile as sf
import datetime
import os
import sys
from pathlib import Path
from pynput import keyboard

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session

samplerate = 44100
channels = 1
recording = True  # control flag

# Transcription is off by default. It used to run inline, right after the
# recording loop, while the orchestrator was terminating this process two
# seconds later — so Whisper was always killed, and worse, killing it before
# the SoundFile context manager closed left an unfinalised WAV header. That is
# what the 44-byte "empty" WAVs in the old archive are. Transcribe afterwards
# instead: python src/microphone_recording.py --transcribe <file.wav>
TRANSCRIBE = os.environ.get("PLAYSMART_TRANSCRIBE", "0") == "1"

# ------------------ PATH SETUP ------------------
filepath = str(session.stream_path("audio", ext="wav"))

print(f"Recording microphone audio to: {filepath}")
print("Stops when the game ends, or on F12.")

# ------------------ KEY LISTENER ------------------
def on_press(key):
    global recording
    if key == keyboard.Key.f12:
        print("\nF12 pressed -> stopping recording...")
        recording = False
        return False  # stop listener

listener = keyboard.Listener(on_press=on_press)
listener.start()

# ------------------ RECORD AUDIO ------------------
# Leaving this `with` block is what finalises the WAV header, so the loop must
# be allowed to exit normally rather than the process being killed.
with sf.SoundFile(filepath, mode='w', samplerate=samplerate, channels=channels) as file:
    with sd.InputStream(samplerate=samplerate, channels=channels) as stream:
        while recording and not session.stop_requested():
            data, _ = stream.read(1024)
            file.write(data)

print(f"Recording stopped, saved {filepath}")

# ------------------ TRANSCRIPTION ------------------
if not TRANSCRIBE:
    print("Skipping transcription (set PLAYSMART_TRANSCRIBE=1 to enable).")
    sys.exit(0)

import whisper  # noqa: E402  (imported late: loading it costs seconds we do not want at capture time)

print("Loading Whisper model...")
model = whisper.load_model("base")  # tiny/base/small/medium/large

print("Transcribing audio...")
result = model.transcribe(filepath)

segments = result["segments"]

# ------------------ FORMAT TRANSCRIPT ------------------
def format_time(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

formatted_transcript = ""

for seg in segments:
    start = format_time(seg["start"])
    end = format_time(seg["end"])
    text = seg["text"].strip()

    formatted_transcript += f"[{start} - {end}] {text}\n"

print("Transcription with timestamps:\n")
print(formatted_transcript)

# ------------------ SAVE TRANSCRIPT ------------------
txt_path = filepath.replace(".wav", ".txt")

with open(txt_path, "w", encoding="utf-8") as f:
    f.write(formatted_transcript)

print(f"Transcript saved to: {txt_path}")