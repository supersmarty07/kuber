import io
import json
import unittest
import wave
from unittest.mock import MagicMock, Mock

import requests
from nepal_retail.voice.contracts import InvalidOrder
from nepal_retail.voice.providers import OpenAIProvider, ProviderUnavailable, validate_audio


def recording(seconds=1):
	buffer = io.BytesIO()
	with wave.open(buffer, "wb") as audio:
		audio.setnchannels(1)
		audio.setsampwidth(2)
		audio.setframerate(8000)
		audio.writeframes(b"\x00\x00" * 8000 * seconds)
	return buffer.getvalue()


class ProviderTests(unittest.TestCase):
	def setUp(self):
		self.transport = Mock()
		self.provider = OpenAIProvider("test-only-key", "configured-model", self.transport)

	def respond(self, payload, status=200):
		self.transport.post.return_value = MagicMock()
		response = self.transport.post.return_value.__enter__.return_value
		response.status_code = status
		response.iter_content.return_value = [json.dumps(payload).encode()]
		return response

	def extracted(self, payload, finish="stop", refusal=None):
		self.respond(
			{
				"choices": [
					{"finish_reason": finish, "message": {"content": json.dumps(payload), "refusal": refusal}}
				]
			}
		)

	def test_extract_validated_order_and_safe_transport(self):
		payload = {"items": [{"description": "mouse", "quantity": "1"}], "customer": None, "discount": None}
		self.extracted(payload)
		self.assertEqual(self.provider.extract("euta mouse"), payload)
		arguments = self.transport.post.call_args
		self.assertEqual(arguments.args[0], "https://api.openai.com/v1/chat/completions")
		self.assertFalse(arguments.kwargs["allow_redirects"])
		self.assertEqual(arguments.kwargs["timeout"], (5, 45))
		self.assertTrue(arguments.kwargs["json"]["response_format"]["json_schema"]["strict"])

	def test_refused_truncated_and_injected_outputs_fail_closed(self):
		for payload, finish, refusal in (
			({"items": []}, "stop", None),
			({"items": [], "total": 5}, "stop", None),
			({}, "length", None),
			({}, "stop", "refused"),
		):
			with self.subTest(payload=payload, finish=finish, refusal=refusal):
				self.extracted(payload, finish, refusal)
				with self.assertRaises(ProviderUnavailable):
					self.provider.extract("mouse")

	def test_http_errors_and_timeouts_do_not_leak_provider_body(self):
		self.respond({"secret": "sensitive upstream message"}, 401)
		with self.assertRaises(ProviderUnavailable) as caught:
			self.provider.extract("mouse")
		self.assertNotIn("sensitive", str(caught.exception))
		self.transport.post.side_effect = requests.Timeout("secret")
		with self.assertRaises(ProviderUnavailable):
			self.provider.extract("mouse")

	def test_empty_and_oversized_transcripts_never_call_provider(self):
		for transcript in ("", " " * 10, "x" * 4001):
			with self.assertRaises(InvalidOrder):
				self.provider.extract(transcript)
		self.transport.post.assert_not_called()

	def test_audio_bounds_and_transcription(self):
		self.respond({"text": "euta mouse"})
		self.assertEqual(self.provider.transcribe(recording()), "euta mouse")
		for audio in (b"", b"not audio", recording(31), recording()[:-1]):
			with self.subTest(size=len(audio)), self.assertRaises(InvalidOrder):
				validate_audio(audio)

	def test_missing_credentials_fail_closed(self):
		with self.assertRaises(ProviderUnavailable):
			OpenAIProvider(None, "model")

	def test_oversized_provider_response_is_rejected(self):
		response = self.respond({})
		response.iter_content.return_value = [b"x" * 65537]
		with self.assertRaises(ProviderUnavailable):
			self.provider.extract("mouse")
