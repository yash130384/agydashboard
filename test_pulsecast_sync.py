import unittest
from unittest.mock import patch, MagicMock
import json
import re
import subprocess
import app as app_module

class TestPulsecastSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app_module.app.test_client()
        with open('app.py', 'r', encoding='utf-8') as f:
            cls.app_content = f.read()

    def test_backend_sync_unauthorized(self):
        with patch.object(app_module, "_pulsecast_authorized", return_value=False):
            resp = self.client.post("/api/pulsecast/sync")
            self.assertEqual(resp.status_code, 403)
            data = resp.get_json()
            self.assertFalse(data.get("success"))
            self.assertTrue(data.get("locked"))

    def test_backend_sync_success_default_hours(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "xtreamSyncIntervalHours": 2,
            "message": "Sync triggered"
        }
        with patch.object(app_module, "_pulsecast_authorized", return_value=True), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            resp = self.client.post("/api/pulsecast/sync", json={})
            self.assertEqual(resp.status_code, 200)
            mock_post.assert_called_once_with(
                "http://127.0.0.1:3000/api/settings",
                json={"xtreamSyncIntervalHours": 2},
                timeout=15
            )
            data = resp.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("xtreamSyncIntervalHours"), 2)

    def test_backend_sync_custom_hours(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "xtreamSyncIntervalHours": 4}
        with patch.object(app_module, "_pulsecast_authorized", return_value=True), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            resp = self.client.post("/api/pulsecast/sync", json={"xtreamSyncIntervalHours": 4})
            self.assertEqual(resp.status_code, 200)
            mock_post.assert_called_once_with(
                "http://127.0.0.1:3000/api/settings",
                json={"xtreamSyncIntervalHours": 4},
                timeout=15
            )

    def test_frontend_elements_in_html(self):
        self.assertIn('id="pulsecastSyncBtn"', self.app_content)
        self.assertIn('📡 SYNC METADATEN', self.app_content)
        self.assertIn('id="pulsecastSyncSpinner"', self.app_content)
        self.assertIn('id="pulsecastToast"', self.app_content)
        self.assertIn('onclick="triggerPulsecastSync()"', self.app_content)

    def test_frontend_trigger_sync_js_flow(self):
        # Extract function triggerPulsecastSync and showPulsecastToast from app.py
        fn_match = re.search(r'(async function triggerPulsecastSync\s*\([^)]*\)\s*\{[\s\S]*?\n  \})', self.app_content)
        self.assertIsNotNone(fn_match, "triggerPulsecastSync function not found in app.py")
        fn_sync = fn_match.group(1)

        fn_toast_match = re.search(r'(function showPulsecastToast\s*\([^)]*\)\s*\{[\s\S]*?\n  \})', self.app_content)
        self.assertIsNotNone(fn_toast_match, "showPulsecastToast function not found in app.py")
        fn_toast = fn_toast_match.group(1)

        js_script = f"""
        let fetchCalls = [];
        let toastMessages = [];
        let catalogReloadCalled = false;
        let localCountsReloadCalled = false;

        function playLcarsBeep() {{}}
        function getPulsecastHeaders() {{ return {{ 'X-Command-Code': '0901', 'Content-Type': 'application/json' }}; }}

        let pulsecastCatalogPage = 1;
        let pulsecastToastTimeout = null;
        let pulsecastActiveSubtab = 'downloads';

        async function loadPulsecastCatalog(page) {{
          catalogReloadCalled = true;
        }}
        function loadPulsecastLocalCounts() {{
          localCountsReloadCalled = true;
        }}

        const btnMock = {{
          disabled: false,
          style: {{ opacity: '1' }}
        }};
        const spinnerMock = {{
          style: {{ display: 'none' }}
        }};
        const toastMock = {{
          style: {{ display: 'none', opacity: '0' }},
          textContent: ''
        }};

        const document = {{
          getElementById: (id) => {{
            if (id === 'pulsecastSyncBtn') return btnMock;
            if (id === 'pulsecastSyncSpinner') return spinnerMock;
            if (id === 'pulsecastToast') return toastMock;
            return null;
          }},
          body: {{
            appendChild: () => {{}}
          }}
        }};

        const fetch = async (url, opts) => {{
          fetchCalls.push({{ url, opts: {{ ...opts, body: JSON.parse(opts.body) }} }});
          return {{
            ok: true,
            status: 200,
            json: async () => ({{ success: true }})
          }};
        }};

        {fn_toast}
        {fn_sync}

        (async () => {{
          await triggerPulsecastSync();
          console.log(JSON.stringify({{
            fetchCalls,
            toastText: toastMock.textContent,
            btnDisabled: btnMock.disabled,
            btnOpacity: btnMock.style.opacity,
            spinnerDisplay: spinnerMock.style.display,
            catalogReloadCalled,
            localCountsReloadCalled
          }}));
        }})();
        """

        proc = subprocess.run(["node", "-e", js_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node script failed:\n{proc.stderr}")
        res = json.loads(proc.stdout.strip())

        self.assertEqual(len(res["fetchCalls"]), 1)
        self.assertEqual(res["fetchCalls"][0]["url"], "/api/pulsecast/sync")
        self.assertEqual(res["fetchCalls"][0]["opts"]["body"]["xtreamSyncIntervalHours"], 2)
        self.assertEqual(res["toastText"], "Sync gestartet...")
        self.assertFalse(res["btnDisabled"])
        self.assertEqual(res["spinnerDisplay"], "none")
        self.assertTrue(res["catalogReloadCalled"])
        self.assertTrue(res["localCountsReloadCalled"])

if __name__ == '__main__':
    unittest.main()
