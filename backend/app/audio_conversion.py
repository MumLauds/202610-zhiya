"""Bounded browser-audio decoding; no filenames, network inputs or audio temp files."""

import asyncio
import io
import wave

from app.errors import ServiceError

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_AUDIO_SECONDS = 120
MAX_PCM_BYTES = MAX_AUDIO_SECONDS * 16000 * 2
CONVERSION_TIMEOUT = 10
FORMATS = {"audio/webm": "matroska", "audio/ogg": "ogg", "audio/mp4": "mov",
           "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3"}


def pcm_wav(pcm: bytes) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(pcm)
    return target.getvalue()


async def normalize_recording(audio: bytes, media_type: str) -> bytes:
    media_type = media_type.split(";", 1)[0].strip().lower()
    if media_type not in FORMATS:
        raise ServiceError("AUDIO_UNSUPPORTED_FORMAT", "录音格式不支持", 415)
    if not isinstance(audio, bytes) or not audio:
        raise ServiceError("AUDIO_INVALID", "录音为空或无法解码", 422)
    if len(audio) > MAX_UPLOAD_BYTES:
        raise ServiceError("AUDIO_TOO_LARGE", "录音超过大小限制", 413)
    if FORMATS[media_type] == "wav":
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ServiceError("AUDIO_INVALID", "录音无法解码", 422)
        try:
            with wave.open(io.BytesIO(audio), "rb") as source:
                if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) == (16000, 1, 2, "NONE"):
                    frames = source.getnframes()
                    if frames > MAX_AUDIO_SECONDS * 16000:
                        raise ServiceError("AUDIO_TOO_LONG", "录音不能超过两分钟", 413)
                    pcm = source.readframes(frames)
                    if not pcm or len(pcm) != frames * 2:
                        raise ServiceError("AUDIO_INVALID", "录音不完整", 422)
                    return pcm_wav(pcm)
        except (wave.Error, EOFError, RuntimeError):
            # wave only understands PCM; other WAV codecs need the bounded decoder.
            pass

    process = None
    tasks = []
    try:
        async with asyncio.timeout(CONVERSION_TIMEOUT):
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                "-threads", "1", "-protocol_whitelist", "pipe", "-f", FORMATS[media_type],
                "-i", "pipe:0", "-map", "0:a:0", "-vn", "-sn", "-dn",
                "-map_metadata", "-1", "-ac", "1", "-ar", "16000", "-threads", "1",
                "-t", str(MAX_AUDIO_SECONDS + 1), "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            async def send():
                try:
                    process.stdin.write(audio)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()

            async def receive():
                result = bytearray()
                while chunk := await process.stdout.read(32768):
                    if len(result) + len(chunk) > MAX_PCM_BYTES:
                        raise ServiceError("AUDIO_TOO_LONG", "录音不能超过两分钟", 413)
                    result.extend(chunk)
                return bytes(result)

            tasks = [asyncio.create_task(send()), asyncio.create_task(receive()), asyncio.create_task(process.wait())]
            _, pcm, returncode = await asyncio.gather(*tasks)
            if returncode or not pcm or len(pcm) % 2:
                raise ServiceError("AUDIO_INVALID", "录音为空或无法完整解码", 422)
            return pcm_wav(pcm)
    except TimeoutError:
        raise ServiceError("AUDIO_CONVERSION_TIMEOUT", "录音转换超时，请缩短录音", 504) from None
    except FileNotFoundError:
        raise ServiceError("AUDIO_CONVERTER_UNAVAILABLE", "录音转换组件尚未安装", 503) from None
    except OSError:
        raise ServiceError("AUDIO_CONVERTER_UNAVAILABLE", "录音转换组件暂不可用", 503) from None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
