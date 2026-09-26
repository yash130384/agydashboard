import unittest
from unittest.mock import patch, MagicMock
import app as app_module

class TestVoiceStreamEndpoints(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.client = self.app.test_client()

    def test_voice_stream_unauthorized_when_locked(self):
        with patch.object(app_module, "_gemini_live_authorized", return_value=False):
            resp = self.client.get("/api/voice/stream")
            self.assertEqual(resp.status_code, 403)
            data = resp.get_json()
            self.assertTrue(data.get("locked"))

    def test_voice_stream_source_not_found(self):
        with patch.object(app_module, "_gemini_live_authorized", return_value=True):
            mock_res = MagicMock()
            mock_res.stdout = "1\talsa_output.pci\tPipeWire\n2\talsa_input.mic\tPipeWire\n"
            with patch("subprocess.run", return_value=mock_res):
                resp = self.client.get("/api/voice/stream?source=nonexistent123")
                self.assertEqual(resp.status_code, 503)
                data = resp.get_json()
                self.assertFalse(data.get("success"))
                self.assertIn("nicht gefunden", data.get("error"))

    def test_voice_stream_source_unavailable_popen_exit(self):
        # Mocking subprocess.Popen exiting immediately with exit code 1
        with patch.object(app_module, "_gemini_live_authorized", return_value=True):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 1
            mock_proc.stderr.read.return_value = b"Cannot open audio device"
            with patch("subprocess.Popen", return_value=mock_proc):
                resp = self.client.get("/api/voice/stream?source=default")
                self.assertEqual(resp.status_code, 503)
                data = resp.get_json()
                self.assertFalse(data.get("success"))
                self.assertIn("Audio-Quelle nicht verfügbar", data.get("error"))

    def test_voice_stream_popen_exception(self):
        with patch.object(app_module, "_gemini_live_authorized", return_value=True):
            with patch("subprocess.Popen", side_effect=OSError("ffmpeg binary not found")):
                resp = self.client.get("/api/voice/stream")
                self.assertEqual(resp.status_code, 500)
                data = resp.get_json()
                self.assertFalse(data.get("success"))
                self.assertIn("FFmpeg Startfehler", data.get("error"))

    def test_voice_stream_mp3_streaming_and_process_cleanup(self):
        with patch.object(app_module, "_gemini_live_authorized", return_value=True):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None  # running
            mock_proc.stdout.read1.side_effect = [b"MP3_CHUNK_1", b"MP3_CHUNK_2", b""]
            mock_proc.stdout.read.side_effect = [b"MP3_CHUNK_1", b"MP3_CHUNK_2", b""]
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                resp = self.client.get("/api/voice/stream?format=mp3")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "audio/mpeg")

                # Consume stream
                chunks = [chunk for chunk in resp.response]
                self.assertEqual(b"".join(chunks), b"MP3_CHUNK_1MP3_CHUNK_2")

                # Verify cleanup called
                mock_proc.terminate.assert_called_once()
                mock_proc.wait.assert_called_once()
                mock_proc.stdout.close.assert_called()

                # Verify ffmpeg args for mp3
                args, _ = mock_popen.call_args
                cmd = args[0]
                self.assertIn("libmp3lame", cmd)
                self.assertIn("mp3", cmd)

    def test_audio_live_ogg_streaming(self):
        with patch.object(app_module, "_gemini_live_authorized", return_value=True):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.stdout.read1.side_effect = [b"OGG_CHUNK_1", b""]
            mock_proc.stdout.read.side_effect = [b"OGG_CHUNK_1", b""]
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                resp = self.client.get("/api/audio/live?format=ogg")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "audio/ogg")

                chunks = [chunk for chunk in resp.response]
                self.assertEqual(b"".join(chunks), b"OGG_CHUNK_1")

                mock_proc.terminate.assert_called_once()
                args, _ = mock_popen.call_args
                cmd = args[0]
                self.assertIn("libopus", cmd)
                self.assertIn("ogg", cmd)

if __name__ == "__main__":
    unittest.main()
