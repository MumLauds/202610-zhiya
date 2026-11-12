import unittest
from dataclasses import replace
from unittest.mock import patch

from app.config import DASHSCOPE_CHAT_URL, Settings
from app.speech_config import SpeechSettings


class EnvironmentSettingsTests(unittest.TestCase):
    def test_tts_requires_workspace_endpoint_and_matching_model_voice(self):
        settings = SpeechSettings(api_key="key")
        self.assertTrue(settings.asr_configured)
        self.assertFalse(settings.tts_configured)
        with self.assertRaises(ValueError):
            SpeechSettings(tts_model="qwen3-tts-instruct-flash")
        with self.assertRaises(ValueError):
            SpeechSettings(tts_model="qwen3-tts-flash")
        with self.assertRaises(ValueError):
            SpeechSettings(tts_model="qwen3-tts-flash", tts_voice="Cherry")

    def test_default_voice_is_restricted_to_supported_choices_or_disabled(self):
        for voice in ("wenhuaiqing_v3.1", "yezhiqing_v3.1", "longniuniu_v3.1", "longshanshan_v3.1", "longjielidou_v3.1", "longpaopao_v3.1", ""):
            self.assertEqual(SpeechSettings(tts_voice=voice).tts_voice, voice)
        for voice in ("unknown", "Maia", "Cherry", "cherry", " yeqinghe_v3.1 ", "yeqinghe_v3.1", "anmingyuan_v3.1", "Emily_v3.1"):
            with self.subTest(voice=voice), self.assertRaises(ValueError) as caught:
                SpeechSettings(tts_voice=voice)
            self.assertNotIn(voice, str(caught.exception))

    def test_defaults_and_separate_provider_keys(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
            self.assertEqual(settings.model_api_url, "https://api.deepseek.com/chat/completions")
            self.assertEqual(settings.model_name, "deepseek-flash")
            self.assertEqual(SpeechSettings.from_env().asr_api_url, DASHSCOPE_CHAT_URL)
            self.assertFalse(settings.model_configured)
            self.assertFalse(SpeechSettings.from_env().asr_configured)
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "dash-key", "MODEL_API_KEY": "deepseek-key"}, clear=True):
            self.assertEqual(Settings.from_env().model_api_key, "deepseek-key")
            self.assertEqual(SpeechSettings.from_env().api_key, "dash-key")
        for text_key in ({}, {"MODEL_API_KEY": ""}):
            with self.subTest(text_key=text_key), patch.dict("os.environ", {"DASHSCOPE_API_KEY": "dash-key", **text_key}, clear=True):
                self.assertFalse(Settings.from_env().model_configured)
                self.assertEqual(Settings.from_env().model_api_key, "")
                self.assertTrue(SpeechSettings.from_env().asr_configured)
        with patch.dict("os.environ", {"MODEL_API_KEY": "deepseek-key"}, clear=True):
            self.assertTrue(Settings.from_env().model_configured)
            self.assertFalse(SpeechSettings.from_env().asr_configured)
            self.assertFalse(SpeechSettings.from_env().tts_configured)

    def test_explicit_empty_configuration(self):
        for name in ("MODEL_API_URL", "MODEL_NAME"):
            with self.subTest(name=name), patch.dict("os.environ", {"MODEL_API_KEY": "key", name: ""}, clear=True):
                self.assertFalse(Settings.from_env().model_configured)
        for name in ("ASR_API_URL", "ASR_MODEL", "TTS_API_URL", "TTS_MODEL", "TTS_VOICE"):
            with self.subTest(name=name), patch.dict("os.environ", {"DASHSCOPE_API_KEY": "key", "TTS_API_URL": "https://ws-test.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer", name: ""}, clear=True):
                settings = SpeechSettings.from_env()
                self.assertFalse(settings.asr_configured if name.startswith("ASR") else settings.tts_configured)

    def test_environment_urls_reject_whitespace_without_exposing_values(self):
        for name, cls in (("MODEL_API_URL", Settings), ("ASR_API_URL", SpeechSettings), ("TTS_API_URL", SpeechSettings)):
            for value in ("https://example.test/private\n", "\thttps://example.test/private", " https://example.test/private ", " "):
                with self.subTest(name=name, value=repr(value)), patch.dict("os.environ", {name: value}, clear=True):
                    with self.assertRaises(ValueError) as caught:
                        cls.from_env()
                    self.assertNotIn("example.test", str(caught.exception))

    def test_speech_defaults_overrides_and_secret_repr(self):
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "private-key", "TTS_VOICE": "yezhiqing_v3.1", "VOICE_TIMEOUT_SECONDS": "12.5", "TTS_API_URL": "https://ws-test.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"}, clear=True):
            settings = SpeechSettings.from_env()
        self.assertTrue(settings.asr_configured)
        self.assertTrue(settings.tts_configured)
        self.assertEqual(settings.tts_voice, "yezhiqing_v3.1")
        self.assertEqual(settings.timeout_seconds, 12.5)
        self.assertNotIn("private-key", repr(settings))

    def test_invalid_values_do_not_expose_secrets(self):
        for key in ("private key", "private\nkey", "private-密钥", "private\x7fkey"):
            for cls, field in ((Settings, "model_api_key"), (SpeechSettings, "api_key")):
                with self.subTest(key=repr(key), cls=cls):
                    with self.assertRaises(ValueError) as error:
                        cls(**{field: key})
                    self.assertNotIn(key, str(error.exception))
        for field in ("asr_api_url", "tts_api_url"):
            for url in ("http://host/path", "https://user:secret@host/path", "https://host/?secret", "https://host/#secret", "https://host/\nsecret", "https://host:99999/path", "https://🧪.example/path"):
                with self.subTest(field=field, url=url), self.assertRaises(ValueError) as error:
                    replace(SpeechSettings(), **{field: url})
                self.assertNotIn(url, str(error.exception))
        for field in ("asr_model", "tts_model", "tts_voice"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(SpeechSettings(), **{field: "bad-\ud800"})
        for timeout in ("bad", "", "0", "-1", "20.1", "inf", "nan"):
            with self.subTest(timeout=timeout), patch.dict("os.environ", {"VOICE_TIMEOUT_SECONDS": timeout}, clear=True), self.assertRaises(ValueError):
                SpeechSettings.from_env()
        for name in ("MODEL_TIMEOUT_SECONDS", "MODEL_MAX_TOKENS"):
            with patch.dict("os.environ", {name: "bad"}, clear=True), self.assertRaises(ValueError):
                Settings.from_env()
