# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Local ears and mouth: faster-whisper STT + Piper TTS, in-process.

No service, no port, no GPU, no browser plugin. Both engines live in
the venv already (faster_whisper, onnxruntime, piper) and lazy-load
once per worker process behind a lock — inference serializes per
worker, which is fine at chat scale and keeps memory bounded.

First-use downloads (network once, disk forever):
- STT model: faster-whisper fetches it into the HuggingFace cache
  itself (base.en is usually already there).
- TTS voice: one ~60MB .onnx + .json fetched here into Odoo's data
  dir (nova_ai/voices/). No installer, no admin step.

Missing pieces never crash: every entry point raises RuntimeError
with a plain reason, so callers fall through to the next fallback
(provider -> local -> browser). Kill switches:
nova_ai.local_stt = "0" disables STT (or names the model size,
default "base.en"); nova_ai.local_tts = "0" disables TTS;
nova_ai.local_tts_voice names the voice
(default "en_US-lessac-medium").
"""

import contextlib
import io
import threading
import wave

_STT_PARAM = "nova_ai.local_stt"
_STT_DEFAULT = "base.en"
_TTS_PARAM = "nova_ai.local_tts"
_VOICE_PARAM = "nova_ai.local_tts_voice"
_VOICE_DEFAULT = "en_US-lessac-medium"
_VOICE_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/"
              "main/%(lang)s/%(locale)s/%(name)s/%(quality)s/%(voice)s")


def _flag_on(value, default=True):
    """Config truthiness that survives Boolean fields ("True"/"False"
    strings), "1"/"0", and plain words alike."""
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return bool(default)
    return text not in ("0", "false", "no", "off", "none")


def voice_url(voice):
    """Download URL + config for a voice id like en_US-lessac-medium.
    Raises RuntimeError on malformed ids so setup refuses early."""
    parts = (voice or "").strip().split("-")
    if len(parts) != 3 or "_" not in parts[0]:
        raise RuntimeError("Unknown voice: %s." % (voice or "—"))
    locale, name, quality = parts
    lang = locale.split("_")[0]
    if not all((lang, locale, name, quality)):
        raise RuntimeError("Unknown voice: %s." % voice)
    return _VOICE_URL % {"lang": lang, "locale": locale, "name": name,
                         "quality": quality, "voice": voice}

_lock = threading.RLock()
_stt_models = {}
_tts_voices = {}


@contextlib.contextmanager
def _single_flight():
    """One voice job at a time per worker: a second concurrent
    request gets an honest busy answer instead of piling another
    200MB model load or a minute-long queue onto the worker. This
    is the no-crash guarantee — voice can be slow, never fatal."""
    if not _lock.acquire(blocking=False):
        raise RuntimeError("Voice engine is busy — try again "
                           "in a moment.")
    try:
        yield
    finally:
        _lock.release()


def _param(env, key, default=""):
    try:
        return env["ir.config_parameter"].get_str(key) or default
    except Exception:
        return default

def stt_enabled(env):
    """STT allowed and the engine importable."""
    if not _flag_on(_param(env, _STT_PARAM, _STT_DEFAULT), True):
        return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def tts_enabled(env):
    """TTS allowed and the engine importable."""
    if not _flag_on(_param(env, _TTS_PARAM, "1"), True):
        return False
    try:
        from piper import PiperVoice  # noqa: F401
        return True
    except ImportError:
        return False


def _stt_model(size):
    """Cached WhisperModel (CPU, int8): one per size per worker."""
    from faster_whisper import WhisperModel
    import multiprocessing
    key = size or _STT_DEFAULT
    model = _stt_models.get(key)
    if model is None:
        threads = 0
        try:
            threads = max(1, (multiprocessing.cpu_count() or 2) // 2)
        except Exception:
            threads = 0
        kwargs = {"device": "cpu", "compute_type": "int8"}
        if threads:
            kwargs["cpu_threads"] = threads
        model = WhisperModel(key, **kwargs)
        _stt_models[key] = model
    return model


def transcribe_wav(env, wav_bytes, lang="en"):
    """16kHz mono WAV bytes -> transcript string. Raises RuntimeError
    when disabled, uninstalled, or unusable (caller falls through)."""
    size = _param(env, _STT_PARAM, _STT_DEFAULT).strip() or _STT_DEFAULT
    if not _flag_on(size, True):
        raise RuntimeError("Local transcription is off.")
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("Local transcription needs numpy.")
    try:
        with wave.open(io.BytesIO(bytes(wav_bytes)), "rb") as wav:
            frames = wav.readframes(wav.getnframes())
    except Exception:
        raise RuntimeError("Local transcription needs WAV audio.")
    pcm = None
    try:
        pcm = (np.frombuffer(frames, dtype=np.int16).astype(
            "float32") / 32768.0)
    except Exception:
        raise RuntimeError("Local transcription needs 16-bit audio.")
    try:
        with _single_flight():
            segments, _info = _stt_model(size).transcribe(
                pcm, language=lang or "en" or None,
                beam_size=1, vad_filter=True)
            text = "".join(
                getattr(segment, "text", "") for segment in segments)
    except RuntimeError:
        raise
    except Exception as err:
        raise RuntimeError("Local transcription failed: %s"
                           % str(err)[:160])
    return (text or "").strip()


def _voice_paths(env, voice):
    """Local (onnx, config) paths for a voice, downloading once."""
    from odoo.tools import config
    import os
    name = (voice or _VOICE_DEFAULT).strip() or _VOICE_DEFAULT
    base = os.path.join(config["data_dir"], "nova_ai", "voices")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        raise RuntimeError("Local speech cannot use the data dir.")
    onnx = os.path.join(base, name + ".onnx")
    conf = onnx + ".json"
    if os.path.isfile(onnx) and os.path.isfile(conf):
        return onnx
    base_url = voice_url(name)
    import urllib.request
    for remote, local in (
            (base_url + ".onnx", onnx),
            (base_url + ".onnx.json", conf)):
        try:
            request = urllib.request.Request(
                remote, headers={"User-Agent": "NovaAI/1.0"})
            with urllib.request.urlopen(request, timeout=120) as resp:
                data = resp.read()
            if len(data) < 1024:
                raise RuntimeError("Voice file too small.")
            with open(local, "wb") as handle:
                handle.write(data)
        except RuntimeError:
            raise
        except Exception as err:
            raise RuntimeError("Voice download failed: %s"
                               % str(err)[:120])
    return onnx


def _tts_voice(env):
    """Cached PiperVoice for the configured voice (downloads once)."""
    from piper import PiperVoice
    name = _param(env, _VOICE_PARAM, _VOICE_DEFAULT).strip()
    if not name:
        name = _VOICE_DEFAULT
    voice = _tts_voices.get(name)
    if voice is None:
        voice = PiperVoice.load(_voice_paths(env, name))
        _tts_voices[name] = voice
    return voice


def speak_text(env, text):
    """Plain text -> (wav bytes, rate). Raises RuntimeError when
    disabled, uninstalled, or unusable (caller falls through)."""
    if not _flag_on(_param(env, _TTS_PARAM, "1"), True):
        raise RuntimeError("Local speech is off.")
    plain = (text or "").strip()
    if not plain:
        raise RuntimeError("Nothing to say.")
    try:
        with _single_flight():
            voice = _tts_voice(env)
            pcm = b"".join(
                bytes(getattr(chunk, "audio_int16_bytes", b""))
                for chunk in voice.synthesize(plain[:600]))
            try:
                rate = int(getattr(
                    getattr(voice, "config", None),
                    "sample_rate", 22050))
            except Exception:
                rate = 22050
            out = io.BytesIO()
            with wave.open(out, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate or 22050)
                wav.writeframes(pcm)
            data = out.getvalue()
    except RuntimeError:
        raise
    except Exception as err:
        raise RuntimeError("Local speech failed: %s" % str(err)[:160])
    if len(data) < 1000:
        raise RuntimeError("Local speech produced no audio.")
    return data


def prepare_voice(env):
    """One-click readiness: download the TTS voice, load the STT
    model into this worker, and speak one word to prove the chain.
    Returns {"stt": {...}, "tts": {...}} with ok/detail each — the
    provider button shows it, so setup is a click, not a README.
    Slow on first use (downloads + model load); instant after."""
    report = {"stt": {"ok": False, "detail": ""},
              "tts": {"ok": False, "detail": ""}}
    if stt_enabled(env):
        size = _param(env, _STT_PARAM, _STT_DEFAULT).strip() \
            or _STT_DEFAULT
        try:
            with _single_flight():
                _stt_model(size)
            report["stt"] = {"ok": True, "detail": size}
        except RuntimeError as err:
            report["stt"]["detail"] = str(err)[:120]
        except Exception as err:
            report["stt"]["detail"] = "load failed: %s" % str(err)[:120]
    else:
        try:
            import faster_whisper  # noqa: F401
            report["stt"]["detail"] = "switched off"
        except ImportError:
            report["stt"]["detail"] = "needs faster-whisper"
    if tts_enabled(env):
        try:
            data = speak_text(env, "Hello.")
            report["tts"] = {
                "ok": True,
                "detail": "%s (%d bytes spoken)" % (
                    _param(env, _VOICE_PARAM,
                           _VOICE_DEFAULT).strip() or _VOICE_DEFAULT,
                    len(data))}
        except Exception as err:
            report["tts"]["detail"] = str(err)[:160]
    else:
        try:
            from piper import PiperVoice  # noqa: F401
            report["tts"]["detail"] = "switched off"
        except ImportError:
            report["tts"]["detail"] = "needs piper-tts"
    return report
