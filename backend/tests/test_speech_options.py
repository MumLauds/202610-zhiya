import json
import unittest
from collections import Counter
from pathlib import Path

from app.errors import ServiceError
from app.speech_options import VOICE_IDS, get_speech_options, validate_speech_rate, validate_voice_id


class SpeechOptionsTests(unittest.TestCase):
    def test_removed_model_has_no_available_rates_or_voices(self):
        options = get_speech_options("qwen3-tts-flash")
        self.assertEqual(options["speechRates"], [])
        self.assertEqual(options["voices"], [])
        for model, voice in (("qwen3-tts-flash", "wenhuaiqing_v3.1"), ("qwen-audio-3.1-tts-flash", "Cherry")):
            with self.assertRaises(ServiceError):
                validate_voice_id(voice, model)
        self.assertEqual(get_speech_options("")["voices"], [])
        self.assertEqual(get_speech_options("")["speechRates"], [])

    def test_options_are_valid_and_callers_cannot_mutate_catalog(self):
        options = get_speech_options()
        self.assertEqual([item["voiceId"] for item in options["voices"]], [
            "wenhuaiqing_v3.1", "yezhiqing_v3.1", "longniuniu_v3.1",
            "longshanshan_v3.1", "longjielidou_v3.1", "longpaopao_v3.1",
        ])
        self.assertEqual([item["displayName"] for item in options["voices"]],
                         ["温怀清", "叶知晴", "龙牛牛", "龙闪闪", "龙杰力豆", "龙泡泡"])
        self.assertEqual(list(VOICE_IDS), [item["voiceId"] for item in options["voices"]])
        self.assertEqual(Counter(item["category"] for item in options["voices"]),
                         {"CHINESE": 2, "OTHER_SYSTEM": 4})
        self.assertEqual(options["speechRates"], ["SLOW", "NORMAL", "FAST"])
        self.assertEqual(options["defaultSpeechRate"], "NORMAL")
        for item in options["voices"]:
            self.assertEqual(validate_voice_id(item["voiceId"]), item["voiceId"])
            self.assertTrue(item["displayName"] and item["description"])
        for rate in options["speechRates"]:
            self.assertEqual(validate_speech_rate(rate), rate)
        options["voices"][0]["voiceId"] = "invalid"
        options["voices"][0]["languages"].append("invalid")
        options["voices"][0]["scenarios"].append("invalid")
        options["voices"][0]["dialects"].append("invalid")
        options["speechRates"].append(2)
        self.assertEqual(get_speech_options()["voices"][0]["voiceId"], "wenhuaiqing_v3.1")
        self.assertNotIn("invalid", str(get_speech_options()))
        self.assertEqual(get_speech_options()["speechRates"], ["SLOW", "NORMAL", "FAST"])

    def test_language_metadata_and_exact_provider_identifiers(self):
        catalog = json.loads((Path(__file__).parents[1] / "app/voice_catalog.json").read_text(encoding="utf-8"))
        voices = {voice["voiceId"]: voice for voice in catalog["voices"]}
        self.assertEqual(len(catalog["voices"]), 6)
        self.assertEqual(len(voices), 6)
        self.assertEqual(voices["wenhuaiqing_v3.1"]["languages"], ["中文普通话"])
        self.assertIn("儿童故事", voices["yezhiqing_v3.1"]["scenarios"])
        self.assertIn("儿童有声书", voices["longniuniu_v3.1"]["scenarios"][0])
        self.assertEqual(voices["longniuniu_v3.1"]["languages"], [])
        for voice_id in ("Emily_v3.1", "yeqinghe_v3.1", "anmingyuan_v3.1", "longniuniu_v3.6", "Cherry", "Serena", "Ethan"):
            with self.subTest(voice_id=voice_id), self.assertRaises(ServiceError):
                validate_voice_id(voice_id)

    def test_invalid_preferences_reject_types_and_do_not_echo_values(self):
        cases = (
            (validate_voice_id, "TTS_INVALID_VOICE", (None, "", "cherry", " wenhuaiqing_v3.1 ", "private-input", 1, True, [], {})),
            (validate_speech_rate, "TTS_INVALID_RATE", (None, True, False, "1", "slow", " SLOW ", 0.8, 1.0, 1.2, float("nan"), float("inf"), [], {})),
        )
        for validate, code, values in cases:
            for value in values:
                with self.subTest(code=code, value=value), self.assertRaises(ServiceError) as caught:
                    validate(value)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.status_code, 422)
                self.assertFalse(caught.exception.retryable)
                self.assertNotIn("private-input", str(caught.exception))
