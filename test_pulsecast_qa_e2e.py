import unittest
import subprocess
import json
import re

class TestPulsecastQaE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open('app.py', 'r', encoding='utf-8') as f:
            cls.app_content = f.read()

    def _run_node_script(self, js_test_code):
        helper_code = """
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
        function playLcarsBeep() {}
        function playLcarsAcknowledge() {}
        function getPulsecastHeaders() { return { 'Content-Type': 'application/json' }; }
        """

        # Extract functions from app.py
        fn_season_filter = self._extract_function("populateSeasonFilter")
        fn_filter_episodes = self._extract_function("filterPulsecastEpisodesBySeason")
        fn_render_episodes = self._extract_function("renderEpisodesList")
        fn_download_single = self._extract_function("downloadSingleEpisode")
        fn_download_all = self._extract_function("downloadAllVisibleEpisodes")
        fn_open_local = self._extract_function("openPulsecastLocalGroupModal")

        full_script = "\n".join([
            helper_code,
            fn_season_filter,
            fn_filter_episodes,
            fn_render_episodes,
            fn_download_single,
            fn_download_all,
            fn_open_local,
            js_test_code
        ]).replace(r'\\d', r'\d')

        proc = subprocess.run(["node", "-e", full_script], capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"Node execution failed (code {proc.returncode}):\n{proc.stderr}\nCode:\n{full_script}")
        return json.loads(proc.stdout.strip())

    def _extract_function(self, fn_name):
        pattern = rf"(  (?:async )?function {fn_name}\s*\([^)]*\)\s*\{{)"
        m = re.search(pattern, self.app_content)
        if not m:
            raise AssertionError(f"Function {fn_name} not found in app.py")
        start = m.start()
        brace_count = 0
        idx = m.end() - 1
        for i in range(idx, len(self.app_content)):
            char = self.app_content[i]
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    return self.app_content[start:i+1]
        raise ValueError(f"Could not find matching closing brace for {fn_name}")

    def test_episodes_rendering_local_and_xtream(self):
        js = """
        let listHtml = '';
        const document = {
          getElementById: (id) => {
            if (id === 'pulsecastEpisodesList') {
              return {
                set innerHTML(val) { listHtml = val; },
                get innerHTML() { return listHtml; }
              };
            }
            return { style: {}, textContent: '' };
          }
        };

        const episodes = [
          {
            filename: '/media/INTENSO/Serien/S01E01.mkv',
            isXtream: false,
            metadata: {
              title: '<Episode 1 & "Pilot">',
              seasonEpisode: 'S01E01',
              cast: { duration: '45m', rating: 8.5 }
            }
          },
          {
            filename: 'http://xtream.server:8080/series/user/pass/102.mp4',
            isXtream: true,
            metadata: {
              title: 'Xtream Episode 2',
              seasonEpisode: 'S01E02'
            }
          }
        ];

        renderEpisodesList(episodes);

        const hasEscapedTitle = listHtml.includes('&lt;Episode 1 &amp; &quot;Pilot&quot;&gt;');
        const hasLocalPlayerBtn = listHtml.includes('openPulsecastPlayerModal');
        const hasDownloadBtn = listHtml.includes('downloadSingleEpisode');

        console.log(JSON.stringify({
          rendered: true,
          hasEscapedTitle,
          hasLocalPlayerBtn,
          hasDownloadBtn
        }));
        """
        res = self._run_node_script(js)
        self.assertTrue(res['rendered'])
        self.assertTrue(res['hasEscapedTitle'], "XSS characters not escaped in safeTitle")
        self.assertTrue(res['hasLocalPlayerBtn'], "Player button missing for local episode")
        self.assertTrue(res['hasDownloadBtn'], "Download button missing")

    def test_season_filter_matches_both_s01_and_s1(self):
        js = """
        let currentFilterValue = 'all';
        let renderedHtml = '';
        let seasonOptions = '';

        const document = {
          getElementById: (id) => {
            if (id === 'pulsecastEpisodesList') {
              return {
                set innerHTML(val) { renderedHtml = val; },
                get innerHTML() { return renderedHtml; }
              };
            }
            if (id === 'pulsecastSeasonFilterSelect') {
              return {
                set innerHTML(val) { seasonOptions = val; },
                get innerHTML() { return seasonOptions; },
                get value() { return currentFilterValue; },
                set value(v) { currentFilterValue = v; }
              };
            }
            return { style: {}, textContent: '' };
          }
        };

        let pulsecastCurrentSeriesEpisodes = [
          { filename: 'ep1.mkv', metadata: { title: 'E1', seasonEpisode: 'S01E01' } },
          { filename: 'ep2.mkv', metadata: { title: 'E2', seasonEpisode: 'S01E02' } },
          { filename: 'ep3.mkv', metadata: { title: 'E3', seasonEpisode: 'S02E01' } }
        ];

        populateSeasonFilter(pulsecastCurrentSeriesEpisodes);

        // Filter by S1
        currentFilterValue = 'S1';
        filterPulsecastEpisodesBySeason();
        const s1Matches = !renderedHtml.includes('KEINE EPISODEN GEFUNDEN') && renderedHtml.includes('S01E01') && renderedHtml.includes('S01E02') && !renderedHtml.includes('S02E01');

        // Filter by S2
        currentFilterValue = 'S2';
        filterPulsecastEpisodesBySeason();
        const s2Matches = !renderedHtml.includes('KEINE EPISODEN GEFUNDEN') && !renderedHtml.includes('S01E01') && renderedHtml.includes('S02E01');

        // Filter by all
        currentFilterValue = 'all';
        filterPulsecastEpisodesBySeason();
        const allMatches = renderedHtml.includes('S01E01') && renderedHtml.includes('S02E01');

        console.log(JSON.stringify({
          seasonOptions,
          s1Matches,
          s2Matches,
          allMatches
        }));
        """
        res = self._run_node_script(js)
        self.assertTrue(res['s1Matches'], "Season filter failed on S01E01 with S1 filter value")
        self.assertTrue(res['s2Matches'], "Season filter failed on S02E01 with S2 filter value")
        self.assertTrue(res['allMatches'], "All filter failed")

    def test_open_local_group_modal_workflow(self):
        js = """
        let modalDisplay = 'none';
        let modalTitle = '';
        let modalMeta = '';
        let listHtml = '';
        let seasonOptions = '';

        const document = {
          getElementById: (id) => {
            if (id === 'pulsecastSeriesModal') {
              return { style: { set display(v) { modalDisplay = v; }, get display() { return modalDisplay; } } };
            }
            if (id === 'pulsecastModalSeriesTitle') {
              return { set textContent(v) { modalTitle = v; }, get textContent() { return modalTitle; } };
            }
            if (id === 'pulsecastModalSeriesMeta') {
              return { set textContent(v) { modalMeta = v; }, get textContent() { return modalMeta; } };
            }
            if (id === 'pulsecastEpisodesLoading') {
              return { style: { display: 'none' } };
            }
            if (id === 'pulsecastEpisodesList') {
              return { set innerHTML(v) { listHtml = v; }, get innerHTML() { return listHtml; } };
            }
            if (id === 'pulsecastSeasonFilterSelect') {
              return { set innerHTML(v) { seasonOptions = v; }, get innerHTML() { return seasonOptions; }, value: 'all' };
            }
            return { style: {}, textContent: '' };
          }
        };

        let pulsecastLocalItemsCache = [
          {
            id: 'snw_local',
            title: 'Star Trek: Strange New Worlds',
            posterUrl: '/poster.jpg',
            files: [
              { filename: 'SNW.S01E01.mkv', isXtream: false, metadata: { title: 'Pilot', seasonEpisode: 'S01E01' } },
              { filename: 'SNW.S01E02.mkv', isXtream: false, metadata: { title: 'Ep2', seasonEpisode: 'S01E02' } }
            ]
          }
        ];

        let pulsecastActiveSeries = null;
        let pulsecastCurrentSeriesEpisodes = [];

        openPulsecastLocalGroupModal(0);

        console.log(JSON.stringify({
          modalDisplay,
          modalTitle,
          modalMeta,
          activeSeriesTitle: pulsecastActiveSeries?.title,
          episodesLoaded: pulsecastCurrentSeriesEpisodes.length,
          hasPilot: listHtml.includes('Pilot')
        }));
        """
        res = self._run_node_script(js)
        self.assertEqual(res['modalDisplay'], 'flex')
        self.assertEqual(res['modalTitle'], 'Star Trek: Strange New Worlds')
        self.assertIn('2 EPISODEN VORHANDEN', res['modalMeta'])
        self.assertEqual(res['episodesLoaded'], 2)
        self.assertTrue(res['hasPilot'])

    def test_download_single_and_batch_actions(self):
        js = """
        let fetchCalls = [];
        const fetch = async (url, opts) => {
          fetchCalls.push({ url, opts: opts ? { ...opts, body: JSON.parse(opts.body) } : null });
          return { ok: true, json: async () => ({ success: true }) };
        };

        const alert = () => {};
        const confirm = () => true;

        let currentFilterValue = 'S2';
        const document = {
          getElementById: (id) => {
            if (id === 'pulsecastSeasonFilterSelect') {
              return { value: currentFilterValue };
            }
            if (id === 'pulsecastEpisodesList') {
              return { set innerHTML(v) {} };
            }
            return { style: {}, textContent: '' };
          }
        };

        let pulsecastActiveSeries = { id: 'mando', title: 'The Mandalorian' };
        let pulsecastCurrentSeriesEpisodes = [
          { filename: 'ep1.mkv', metadata: { title: 'Chapter 1', seasonEpisode: 'S01E01' } },
          { filename: 'ep2.mkv', metadata: { title: 'Chapter 9', seasonEpisode: 'S02E01' } },
          { filename: 'ep3.mkv', metadata: { title: 'Chapter 10', seasonEpisode: 'S02E02' } }
        ];

        (async () => {
          // 1. Download single episode
          await downloadSingleEpisode('http://stream.test/ep1.mkv', 'Chapter 1', 'S01E01');

          // 2. Download all visible episodes (filter = S2)
          await downloadAllVisibleEpisodes();

          console.log(JSON.stringify({
            fetchCalls
          }));
        })();
        """
        res = self._run_node_script(js)
        calls = res['fetchCalls']
        self.assertEqual(len(calls), 2)

        # Single episode check
        self.assertEqual(calls[0]['url'], '/api/pulsecast/download/media')
        self.assertEqual(calls[0]['opts']['body']['url'], 'http://stream.test/ep1.mkv')
        self.assertEqual(calls[0]['opts']['body']['title'], 'S01E01 - Chapter 1')
        self.assertEqual(calls[0]['opts']['body']['seriesTitle'], 'The Mandalorian')

        # Batch check (only Season 2 episodes should be sent)
        self.assertEqual(calls[1]['url'], '/api/pulsecast/download/media')
        batch_items = calls[1]['opts']['body']['items']
        self.assertEqual(len(batch_items), 2, "Batch download did not filter by S2 correctly")
        self.assertTrue(all('S02' in item['title'] for item in batch_items))

if __name__ == '__main__':
    unittest.main()
