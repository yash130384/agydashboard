#!/usr/bin/env python3
"""
Test Suite for Faster-Whisper STT Backend and Cactus Needle Integration in agydashboard.
Tests:
- /api/gemini-live/status includes model & STT metadata
- /api/voice/transcribe rejects unauthorized requests when locked
- /api/voice/transcribe rejects empty audio
- /api/voice/transcribe processes WAV/WebM audio with Faster-Whisper
- /api/voice/transcribe with process=false returns only transcription
- /api/voice/transcribe with process=true forwards to Cactus Needle pipeline
"""

import io
import unittest
import wave
import numpy as np

from app import app, get_whisper_model, _process_cactus_prompt


class TestVoiceSttIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.auth_headers = {"X-Command-Code": "0901"}
        cls.model = get_whisper_model()

    def _create_wav(self, duration_s=1.0, freq=440, sample_rate=16000):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            t = np.linspace(0, duration_s, int(sample_rate * duration_s), False)
            if freq > 0:
                audio_data = (np.sin(2 * np.pi * freq * t) * 10000).astype(np.int16)
            else:
                audio_data = np.zeros(int(sample_rate * duration_s), dtype=np.int16)
            wav_file.writeframes(audio_data.tobytes())
        buf.seek(0)
        return buf

    def test_status_endpoint(self):
        resp = self.client.get("/api/gemini-live/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("configured"))
        self.assertIn("Cactus Needle", data.get("model", ""))
        self.assertIn("Faster-Whisper", data.get("stt", ""))

    def test_auth_rejection_without_code(self):
        resp = self.client.post("/api/voice/transcribe", data=b"dummy", headers={"Content-Type": "audio/webm"})
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertTrue(data.get("locked"))

    def test_empty_audio_rejection(self):
        resp = self.client.post("/api/voice/transcribe", data=b"", headers={**self.auth_headers, "Content-Type": "audio/webm"})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data.get("success"))

    def test_silent_audio_transcription(self):
        silent_wav = self._create_wav(duration_s=1.0, freq=0)
        resp = self.client.post(
            "/api/voice/transcribe?mode=test",
            data={"audio": (silent_wav, "silent.wav")},
            headers=self.auth_headers,
            content_type="multipart/form-data"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("text"), "")
        self.assertFalse(data.get("recognized"))

    def test_process_cactus_prompt_direct(self):
        res = _process_cactus_prompt("Schalte Decke 1 an", mode="test")
        self.assertIn("mode", res)
        self.assertEqual(res["mode"], "test")
        self.assertIn("prompt", res)
        self.assertIn("message", res)

    def test_cactus_process_endpoint(self):
        resp = self.client.post(
            "/api/cactus/process",
            json={"prompt": "Decke 2 aus", "mode": "test"},
            headers=self.auth_headers
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("mode"), "test")
        self.assertIn("prompt", data)

    def test_transcribe_raw_body_without_processing(self):
        silent_wav = self._create_wav(duration_s=0.5, freq=0)
        resp = self.client.post(
            "/api/voice/transcribe?mode=test&process=false",
            data=silent_wav.getvalue(),
            headers={**self.auth_headers, "Content-Type": "audio/wav"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("text"), "")

    def test_concurrent_transcribe_thread_safety(self):
        import concurrent.futures
        wav1 = self._create_wav(duration_s=0.5, freq=0).getvalue()
        wav2 = self._create_wav(duration_s=0.5, freq=0).getvalue()

        def request_transcribe(wav_bytes):
            return self.client.post(
                "/api/voice/transcribe?mode=test&process=false",
                data=wav_bytes,
                headers={**self.auth_headers, "Content-Type": "audio/wav"}
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(request_transcribe, wav1)
            f2 = executor.submit(request_transcribe, wav2)
            r1 = f1.result()
            r2 = f2.result()

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r1.get_json().get("success"))
        self.assertTrue(r2.get_json().get("success"))

    def test_corrupted_audio_handling(self):
        # Invalid / corrupted audio data should return 500 or error json gracefully, not crash server
        resp = self.client.post(
            "/api/voice/transcribe?mode=test&process=false",
            data=b"INVALID_CORRUPTED_AUDIO_PAYLOAD",
            headers={**self.auth_headers, "Content-Type": "audio/webm"}
        )
        self.assertEqual(resp.status_code, 500)
        data = resp.get_json()
        self.assertFalse(data.get("success"))
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
