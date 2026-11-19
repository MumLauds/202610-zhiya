import asyncio
import io
import json
import struct
import unittest
import wave
from dataclasses import replace
from unittest.mock import patch

import httpx

from app.errors import ServiceError
from app.config import Settings
from app.model_gateway import ModelGateway
from app.speech_config import SpeechSettings
from app.speech_options import get_speech_options
from app.voice_gateway import AUDIO_HOST, MAX_AUDIO_BYTES, VoiceGateway

TTS_URL = "https://ws-test.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
SETTINGS = SpeechSettings(api_key="private-key", tts_api_url=TTS_URL)
URL = f"http://{AUDIO_HOST}/audio.wav?Signature=private-signature"


def wav(rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0" * 160)
    return buffer.getvalue()


def asr(text="  你好  "):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


def tts(url=URL):
    return {"output": {"finish_reason": "stop", "audio": {"url": url}}}


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_selected_voice_works_with_all_rates_in_protocol(self):
        payloads = []
        def handler(request):
            if request.method == "POST":
                payloads.append(json.loads(request.content))
                return httpx.Response(200, json=tts())
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=wav(24000))
        async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
            for option in get_speech_options()["voices"]:
                voice_id = option["voiceId"]
                self.assertEqual(replace(SETTINGS, tts_voice=voice_id).tts_voice, voice_id)
                for rate, value in (("SLOW", 0.9), ("NORMAL", 1.0), ("FAST", 1.2)):
                    with self.subTest(voice=voice_id, rate=rate):
                        self.assertEqual(await gateway.synthesize(text="Test", voice_id=voice_id, speech_rate=rate), wav(24000))
                        self.assertEqual(payloads[-1], {"model": "qwen-audio-3.1-tts-flash", "input": {
                            "text": "Test", "voice": voice_id, "format": "wav", "sample_rate": 24000, "rate": value}})
        self.assertEqual(len(payloads), 6 * 3)

    async def test_per_call_voices_and_model_rates_are_isolated(self):
        payloads = []
        ready = asyncio.Event()
        async def handler(request):
            if request.method == "POST":
                payload = json.loads(request.content)
                payloads.append(payload)
                if len(payloads) == 3:
                    ready.set()
                await asyncio.wait_for(ready.wait(), timeout=1)
                self.assertEqual(set(payload), {"model", "input"})
                self.assertEqual(set(payload["input"]), {"text", "voice", "format", "sample_rate", "rate"})
                return httpx.Response(200, json=tts())
            return httpx.Response(200, content=wav(24000))
        settings = replace(SETTINGS, tts_voice="longniuniu_v3.1")
        async with VoiceGateway(settings, transport=httpx.MockTransport(handler)) as gateway:
            results = await asyncio.gather(*(
                gateway.synthesize(text=voice, voice_id=voice, speech_rate=rate)
                for voice, rate in (("wenhuaiqing_v3.1", "SLOW"), ("yezhiqing_v3.1", "NORMAL"), ("longniuniu_v3.1", "FAST"))
            ))
            self.assertEqual(results, [wav(24000)] * 3)
            self.assertEqual({p["input"]["text"]: p["input"]["voice"] for p in payloads},
                             {"wenhuaiqing_v3.1": "wenhuaiqing_v3.1", "yezhiqing_v3.1": "yezhiqing_v3.1", "longniuniu_v3.1": "longniuniu_v3.1"})
            self.assertEqual({p["input"]["voice"]: p["input"]["rate"] for p in payloads},
                             {"wenhuaiqing_v3.1": 0.9, "yezhiqing_v3.1": 1.0, "longniuniu_v3.1": 1.2})
            await gateway.synthesize(text="默认声线")
        self.assertEqual(payloads[-1]["input"]["voice"], "longniuniu_v3.1")
        self.assertEqual(payloads[-1]["input"]["rate"], 1.0)
        self.assertEqual(settings.tts_voice, "longniuniu_v3.1")

    async def test_invalid_voice_never_calls_provider_and_override_cannot_enable_disabled_tts(self):
        def unexpected(request):
            self.fail("Invalid voice or disabled TTS must not call the provider")
        async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(unexpected)) as gateway:
            for voice in ("", "Cherry", "cherry", " yeqinghe_v3.1 ", "yeqinghe_v3.1", "anmingyuan_v3.1", "Emily_v3.1", "unknown", 1, True, [], {}):
                with self.subTest(voice=voice), self.assertRaises(ServiceError) as caught:
                    await gateway.synthesize(text="测试", voice_id=voice)
                self.assertEqual(caught.exception.code, "TTS_INVALID_VOICE")
                self.assertEqual(caught.exception.status_code, 422)
        async with VoiceGateway(replace(SETTINGS, tts_voice=""), transport=httpx.MockTransport(unexpected)) as gateway:
            with self.assertRaises(ServiceError) as caught:
                await gateway.synthesize(text="测试", voice_id="yezhiqing_v3.1")
            self.assertEqual(caught.exception.code, "TTS_NOT_CONFIGURED")

    async def test_invalid_rate_never_calls_provider(self):
        def unexpected(request):
            self.fail("Invalid or unsupported rate must not reach the provider")
        async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(unexpected)) as gateway:
            for rate in (None, "slow", " SLOW ", 0.8, 1.0, True, [], {}):
                with self.subTest(rate=rate), self.assertRaises(ServiceError) as caught:
                    await gateway.synthesize(text="测试", speech_rate=rate)
                self.assertEqual(caught.exception.code, "TTS_INVALID_RATE")

    async def test_synthesis_rejects_wav_outside_requested_format(self):
        for sample_rate, channels, width in ((16000, 1, 2), (24000, 2, 2), (24000, 1, 1)):
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as output:
                output.setparams((channels, width, sample_rate, 0, "NONE", "not compressed"))
                output.writeframes(b"\0" * (160 * channels * width))
            calls = []

            def handler(request):
                calls.append(request)
                return httpx.Response(200, json=tts()) if request.method == "POST" else httpx.Response(200, content=buffer.getvalue())

            with self.subTest(rate=sample_rate, channels=channels, width=width):
                async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
                    with self.assertRaises(ServiceError) as caught:
                        await gateway.synthesize(text="测试")
                self.assertEqual(caught.exception.code, "TTS_INVALID_RESPONSE")
                self.assertEqual(len(calls), 2)

    async def test_bailian_placeholder_wav_is_normalized_without_changing_pcm(self):
        expected = wav(24000)
        audio = bytearray(expected)
        audio[4:8] = struct.pack("<I", 0x7FFFFFBF)
        audio[40:44] = struct.pack("<I", 0x7FFFFF9B)
        def handler(request):
            return httpx.Response(200, json=tts()) if request.method == "POST" else httpx.Response(200, content=bytes(audio))
        async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
            result = await gateway.synthesize(text="测试")
        self.assertEqual(result, expected)
        with wave.open(io.BytesIO(result), "rb") as source:
            self.assertEqual(source.getnframes(), 160)
            self.assertEqual(source.getframerate(), 24000)

    async def test_placeholder_repair_rejects_other_damage_and_asr_input(self):
        original = bytearray(wav(24000))
        original[4:8] = struct.pack("<I", 0x7FFFFFBF)
        original[40:44] = struct.pack("<I", 0x7FFFFF9B)
        unknown_size = bytearray(original)
        unknown_size[40:44] = struct.pack("<I", 100000)
        wrong_format = bytearray(original)
        wrong_format[20:22] = struct.pack("<H", 3)
        for audio in (original[:-1], original[:44], unknown_size, wrong_format):
            with self.subTest(length=len(audio), header=bytes(audio[:44]).hex()):
                def handler(request):
                    return httpx.Response(200, json=tts()) if request.method == "POST" else httpx.Response(200, content=bytes(audio))
                async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
                    with self.assertRaises(ServiceError) as caught:
                        await gateway.synthesize(text="测试")
                    self.assertEqual(caught.exception.code, "TTS_INVALID_RESPONSE")
        # Caller uploads remain strictly validated; no supplier repair is applied.
        upload = bytearray(wav())
        upload[4:8] = original[4:8]
        upload[40:44] = original[40:44]
        def unexpected_request(request):
            self.fail("Invalid upload must not reach the provider")
        async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(unexpected_request)) as gateway:
            with self.assertRaises(ServiceError) as caught:
                await gateway.transcribe(audio=bytes(upload), media_type="audio/wav")
            self.assertEqual(caught.exception.code, "ASR_INVALID_INPUT")

    async def test_mixed_providers_use_separate_credentials_and_protocols(self):
        with patch.dict("os.environ", {"MODEL_API_KEY": "deepseek-only", "DASHSCOPE_API_KEY": "speech-only", "TTS_API_URL": TTS_URL}, clear=True):
            model_settings, speech_settings = Settings.from_env(), SpeechSettings.from_env()
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == "GET":
                self.assertEqual(request.url.host, AUDIO_HOST)
                self.assertNotIn("authorization", request.headers)
                return httpx.Response(200, content=wav(24000))
            payload = json.loads(request.content)
            if request.url.host == "api.deepseek.com":
                self.assertEqual(request.headers["authorization"], "Bearer deepseek-only")
                self.assertEqual(payload["model"], "deepseek-flash")
                self.assertEqual(payload["thinking"], {"type": "disabled"})
                self.assertNotIn("enable_thinking", payload)
                return httpx.Response(200, json=asr("模型回答"))
            self.assertIn(request.url.host, ("dashscope.aliyuncs.com", "ws-test.cn-beijing.maas.aliyuncs.com"))
            self.assertEqual(request.headers["authorization"], "Bearer speech-only")
            self.assertNotIn("thinking", payload)
            if "messages" in payload:
                self.assertEqual(payload["model"], "qwen3-asr-flash")
                return httpx.Response(200, json=asr())
            self.assertEqual(payload["model"], "qwen-audio-3.1-tts-flash")
            return httpx.Response(200, json=tts())
        async with ModelGateway(model_settings, transport=httpx.MockTransport(handler)) as model, VoiceGateway(speech_settings, transport=httpx.MockTransport(handler)) as voice:
            self.assertEqual(await model.generate(input_text="测试", system_prompt="简短回答"), "模型回答")
            self.assertEqual((await voice.transcribe(audio=wav(), media_type="audio/wav")).text, "你好")
            self.assertEqual(await voice.synthesize(text="测试"), wav(24000))
        self.assertEqual(len(calls), 4)

    async def test_speech_key_alone_does_not_send_text_request(self):
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "speech-only"}, clear=True):
            settings = Settings.from_env()
        def handler(request):
            self.fail("Speech credential must not be sent to the text service")
        async with ModelGateway(settings, transport=httpx.MockTransport(handler)) as gateway:
            with self.assertRaises(ServiceError) as caught:
                await gateway.generate(input_text="测试", system_prompt="简短回答")
            self.assertEqual(caught.exception.code, "MODEL_NOT_CONFIGURED")

    async def test_malformed_wav_chunk_returns_safe_errors(self):
        audio = b"RIFF" + struct.pack("<I", 36) + b"WAVEJUNK" + struct.pack("<I", 100) + b"1234"
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=tts()) if request.method == "POST" else httpx.Response(200, content=audio)
        async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
            with self.assertRaises(ServiceError) as caught:
                await gateway.transcribe(audio=audio, media_type="audio/wav")
            self.assertEqual(caught.exception.code, "ASR_INVALID_INPUT")
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(requests, [])
            with self.assertRaises(ServiceError) as caught:
                await gateway.synthesize(text="你好")
            self.assertEqual(caught.exception.code, "TTS_INVALID_RESPONSE")
            self.assertEqual(len(requests), 2)

    async def test_non_transcription_messages_are_rejected(self):
        for fields in ({"role": "user"}, {"role": None}, {"refusal": "refused"}, {"tool_calls": [{"id": "call"}]}):
            payload = asr()
            payload["choices"][0]["message"].update(fields)
            with self.subTest(fields=fields):
                async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as gateway:
                    with self.assertRaises(ServiceError) as caught:
                        await gateway.transcribe(audio=wav(), media_type="audio/wav")
                    self.assertEqual(caught.exception.code, "ASR_RECOGNITION_FAILED")

    async def test_success_payload_and_download_has_no_credentials(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.method == "GET":
                self.assertEqual(request.url.scheme, "https")
                self.assertNotIn("authorization", request.headers)
                return httpx.Response(200, content=wav(24000))
            payload = json.loads(request.content)
            if "messages" in payload:
                self.assertEqual(payload["asr_options"], {"enable_itn": False})
                self.assertTrue(payload["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/wav;base64,"))
                return httpx.Response(200, json=asr())
            self.assertEqual(payload["input"], {"text": "你好", "voice": "wenhuaiqing_v3.1", "format": "wav", "sample_rate": 24000, "rate": 1.0})
            return httpx.Response(200, json=tts())
        async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
            with self.assertLogs("zhiya.voice", level="INFO") as logs:
                result = await gateway.transcribe(audio=wav(), media_type="audio/wav")
                self.assertEqual(result.text, "你好")
                self.assertFalse(result.reliable)
                self.assertEqual(await gateway.synthesize(text=" 你好 "), wav(24000))
        self.assertEqual(len(requests), 3)
        for secret in ("private-key", "private-signature", "你好", "RIFF"):
            self.assertNotIn(secret, "".join(logs.output))

    async def test_unconfigured_and_invalid_inputs_do_not_request(self):
        def handler(request):
            self.fail("unexpected network")
        async with VoiceGateway(SpeechSettings(), transport=httpx.MockTransport(handler)) as gateway:
            for method in (gateway.synthesize(text="你好"), gateway.transcribe(audio=wav(), media_type="audio/wav")):
                with self.assertRaises(ServiceError) as caught:
                    await method
                self.assertTrue(caught.exception.code.endswith("NOT_CONFIGURED"))
            for data, media in ((b"invalid", "audio/wav"), (wav(), "audio/webm"), (b"x" * (MAX_AUDIO_BYTES + 1), "audio/mpeg")):
                with self.assertRaises(ServiceError):
                    await gateway.transcribe(audio=data, media_type=media)
            for text in ("", "x" * 601, "\ud800", None):
                with self.assertRaises(ServiceError):
                    await gateway.synthesize(text=text)
        for field in ("asr_api_url", "asr_model", "tts_api_url", "tts_model", "tts_voice"):
            async with VoiceGateway(replace(SETTINGS, **{field: ""}), transport=httpx.MockTransport(handler)) as gateway:
                with self.assertRaises(ServiceError) as caught:
                    if field.startswith("asr"):
                        await gateway.transcribe(audio=wav(), media_type="audio/wav")
                    else:
                        await gateway.synthesize(text="你好")
                self.assertTrue(caught.exception.code.endswith("NOT_CONFIGURED"))

    async def test_status_mapping_no_retry(self):
        for status, suffix in ((401, "AUTH_FAILED"), (403, "AUTH_FAILED"), (429, "RATE_LIMITED"), (503, "UNAVAILABLE"), (302, "PROVIDER_ERROR"), (400, "PROVIDER_ERROR")):
            for kind in ("ASR", "TTS"):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(status, text="secret-provider-body")
                async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
                    with self.assertRaises(ServiceError) as caught:
                        if kind == "ASR":
                            await gateway.transcribe(audio=wav(), media_type="audio/wav")
                        else:
                            await gateway.synthesize(text="你好")
                self.assertEqual(caught.exception.code, f"{kind}_{suffix}")
                self.assertEqual(len(calls), 1)
                self.assertNotIn("secret-provider-body", str(caught.exception))

    async def test_invalid_recognition(self):
        for payload in ({}, [], 1, None, asr(""), asr("x" * 201), asr("\ud800"), {"choices": []}):
            async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(payload).encode()))) as gateway:
                with self.assertRaises(ServiceError) as caught:
                    await gateway.transcribe(audio=wav(), media_type="audio/wav")
                self.assertEqual(caught.exception.code, "ASR_RECOGNITION_FAILED")
                self.assertEqual(caught.exception.status_code, 422)

    async def test_untrusted_urls_never_download(self):
        urls = ["https://evil.test/x?q=x", f"https://user@{AUDIO_HOST}/x?q=x", f"https://@{AUDIO_HOST}/x?q=x", f"https://{AUDIO_HOST}:444/x?q=x", f"https://{AUDIO_HOST}/x?q=x#fragment", f"https://{AUDIO_HOST}/x?q=x#", f"https://{AUDIO_HOST}/x?q=\x7f", f"https://{AUDIO_HOST}/x?q=\ud800", f"https://{AUDIO_HOST}/x", f"https://{AUDIO_HOST}.evil.test/x?q=x", "file:///tmp/audio.wav", None]
        for url in urls:
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(200, content=json.dumps(tts(url)).encode())
            async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
                with self.assertRaises(ServiceError):
                    await gateway.synthesize(text="你好")
            self.assertEqual(len(calls), 1)

    async def test_bounded_and_uncompressed_responses(self):
        for download in (False, True):
            for compressed in (False, True):
                def handler(request):
                    if download and request.method == "POST":
                        return httpx.Response(200, json=tts())
                    return httpx.Response(200, content=b"x" * (1024 * 1024 + 1), headers={"Content-Encoding": "br"} if compressed else {})
                with patch("app.voice_gateway.MAX_DOWNLOAD_BYTES", 10):
                    async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
                        with self.assertRaises(ServiceError):
                            await gateway.synthesize(text="你好")

    async def test_timeout_and_cancel_cover_download(self):
        for cancel in (False, True):
            started, closed = asyncio.Event(), asyncio.Event()
            async def handler(request):
                if request.method == "POST":
                    return httpx.Response(200, json=tts())
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    closed.set()
            settings = replace(SETTINGS, timeout_seconds=0.03)
            async with VoiceGateway(settings, transport=httpx.MockTransport(handler)) as gateway:
                task = asyncio.create_task(gateway.synthesize(text="你好"))
                await started.wait()
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with self.assertRaises(ServiceError) as caught:
                        await task
                    self.assertEqual(caught.exception.code, "TTS_TIMEOUT")
                    self.assertEqual(caught.exception.status_code, 504)
                self.assertTrue(closed.is_set())

    async def test_bad_tts_output_and_audio(self):
        for payload in ({}, {**tts(), "code": "secret-error"}, {**tts(), "status_code": 400}):
            async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as gateway:
                with self.assertRaises(ServiceError) as caught:
                    await gateway.synthesize(text="你好")
                self.assertEqual(caught.exception.code, "TTS_INVALID_RESPONSE")
        for audio in (b"RIFF1234WAVE", wav()[:-2], b"not-wav"):
            def handler(request):
                return httpx.Response(200, json=tts()) if request.method == "POST" else httpx.Response(200, content=audio)
            async with VoiceGateway(SETTINGS, transport=httpx.MockTransport(handler)) as gateway:
                with self.assertRaises(ServiceError):
                    await gateway.synthesize(text="你好")

    async def test_download_stream_closed_on_cancel_and_deadline(self):
        for cancel in (False, True):
            started, closed = asyncio.Event(), asyncio.Event()
            class SlowAudio(httpx.AsyncByteStream):
                async def __aiter__(self):
                    started.set()
                    await asyncio.Future()
                    yield b""

                async def aclose(self):
                    closed.set()

            def handler(request):
                return httpx.Response(200, json=tts()) if request.method == "POST" else httpx.Response(200, stream=SlowAudio())
            async with VoiceGateway(replace(SETTINGS, timeout_seconds=0.03), transport=httpx.MockTransport(handler)) as gateway:
                task = asyncio.create_task(gateway.synthesize(text="你好"))
                await started.wait()
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with self.assertRaises(ServiceError):
                        await task
                self.assertTrue(closed.is_set())
