import os
import re
import subprocess
import unittest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
JS_FILE = os.path.join(PROJECT_ROOT, "static", "js", "motion_alarm.js")
AUDIO_FILE = os.path.join(PROJECT_ROOT, "static", "audio", "rogue_one_alarm.mp3")
TEMPLATE_FILE = os.path.join(PROJECT_ROOT, "templates", "base.html")
JS_TEST_SUITE = os.path.join(PROJECT_ROOT, "test_motion_alarm_suite.js")
JS_E2E_SUITE = os.path.join(PROJECT_ROOT, "test_motion_alarm_e2e.js")


class TestMotionAlarmSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(JS_FILE, "r", encoding="utf-8") as f:
            cls.js_content = f.read()

        with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
            cls.template_content = f.read()

    def test_motion_alarm_artifacts_exist(self):
        """Verify that all motion alarm artifacts exist on disk and are non-empty."""
        self.assertTrue(os.path.isfile(JS_FILE), f"Missing {JS_FILE}")
        self.assertTrue(os.path.isfile(AUDIO_FILE), f"Missing {AUDIO_FILE}")
        self.assertTrue(os.path.isfile(TEMPLATE_FILE), f"Missing {TEMPLATE_FILE}")
        self.assertGreater(os.path.getsize(JS_FILE), 0)
        self.assertGreater(os.path.getsize(AUDIO_FILE), 0)

    def test_audio_file_header_valid(self):
        """Verify audio file exists and has valid audio container header (WAV/RIFF or MP3 sync)."""
        with open(AUDIO_FILE, "rb") as f:
            header = f.read(12)
        # RIFF header for WAV or ID3/0xFF for MP3
        is_riff = header.startswith(b"RIFF")
        is_id3 = header.startswith(b"ID3")
        is_mp3_sync = len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        self.assertTrue(is_riff or is_id3 or is_mp3_sync, "Audio file is not a valid RIFF/WAV or MP3 format")

    def test_template_contains_motion_alarm_elements(self):
        """Verify LCARS template contains audio tag, toggle switch, alert banner and script reference."""
        # Audio tag
        self.assertIn('id="alarmAudio"', self.template_content)
        self.assertIn('src="/static/audio/rogue_one_alarm.mp3"', self.template_content)

        # Toggle element
        self.assertIn('id="alarmToggle"', self.template_content)

        # Alert banner
        self.assertIn('id="intruderAlert"', self.template_content)

        # Script inclusion
        self.assertIn('src="/static/js/motion_alarm.js"', self.template_content)

        # Red alert theme definition
        self.assertIn('[data-theme="redalert"]', self.template_content)

    def test_javascript_syntax_clean(self):
        """Verify that static/js/motion_alarm.js passes node syntax check with zero errors."""
        proc = subprocess.run(
            ["node", "--check", JS_FILE],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"JS syntax check failed:\n{proc.stderr}")

    def test_javascript_contains_required_functions_and_keys(self):
        """Verify that motion_alarm.js has all required signatures and keys."""
        # localStorage keys
        self.assertIn("haMotionAlarmEnabled", self.js_content)
        self.assertIn("haMotionAlarmLastTrigger", self.js_content)

        # Cooldown constant (30s)
        self.assertIn("ALARM_COOLDOWN_MS", self.js_content)
        self.assertIn("30000", self.js_content)

        # Functions
        self.assertIn("function initializeAlarmToggle()", self.js_content)
        self.assertIn("function triggerAlarm()", self.js_content)
        self.assertIn("function checkMotionEvents(", self.js_content)

        # HA event subscription
        self.assertIn("haSubscribe", self.js_content)
        self.assertIn("state_changed", self.js_content)

    def test_node_test_suite_execution(self):
        """Execute node test runner covering unit tests, HA event integration, cooldown, audio and localStorage."""
        self.assertTrue(os.path.isfile(JS_TEST_SUITE), f"Missing {JS_TEST_SUITE}")
        proc = subprocess.run(
            ["node", JS_TEST_SUITE],
            capture_output=True,
            text=True
        )
        self.assertEqual(
            proc.returncode, 0,
            f"Node test suite failed with exit code {proc.returncode}:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        self.assertIn("ALL 14 MOTION ALARM JAVASCRIPT TESTS PASSED", proc.stdout)

    def test_node_e2e_suite_execution(self):
        """Execute node E2E simulation test verifying HA integration, audio, visual alerts, and localStorage."""
        self.assertTrue(os.path.isfile(JS_E2E_SUITE), f"Missing {JS_E2E_SUITE}")
        proc = subprocess.run(
            ["node", JS_E2E_SUITE],
            capture_output=True,
            text=True
        )
        self.assertEqual(
            proc.returncode, 0,
            f"Node E2E suite failed with exit code {proc.returncode}:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        self.assertIn("ALL E2E MOTION ALARM TESTS PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
