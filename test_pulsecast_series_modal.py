import unittest
import re
import subprocess
import json

class TestPulsecastSeriesEpisodes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open('app.py', 'r', encoding='utf-8') as f:
            cls.app_content = f.read()

    def test_render_episodes_list_defines_safe_variables(self):
        # Locate renderEpisodesList function definition
        start_idx = self.app_content.find("function renderEpisodesList(episodes) {")
        self.assertNotEqual(start_idx, -1, "renderEpisodesList not found")
        snippet = self.app_content[start_idx:start_idx + 4000]

        # Verify safeTitle and safeUrl are declared
        self.assertIn("const safeTitle = escapeHtml(title);", snippet)
        self.assertIn("const safeUrl = escapeHtml(streamUrl);", snippet)
        self.assertIn("${safeTitle}", snippet)
        self.assertIn("downloadSingleEpisode('${safeUrl}'", snippet)

    def test_local_group_and_episodes_contract(self):
        # openPulsecastLocalGroupModal exists and calls renderEpisodesList and populateSeasonFilter
        self.assertIn("function openPulsecastLocalGroupModal(idx) {", self.app_content)
        start_idx = self.app_content.find("function openPulsecastLocalGroupModal(idx) {")
        snippet = self.app_content[start_idx:start_idx + 1000]
        self.assertIn("populateSeasonFilter(item.files);", snippet)
        self.assertIn("renderEpisodesList(item.files);", snippet)
        self.assertIn("pulsecastSeriesModal", snippet)

    def test_js_syntax_and_execution_with_node(self):
        # Extract js helper and functions to run through node
        js_code = """
        function escapeHtml(str) {
          if (!str) return '';
          return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
        }
        function escapeJsString(str) {
          if (!str) return '';
          return JSON.stringify(String(str)).slice(1, -1).replace(/'/g, "\\\\'");
        }

        let innerHTMLResult = '';
        const document = {
          getElementById: (id) => {
            if (id === 'pulsecastEpisodesList') {
              return {
                set innerHTML(val) { innerHTMLResult = val; },
                get innerHTML() { return innerHTMLResult; }
              };
            }
            if (id === 'pulsecastSeasonFilterSelect') {
              return { innerHTML: '', value: 'all' };
            }
            return { style: {}, textContent: '' };
          }
        };

        let pulsecastCurrentSeriesEpisodes = [];
        let pulsecastActiveSeries = null;
        """

        # Extract renderEpisodesList and populateSeasonFilter from app.py
        fn_render_start = self.app_content.find("function renderEpisodesList(episodes) {")
        fn_render_end = self.app_content.find("\n  async function downloadSingleEpisode", fn_render_start)
        render_code = self.app_content[fn_render_start:fn_render_end]

        fn_filter_start = self.app_content.find("function populateSeasonFilter(episodes) {")
        fn_filter_end = self.app_content.find("\n  function filterPulsecastEpisodesBySeason", fn_filter_start)
        filter_code = self.app_content[fn_filter_start:fn_filter_end]

        test_run = """
        // Test populateSeasonFilter & renderEpisodesList
        const mockEpisodes = [
          {
            filename: "Serie.S01E01.mkv",
            metadata: { title: "Pilot", seasonEpisode: "S01E01", cast: { duration: "45m", rating: 8.5 } },
            isXtream: false
          },
          {
            filename: "Serie.S02E01.mkv",
            metadata: { title: "Season 2 Opener", seasonEpisode: "S02E01" },
            isXtream: true
          }
        ];

        populateSeasonFilter(mockEpisodes);
        renderEpisodesList(mockEpisodes);

        if (!innerHTMLResult.includes("Pilot")) throw new Error("Missing episode title");
        if (!innerHTMLResult.includes("S01E01")) throw new Error("Missing season tag");
        if (!innerHTMLResult.includes("downloadSingleEpisode")) throw new Error("Missing download handler");
        console.log("NODE_OK");
        """

        full_script = js_code + "\n" + filter_code + "\n" + render_code + "\n" + test_run
        proc = subprocess.run(["node", "-e", full_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node execution failed: {proc.stderr}")
        self.assertIn("NODE_OK", proc.stdout)

if __name__ == '__main__':
    unittest.main()
