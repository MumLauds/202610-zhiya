"""Explicit local speech checks with adult/synthetic samples; never a public API."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from app.errors import ServiceError
from app.speech_config import SpeechSettings
from app.speech_options import DEFAULT_TTS_MODEL, MODEL_VOICES, SPEECH_RATES, VOICE_IDS, get_speech_options
from app.voice_gateway import VoiceGateway
from app.audio_conversion import MAX_UPLOAD_BYTES, normalize_recording

AUDIO_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "webm": "audio/webm",
               "ogg": "audio/ogg", "mp4": "audio/mp4"}


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    options = commands.add_parser("options", help="List model capabilities offline; does not read .env")
    options.add_argument("--model", choices=MODEL_VOICES, default=DEFAULT_TTS_MODEL)
    asr = commands.add_parser("asr", help="Transcribe a local adult/synthetic WAV or MP3 sample")
    asr.add_argument("audio", type=Path)
    asr.add_argument("--format", choices=AUDIO_TYPES, required=True)
    convert = commands.add_parser("convert", help="Convert a local recording to 16 kHz PCM WAV offline; does not read .env")
    convert.add_argument("audio", type=Path)
    convert.add_argument("output", type=Path)
    convert.add_argument("--format", choices=AUDIO_TYPES, required=True)
    tts = commands.add_parser("tts", help="Read test text from stdin and write a new WAV file")
    tts.add_argument("output", type=Path)
    tts.add_argument("--voice", choices=VOICE_IDS, help="Override the server default for this call only")
    tts.add_argument("--speech-rate", choices=SPEECH_RATES, default="NORMAL", help="Model-generated pace, not an exact playback multiplier")
    args = parser.parse_args(argv)
    request_id = uuid4().hex
    if args.command == "options":
        print(json.dumps({"data": get_speech_options(args.model), "requestId": request_id}, ensure_ascii=False))
        return 0
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        if args.command in ("tts", "convert") and args.output.exists():
            raise ServiceError("OUTPUT_EXISTS", "输出文件已存在，请使用新文件名", 422)
        if args.command in ("tts", "convert") and not args.output.parent.is_dir():
            raise ServiceError("OUTPUT_DIRECTORY_INVALID", "输出目录不存在或不是文件夹，请先创建目录", 422)
        if args.command == "convert":
            with args.audio.open("rb") as source:
                recording = source.read(MAX_UPLOAD_BYTES + 1)
            audio = await normalize_recording(recording, AUDIO_TYPES[args.format])
            with args.output.open("xb") as destination:
                destination.write(audio)
            print(json.dumps({"data": {"status": "SAVED", "bytes": len(audio), "outputPath": str(args.output.resolve())},
                              "requestId": request_id}, ensure_ascii=False))
            return 0
        load_dotenv(Path(__file__).with_name(".env"), override=False)
        settings = SpeechSettings.from_env()
        async with VoiceGateway(settings) as gateway:
            if args.command == "asr":
                with args.audio.open("rb") as source:
                    # Read a sentinel byte so oversized samples fail without loading the full file.
                    audio = source.read(MAX_UPLOAD_BYTES + 1)
                audio = await normalize_recording(audio, AUDIO_TYPES[args.format])
                result = await gateway.transcribe(
                    audio=audio, media_type="audio/wav"
                )
                data = {"text": result.text, "reliable": result.reliable}
            else:
                text = input("输入不含个人信息的测试朗读文本（1～600 字）：\n")
                audio = await gateway.synthesize(text=text, voice_id=args.voice, speech_rate=args.speech_rate)
                # Exclusive creation also prevents a concurrent writer from being overwritten.
                with args.output.open("xb") as destination:
                    destination.write(audio)
                data = {"status": "SAVED", "bytes": len(audio), "outputPath": str(args.output.resolve())}
        print(json.dumps({"data": data, "requestId": request_id}, ensure_ascii=False))
        return 0
    except ServiceError as exc:
        print(json.dumps(exc.as_response(request_id), ensure_ascii=False), file=sys.stderr)
    except ValueError:
        print("语音环境变量格式不正确，请核对 .env.example。", file=sys.stderr)
    except (OSError, EOFError):
        print("无法读取测试输入或保存音频，请检查文件与输入。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
