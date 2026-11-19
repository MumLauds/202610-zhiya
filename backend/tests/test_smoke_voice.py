import asyncio
import io
import json
import wave
from unittest.mock import patch

import smoke_voice
from app.voice_gateway import Transcription


def wav_sample():
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 160)
    return target.getvalue()


def test_cli_convert_is_offline_and_preserves_source(tmp_path):
    source, target = tmp_path / "input.wav", tmp_path / "converted.wav"
    source.write_bytes(wav_sample())
    with patch("smoke_voice.load_dotenv") as env, patch("smoke_voice.VoiceGateway") as gateway:
        assert asyncio.run(smoke_voice.main(["convert", str(source), str(target), "--format", "wav"])) == 0
        assert target.read_bytes() == source.read_bytes() == wav_sample()
        assert asyncio.run(smoke_voice.main(["convert", str(source), str(target), "--format", "wav"])) == 1
        env.assert_not_called()
        gateway.assert_not_called()


def test_cli_saves_new_audio_and_never_overwrites(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke_voice, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setattr("builtins.input", lambda _: "成人模拟测试")
    path = tmp_path / "answer.wav"
    with patch("smoke_voice.VoiceGateway", autospec=True) as factory:
        gateway = factory.return_value
        gateway.__aenter__.return_value = gateway
        gateway.synthesize.return_value = wav_sample()
        assert asyncio.run(smoke_voice.main(["tts", str(path)])) == 0
        assert path.read_bytes() == wav_sample()
        saved = json.loads(capsys.readouterr().out)["data"]
        assert saved["outputPath"] == str(path.resolve())
        assert asyncio.run(smoke_voice.main(["tts", str(path)])) == 1
        gateway.synthesize.assert_awaited_once()


def test_cli_transcribes_without_changing_source(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke_voice, "load_dotenv", lambda *a, **kw: None)
    path = tmp_path / "adult.wav"
    path.write_bytes(wav_sample())
    with patch("smoke_voice.VoiceGateway", autospec=True) as factory:
        gateway = factory.return_value
        gateway.__aenter__.return_value = gateway
        gateway.transcribe.return_value = Transcription("模拟转写")
        assert asyncio.run(smoke_voice.main(["asr", str(path), "--format", "wav"])) == 0
        gateway.transcribe.assert_awaited_once_with(audio=wav_sample(), media_type="audio/wav")
    assert path.read_bytes() == wav_sample()
    assert '"reliable": false' in capsys.readouterr().out


def test_cli_missing_output_directory_never_calls_provider(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke_voice, "load_dotenv", lambda *a, **kw: None)
    output = tmp_path / "missing" / "slow.wav"
    with patch("builtins.input") as read_input, patch("smoke_voice.VoiceGateway") as gateway:
        assert asyncio.run(smoke_voice.main(["tts", str(output)])) == 1
        gateway.assert_not_called()
        read_input.assert_not_called()
    assert "OUTPUT_DIRECTORY_INVALID" in capsys.readouterr().err
    assert not output.parent.exists()


def test_cli_missing_key_has_no_output_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke_voice, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setattr("builtins.input", lambda _: "测试")
    path = tmp_path / "answer.wav"
    assert asyncio.run(smoke_voice.main(["tts", str(path)])) == 1
    assert not path.exists()
    assert "TTS_NOT_CONFIGURED" in capsys.readouterr().err


def test_cli_lists_options_without_credentials_or_client(monkeypatch, capsys):
    with patch("smoke_voice.load_dotenv") as dotenv, patch("smoke_voice.VoiceGateway") as gateway:
        monkeypatch.setenv("VOICE_TIMEOUT_SECONDS", "invalid")
        assert asyncio.run(smoke_voice.main(["options"])) == 0
        dotenv.assert_not_called()
        gateway.assert_not_called()
    data = json.loads(capsys.readouterr().out)["data"]
    assert len(data["voices"]) == 6
    assert "longniuniu_v3.1" in {item["voiceId"] for item in data["voices"]}
    assert all("languages" in item and "scenarios" in item for item in data["voices"])
    assert data["speechRates"] == ["SLOW", "NORMAL", "FAST"]


def test_cli_selected_voice_is_passed_to_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke_voice, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setattr("builtins.input", lambda _: "固定试听文本")
    with patch("smoke_voice.VoiceGateway", autospec=True) as factory:
        gateway = factory.return_value
        gateway.__aenter__.return_value = gateway
        gateway.synthesize.return_value = wav_sample()
        output = tmp_path / "serena.wav"
        assert asyncio.run(smoke_voice.main(["tts", str(output), "--voice", "longniuniu_v3.1", "--speech-rate", "SLOW"])) == 0
        gateway.synthesize.assert_awaited_once_with(text="固定试听文本", voice_id="longniuniu_v3.1", speech_rate="SLOW")
        assert output.read_bytes() == wav_sample()
