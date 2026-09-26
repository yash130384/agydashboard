import unittest
import re
import subprocess
import os
import app as app_module

class TestSubspaceAudioPlayerUI(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.client = self.app.test_client()

        with open("app.py", "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_dom_elements_present_in_subspace_comm(self):
        """Verify that all required LCARS audio player elements are present in the HTML template."""
        # 1. Player control buttons and elements
        self.assertIn('id="btnSubspaceStreamPlay"', self.content)
        self.assertIn('id="btnSubspaceStreamMute"', self.content)
        self.assertIn('id="subspaceStreamVolume"', self.content)
        self.assertIn('id="subspaceStreamVolText"', self.content)
        self.assertIn('id="subspaceAudioStreamStatus"', self.content)
        self.assertIn('id="subspaceStreamMeter"', self.content)
        self.assertIn('id="subspaceStreamMeterText"', self.content)
        self.assertIn('id="subspaceLiveAudio"', self.content)

        # 2. Check onclick / oninput bindings
        self.assertIn('onclick="toggleSubspaceLiveAudio()"', self.content)
        self.assertIn('onclick="toggleSubspaceStreamMute()"', self.content)
        self.assertIn('oninput="setSubspaceStreamVolume(this.value)"', self.content)

    def test_javascript_functions_and_variables_exist(self):
        """Verify JS functions for live stream, mute, volume, analyser and meter exist."""
        self.assertIn("function toggleSubspaceLiveAudio()", self.content)
        self.assertIn("function stopSubspaceLiveAudio()", self.content)
        self.assertIn("function toggleSubspaceStreamMute()", self.content)
        self.assertIn("function setSubspaceStreamVolume(", self.content)
        self.assertIn("function renderSubspaceStreamMeter(", self.content)
        self.assertIn("function startSubspaceAnalyserLoop()", self.content)
        self.assertIn("/api/voice/stream", self.content)

    def test_javascript_syntax_validity(self):
        """Extract script blocks from DASHBOARD_HTML and run node syntax validation with Jinja tokens mocked."""
        template = getattr(app_module, "DASHBOARD_HTML", self.content)
        scripts = re.findall(r"<script>(.*?)</script>", template, re.DOTALL)
        self.assertTrue(len(scripts) >= 2)

        for i, raw_script in enumerate(scripts):
            # Replace Jinja tags {{ ... }} with {} and {% ... %} with /* jinja */
            cleaned_script = re.sub(r"\{\{.*?\}\}", "{}", raw_script)
            cleaned_script = re.sub(r"\{%.*?%\}", "/* jinja */", cleaned_script)

            proc = subprocess.run(
                ["node", "--check", "-"],
                input=cleaned_script.encode("utf-8"),
                capture_output=True
            )
            self.assertEqual(
                proc.returncode, 0,
                f"Script block {i} failed syntax check:\n{proc.stderr.decode('utf-8')}"
            )

    def test_endpoint_accessible(self):
        """Verify GET /api/voice/stream is registered on the Flask application."""
        rules = [rule.rule for rule in self.app.url_map.iter_rules()]
        self.assertIn("/api/voice/stream", rules)
        self.assertIn("/api/audio/live", rules)

if __name__ == "__main__":
    unittest.main()
