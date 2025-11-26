# app/voice_loop.py
import queue, sys, time, shutil, subprocess, re, traceback
import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel

from app.agent_new import run as ask_buddy  # Use new multi-agent system

# -------- config --------
SR = 16_000
FRAME_MS = 20
FRAME_SAMPLES = SR * FRAME_MS // 1000
SILENCE_GAP_S = 0.6           # consider utterance finished after this much silence
MIN_UTTERANCE_S = 0.6         # ignore ultra-short blips
POST_ANSWER_PAUSE_S = 1.2     # let room quiet down after TTS
THROTTLE_BETWEEN_Q_S = 0.6    # tiny throttle before re-arming
RATE_LIMIT_COOLDOWN_S = 24    # Groq suggests ~20–24s in error message

VAD_AGGRESSIVENESS = 2        # 0..3 (3 most aggressive)
WHISPER_MODEL = "medium.en"   # "large-v3" = best accuracy but heavier
WHISPER_COMPUTE = "int8"      # "int8" for CPU efficiency
WHISPER_LANGUAGE = "en"       # set to "es" for Spanish STT

VOICE_NAME = "Samantha"       # run `say -v ?` to list voices
VOICE_RATE = "200"

# -------- init --------
vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
stt = WhisperModel(WHISPER_MODEL, compute_type=WHISPER_COMPUTE)
_q = queue.Queue()

def _cb(indata, frames, time_info, status):
    if status:
        print(status, file=sys.stderr)
    _q.put(bytes(indata))

# -------- tts helpers --------
def speak(text: str, voice: str = VOICE_NAME, rate_wpm: str = VOICE_RATE):
    if not text:
        return
    if shutil.which("say"):
        subprocess.run(["say", "-v", voice, "-r", rate_wpm, text], check=False)
    else:
        print("(TTS)", text)

def ding():
    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)

# -------- wake-word matching --------
_WAKE_PATTERNS = [
    r"\brev\s*ops\b", r"\brev[-\s]*ops\b",
    r"\bbuddy\b", r"\bbudy\b", r"\bbaddi\b", r"\bbody\b"
]
def said_wake_word(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t, flags=re.IGNORECASE) for p in _WAKE_PATTERNS)

# -------- main loop --------
def main():
    print("-" * 55)
    print("🎤  RevOps Buddy — say “Buddy …” (Ctrl+C to exit)")
    print("-" * 55)

    buf = bytearray()
    talking = False
    last_voice = time.time()

    def flush_audio_buffers():
        buf.clear()
        # Clear any pending frames from mic
        with _q.mutex:
            _q.queue.clear()

    with sd.RawInputStream(samplerate=SR, blocksize=FRAME_SAMPLES,
                           dtype="int16", channels=1, callback=_cb):
        while True:
            try:
                pcm_bytes = _q.get()
                if not pcm_bytes:
                    continue

                # Voice activity
                is_speech = vad.is_speech(pcm_bytes, SR)

                if is_speech:
                    talking = True
                    last_voice = time.time()
                    buf.extend(pcm_bytes)

                elif talking and (time.time() - last_voice) > SILENCE_GAP_S:
                    # Utterance finished
                    talking = False

                    audio = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
                    buf.clear()

                    if len(audio) < int(MIN_UTTERANCE_S * SR):
                        # Too short; ignore
                        continue

                    # Transcribe
                    segments, _ = stt.transcribe(audio, language=WHISPER_LANGUAGE)
                    text = " ".join(s.text.strip() for s in segments).strip()
                    if not text:
                        continue

                    # Only show transcript & respond if wake word present
                    if not said_wake_word(text):
                        # Silently ignore background/tts hallucinations
                        continue

                    print("You:", text)

                    # Ask Buddy with the light voice agent
                    answer = ask_buddy(text, mode="voice")
                    print("Buddy:", answer)

                    # Rate-limit handling: pause and re-arm gently
                    lower = answer.lower()
                    if ("couldn’t reach the reasoning model" in lower) or ("couldn't reach the reasoning model" in lower):
                        print(f"[voice] rate-limited; pausing for {RATE_LIMIT_COOLDOWN_S}s…")
                        flush_audio_buffers()
                        time.sleep(RATE_LIMIT_COOLDOWN_S)
                        ding()
                        print("🎧  Ready again (say 'Buddy …')")
                        continue

                    # Speak the answer
                    speak(answer, voice=VOICE_NAME, rate_wpm=VOICE_RATE)

                    # Reset buffers and gently cool down before listening again
                    flush_audio_buffers()
                    time.sleep(POST_ANSWER_PAUSE_S)
                    ding()
                    print("🎧  Ready for next question (say 'Buddy …')")
                    time.sleep(THROTTLE_BETWEEN_Q_S)

            except KeyboardInterrupt:
                print("\n👋 Bye")
                return
            except Exception as e:
                print("[voice_loop error]", e)
                traceback.print_exc()
                # brief backoff to avoid rapid error loops
                time.sleep(0.3)

if __name__ == "__main__":
    main()
