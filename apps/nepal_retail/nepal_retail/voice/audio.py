import io
import math
import struct
import wave

from nepal_retail.voice.contracts import InvalidOrder
from nepal_retail.voice.providers import validate_audio


def encode_pcm16_wav(pcm_frames: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
	if not isinstance(pcm_frames, (bytes, bytearray)) or not pcm_frames:
		raise InvalidOrder("PCM audio frames must be nonempty bytes")
	if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 8000 <= sample_rate <= 48000:
		raise InvalidOrder("Unsupported WAV sample rate")
	if isinstance(channels, bool) or channels not in (1, 2):
		raise InvalidOrder("Unsupported WAV channel count")
	frame_width = channels * 2
	if len(pcm_frames) % frame_width != 0:
		raise InvalidOrder("PCM byte length must align to 16-bit channel frames")
	total_frames = len(pcm_frames) // frame_width
	if not 0 < total_frames / sample_rate <= 30:
		raise InvalidOrder("Use 16-bit PCM WAV audio between zero and 30 seconds")
	buffer = io.BytesIO()
	with wave.open(buffer, "wb") as wav_file:
		wav_file.setnchannels(channels)
		wav_file.setsampwidth(2)
		wav_file.setframerate(sample_rate)
		wav_file.writeframes(bytes(pcm_frames))
	wav_bytes = buffer.getvalue()
	validate_audio(wav_bytes)
	return wav_bytes


def float32_to_pcm16_wav(
	samples: list[float] | tuple[float, ...],
	sample_rate: int = 16000,
	channels: int = 1,
) -> bytes:
	if not isinstance(samples, (list, tuple)) or not samples:
		raise InvalidOrder("Audio sample list must not be empty")
	if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 8000 <= sample_rate <= 48000:
		raise InvalidOrder("Unsupported WAV sample rate")
	if isinstance(channels, bool) or channels not in (1, 2):
		raise InvalidOrder("Unsupported WAV channel count")
	max_samples = sample_rate * channels * 30
	if len(samples) > max_samples or len(samples) % channels != 0:
		raise InvalidOrder("Audio samples exceed 30 seconds or do not align with channels")
	pcm = bytearray(len(samples) * 2)
	for index, sample in enumerate(samples):
		if isinstance(sample, bool) or not isinstance(sample, (int, float)) or not math.isfinite(sample):
			raise InvalidOrder("Audio samples must be finite numbers")
		clamped = max(-1.0, min(1.0, float(sample)))
		scaled = int(round(clamped * 32767.0))
		struct.pack_into("<h", pcm, index * 2, scaled)
	return encode_pcm16_wav(bytes(pcm), sample_rate=sample_rate, channels=channels)
