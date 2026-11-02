# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Voice in three falls: provider, local engine, browser.

STT chain: the agent provider's /audio/transcriptions first (an
OpenAI key hears with zero local engines), then the in-process
faster-whisper engine (nova_ai_voice_local: CPU, cached, no service),
else the browser's own speech recognition (the client falls back by
itself on voice_unavailable). TTS chain: provider /audio/speech,
then Piper TTS in-process, else the browser reads aloud.

The only server-side audio work is WAV normalization (stdlib wave;
ffmpeg only for foreign containers, and the drawer always sends WAV
anyway). Local engines degrade to honest errors, never crashes, so
each link in the chain is skippable by config or by absence.
"""

import io
import wave

from odoo.exceptions import AccessError

_WAKE_PARAM = "nova_ai.voice_wake"
_WAKE_PHRASE_PARAM = "nova_ai.voice_wake_phrase"
_WAKE_PHRASE_DEFAULT = "hey nova"
_SPEAK_MAX_CHARS = 600
# ~30 seconds of 16kHz mono: long enough for a voice note, short
# enough that inference stays seconds, not minutes, and memory
# stays flat. Anything bigger is refused before a byte is decoded.
_TRANSCRIBE_MAX_BYTES = 2 * 1024 * 1024
# Refuse voice work well below Odoo's own kill lines (soft 2GB):
# a busy answer beats a dead server.
_VOICE_RSS_LIMIT_MB = 1536


def _server_strained():
    """True when the worker is too heavy for more voice work."""
    try:
        import psutil
        import os
        return (psutil.Process(os.getpid()).memory_info().rss
                // (1024 * 1024)) >= _VOICE_RSS_LIMIT_MB
    except Exception:
        return False


class NovaAiVoiceError(AccessError):
    """Raised when voice is unavailable or an utterance fails."""


def _wake_settings(env):
    try:
        wake = env["ir.config_parameter"].get_str(
            _WAKE_PARAM, "0") != "0"
    except Exception:
        wake = False
    try:
        phrase = env["ir.config_parameter"].get_str(
            _WAKE_PHRASE_PARAM, _WAKE_PHRASE_DEFAULT) \
            or _WAKE_PHRASE_DEFAULT
    except Exception:
        phrase = _WAKE_PHRASE_DEFAULT
    return wake, phrase


def _provider_audio(env, agent):
    """Whether the agent's endpoint plausibly serves audio.

    openai_compat endpoints may (OpenAI does; free gateways 404, and
    the attempt then falls back). Local runtimes never do. No call is
    spent finding out — the attempt itself is the detection.
    """
    try:
        provider = agent.model_id.provider_id if agent else None
        return bool(provider and provider.kind == "openai_compat")
    except Exception:
        return False


def voice_status(env, agent=None):
    """What the drawer needs before showing voice mode.

    `providerAudio` tells the client whether server transcription is
    worth attempting; the client ORs it with the browser's own speech
    recognition. `localStt`/`localTts` say the in-process engines are
    installed (models download on first use). No model is loaded, no
    call is spent here.
    """
    from odoo.addons.nova_ai.models import nova_ai_voice_local as local
    wake, phrase = _wake_settings(env)
    audio = _provider_audio(env, agent)
    try:
        ears = local.stt_enabled(env)
    except Exception:
        ears = False
    try:
        mouth = local.tts_enabled(env)
    except Exception:
        mouth = False
    if audio or ears:
        reason = ""
    elif mouth:
        reason = ("Server transcription is off, but replies can be "
                  "spoken aloud; the browser can listen instead where "
                  "supported.")
    else:
        reason = ("Server voice needs an OpenAI-compatible audio "
                  "endpoint or the local engines; the browser can "
                  "listen instead where supported.")
    return {"available": bool(audio or ears),
            "reason": reason,
            "providerAudio": audio,
            "localStt": ears,
            "localTts": mouth,
            "canSpeak": True,
            "wake": wake,
            "wakePhrase": phrase}


def _to_wav_bytes(env, raw, mimetype):
    """Browser audio -> 16kHz mono WAV file bytes.

    The drawer always sends WAV (worklet-built, no dependencies). Other
    containers need ffmpeg; without it they are refused honestly.
    """
    is_wav = (mimetype or "").startswith("audio/wav") \
        or bytes(raw)[:4] == b"RIFF"
    if not is_wav:
        try:
            import imageio_ffmpeg
        except ImportError:
            raise NovaAiVoiceError(env._(
                "Voice needs WAV audio from the browser."))
        import subprocess
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        proc = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
            input=bytes(raw), capture_output=True, timeout=60)
        if proc.returncode != 0 or not proc.stdout:
            raise NovaAiVoiceError(env._(
                "That recording could not be decoded."))
        import numpy as np
        pcm = np.frombuffer(proc.stdout, dtype=np.int16).tobytes()
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        return out.getvalue()
    raw = bytes(raw)
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            frames = wav.readframes(wav.getnframes())
            rate, channels, width = (wav.getframerate(),
                                     wav.getnchannels(),
                                     wav.getsampwidth())
    except Exception:
        raise NovaAiVoiceError(env._(
            "That recording could not be decoded."))
    if rate == 16000 and channels == 1 and width == 2:
        return raw
    import numpy as np
    audio = np.frombuffer(
        frames, dtype=np.int16 if width == 2 else np.int8).astype(
            "float32")
    audio = audio / (32768.0 if width == 2 else 128.0)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    count = int(len(audio) * 16000 / rate)
    audio = np.interp(np.linspace(0, len(audio), count),
                      np.arange(len(audio)), audio)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    return out.getvalue()


def transcribe_audio(env, raw, mimetype="", agent=None):
    """Bytes in, transcript out. Raises NovaAiVoiceError on failure.

    Provider first, local faster-whisper second, browser last (the
    client falls back by itself on voice_unavailable).
    """
    if not raw:
        raise NovaAiVoiceError(env._("Empty recording."))
    if len(raw) > _TRANSCRIBE_MAX_BYTES:
        raise NovaAiVoiceError(env._(
            "Recording too long — keep voice notes under 30 seconds."))
    if _server_strained():
        raise NovaAiVoiceError(env._(
            "The server is briefly busy — try again in a minute."))
    try:
        wav = _to_wav_bytes(env, bytes(raw), mimetype or "")
    except NovaAiVoiceError:
        raise
    except Exception:
        raise NovaAiVoiceError(env._(
            "That recording could not be decoded."))
    model = agent.model_id if agent else None
    if model and model.provider_id:
        try:
            text = model.provider_id.transcribe_via_provider(
                model.name, wav)
            return {"text": (text or "").strip(), "lang": "",
                    "duration": round(len(wav) / 32000, 1)}
        except Exception:
            # Provider heard nothing usable: fall through to local.
            pass
    try:
        from odoo.addons.nova_ai.models import nova_ai_voice_local as local
        text = local.transcribe_wav(env, wav)
        return {"text": text, "lang": "",
                "duration": round(len(wav) / 32000, 1)}
    except RuntimeError as err:
        raise NovaAiVoiceError(env._(
            "Transcription failed: %s") % str(err)[:160])
    except Exception:
        raise NovaAiVoiceError(env._(
            "Server transcription is unavailable: no audio endpoint "
            "and no local engine."))


def speak_text(env, text, agent=None):
    """Text in, (audio bytes, mime) out (truncated for sanity).

    Provider TTS first, local Piper second, browser voices last
    (the client reads aloud by itself on voice_unavailable).
    """
    plain = (text or "").strip()[:_SPEAK_MAX_CHARS]
    if not plain:
        raise NovaAiVoiceError(env._("Nothing to say."))
    if _server_strained():
        raise NovaAiVoiceError(env._(
            "The server is briefly busy — try again in a minute."))
    model = agent.model_id if agent else None
    if model and model.provider_id:
        try:
            wav, mime = model.provider_id.speak_via_provider(
                model.name, plain)
            if wav and (mime or "").startswith("audio/"):
                return wav, mime
        except Exception:
            # Provider silent: fall through to local.
            pass
    try:
        from odoo.addons.nova_ai.models import nova_ai_voice_local as local
        return local.speak_text(env, plain), "audio/wav"
    except RuntimeError as err:
        raise NovaAiVoiceError(env._(
            "Voice replies failed: %s") % str(err)[:160])
    except Exception:
        raise NovaAiVoiceError(env._(
            "Server speech is unavailable: no audio endpoint "
            "and no local engine."))
