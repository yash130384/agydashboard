import unittest
from unittest.mock import patch, mock_open
import app


class TestSystemThemeVariable(unittest.TestCase):
    def test_hardware_model_detection_live(self):
        model = app.get_hardware_model()
        self.assertIsInstance(model, str)
        self.assertTrue(len(model) > 0)
        self.assertNotIn("none", model.lower())

    def test_system_elbow_label_live(self):
        label = app.get_system_elbow_label(ram_total_gb=7.62)
        self.assertIsInstance(label, str)
        self.assertIn("// 8G", label)

    def test_system_elbow_label_raspberry_pi_mock(self):
        with patch("app.get_hardware_model", return_value="Raspberry Pi 4 Model B Rev 1.4"):
            label = app.get_system_elbow_label(ram_total_gb=8.0)
            self.assertEqual(label, "PI-4B // 8G")

        with patch("app.get_hardware_model", return_value="Raspberry Pi 5 Model B Rev 1.0"):
            label = app.get_system_elbow_label(ram_total_gb=8.0)
            self.assertEqual(label, "PI-5B // 8G")

    def test_system_elbow_label_thinkcentre_mock(self):
        with patch("app.get_hardware_model", return_value="LENOVO ThinkCentre M720q"):
            label = app.get_system_elbow_label(ram_total_gb=16.0)
            self.assertEqual(label, "M720Q // 16G")

    def test_get_system_stats_contains_variables(self):
        stats = app.get_system_stats()
        self.assertIn("system_model", stats)
        self.assertIn("system_label", stats)
        self.assertTrue(stats["system_model"])
        self.assertTrue(stats["system_label"])

    def test_no_hardcoded_pi_in_theme(self):
        # The hardcoded string 'PI-4B // 8G' must not exist in DASHBOARD_HTML
        self.assertNotIn("PI-4B // 8G", app.DASHBOARD_HTML)
        # The hardcoded terminal name 'AGY-PI' must not exist in DASHBOARD_HTML
        self.assertNotIn("AGY-PI", app.DASHBOARD_HTML)
        # The hardcoded 'Framework auf dem Raspberry Pi' must not exist in DASHBOARD_HTML
        self.assertNotIn("auf dem Raspberry Pi", app.DASHBOARD_HTML)

        # The new dynamic placeholders / ids must be present
        self.assertIn('id="sysElbowLabel">{{ stats.system_label }}', app.DASHBOARD_HTML)
        self.assertIn('id="topTerminalHost">{{ stats.hostname.upper() }}', app.DASHBOARD_HTML)
        self.assertIn('id="sysHardwareModel">{{ stats.system_model }}', app.DASHBOARD_HTML)

    def test_render_html_fallback(self):
        stats = {
            "hostname": "TestNode",
            "platform": "Linux TestOS (x86_64)",
            "system_model": "Custom Test Machine",
            "system_label": "TEST-NODE // 16G",
            "timestamp": "2026-09-25 12:00:00"
        }
        html = app.render_html_fallback(stats)
        self.assertIn("TEST-NODE // 16G", html)
        self.assertIn("TESTNODE", html)
        self.assertIn("Custom Test Machine", html)
        self.assertNotIn("PI-4B // 8G", html)
        self.assertNotIn("AGY-PI", html)


if __name__ == "__main__":
    unittest.main()
