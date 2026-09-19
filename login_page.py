#!/usr/bin/env python3
"""
LCARS Login Interface Template for agydashboard (*.pimmel.site).
Styled according to authentic Star Trek LCARS design specifications.
"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="de" data-theme="classic">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>LCARS ACCESS AUTHORIZATION // FEDERATION TERMINAL</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Antonio:wght@400;600;700&family=Bebas+Neue&family=Share+Tech+Mono&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #000000;
      --font-family: 'Antonio', 'Arial Narrow', sans-serif;
      --mono-family: 'Share Tech Mono', monospace;
      --c-primary: #eb943a;       /* Okuda Orange */
      --c-secondary: #baa4e5;     /* Lilac / Violet */
      --c-blue: #8899ff;          /* Bluey */
      --c-almond: #d29b7f;        /* Almond */
      --c-butterscotch: #ea9c72;  /* Butterscotch */
      --c-red: #cf4f4f;           /* Mars Red / Danger */
      --c-gold: #edb378;          /* Barley Gold */
      --c-green: #44dd88;
      --c-card-bg: rgba(18, 18, 26, 0.95);
      --c-card-border: rgba(235, 148, 58, 0.4);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: #ffffff;
      font-family: var(--font-family);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      padding: 1.5rem;
      user-select: none;
      -webkit-user-select: none;
    }

    .lcars-frame {
      width: 100%;
      max-width: 580px;
      border: 2px solid var(--c-primary);
      border-radius: 20px 0 20px 0;
      background: var(--c-card-bg);
      box-shadow: 0 0 35px rgba(235, 148, 58, 0.15);
      overflow: hidden;
      position: relative;
    }

    .lcars-header {
      background-color: var(--c-primary);
      color: #000000;
      padding: 0.8rem 1.25rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-size: 1.35rem;
    }

    .lcars-subhead {
      background-color: var(--c-blue);
      color: #000000;
      padding: 0.35rem 1.25rem;
      font-family: var(--mono-family);
      font-size: 0.85rem;
      font-weight: 700;
      letter-spacing: 0.1em;
      display: flex;
      justify-content: space-between;
    }

    .lcars-body {
      padding: 2rem 1.75rem;
    }

    .service-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      background: rgba(235, 148, 58, 0.15);
      border: 1px solid var(--c-primary);
      border-radius: 4px;
      padding: 0.5rem 0.85rem;
      margin-bottom: 1.5rem;
      width: 100%;
      font-family: var(--mono-family);
      font-size: 0.9rem;
      color: var(--c-gold);
    }

    .form-group {
      margin-bottom: 1.35rem;
    }

    .form-label {
      display: block;
      color: var(--c-primary);
      font-size: 1.1rem;
      font-weight: 600;
      letter-spacing: 0.05em;
      margin-bottom: 0.4rem;
      text-transform: uppercase;
    }

    .lcars-input {
      width: 100%;
      background: #08080c;
      border: 2px solid var(--c-almond);
      border-radius: 6px;
      padding: 0.75rem 1rem;
      color: #ffffff;
      font-family: var(--mono-family);
      font-size: 1.2rem;
      letter-spacing: 0.05em;
      outline: none;
      transition: border-color 0.2s, box-shadow 0.2s;
    }

    .lcars-input:focus {
      border-color: var(--c-primary);
      box-shadow: 0 0 12px rgba(235, 148, 58, 0.4);
    }

    .checkbox-row {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-top: 0.5rem;
      margin-bottom: 1.75rem;
      cursor: pointer;
    }

    .checkbox-row input[type="checkbox"] {
      width: 20px;
      height: 20px;
      accent-color: var(--c-primary);
      cursor: pointer;
    }

    .checkbox-label {
      font-family: var(--mono-family);
      font-size: 0.9rem;
      color: #cccccc;
      letter-spacing: 0.05em;
    }

    .btn-row {
      display: flex;
      gap: 0.85rem;
    }

    .lcars-btn {
      flex: 1;
      padding: 0.85rem 1rem;
      font-family: var(--font-family);
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      transition: filter 0.15s, transform 0.05s;
    }

    .lcars-btn:active {
      transform: scale(0.98);
    }

    .btn-submit {
      background-color: var(--c-primary);
      color: #000000;
    }

    .btn-submit:hover { filter: brightness(1.15); }

    .btn-reset {
      background-color: var(--c-red);
      color: #ffffff;
    }

    .btn-reset:hover { filter: brightness(1.15); }

    .status-box {
      margin-top: 1.5rem;
      padding: 0.85rem;
      border-radius: 6px;
      font-family: var(--mono-family);
      font-size: 0.9rem;
      text-align: center;
      letter-spacing: 0.08em;
      display: none;
    }

    .status-error {
      background: rgba(207, 79, 79, 0.2);
      border: 1px solid var(--c-red);
      color: #ff8888;
      display: block;
    }

    .status-success {
      background: rgba(68, 221, 136, 0.2);
      border: 1px solid var(--c-green);
      color: var(--c-green);
      display: block;
    }

    .status-info {
      background: rgba(136, 153, 255, 0.2);
      border: 1px solid var(--c-blue);
      color: var(--c-blue);
      display: block;
    }

    .lcars-footer {
      border-top: 1px solid rgba(255, 255, 255, 0.1);
      padding: 0.75rem 1.75rem;
      display: flex;
      justify-content: space-between;
      font-family: var(--mono-family);
      font-size: 0.75rem;
      color: #777777;
    }
  </style>
</head>
<body>

  <div class="lcars-frame">
    <!-- Header -->
    <div class="lcars-header">
      <span>UNITED FEDERATION OF PLANETS</span>
      <span style="font-family:var(--mono-family); font-size:1rem;">SEC-LVL-4</span>
    </div>

    <div class="lcars-subhead">
      <span>SUBRAUM-ZUGANGSKONTROLLE // LCARS 24.9</span>
      <span id="stardateDisplay">SD 79248.5</span>
    </div>

    <!-- Body Form -->
    <div class="lcars-body">
      <div class="service-badge" id="serviceBadge">
        <span style="font-size:1.2rem;">🛡️</span>
        <div>
          <div style="font-weight:700; color:#fff;" id="targetServiceTitle">GESCHÜTZTER SUBRAUM-DIENST</div>
          <div style="font-size:0.8rem; color:#aaa;" id="targetServiceDesc">Identifikation erforderlich für Zugriff auf *.pimmel.site</div>
        </div>
      </div>

      <form id="lcarsLoginForm" onsubmit="handleLoginSubmit(event)">
        <div class="form-group">
          <label class="form-label" for="loginUsername">BENUTZERKENNUNG</label>
          <input type="text" id="loginUsername" name="username" class="lcars-input" autocomplete="username" autofocus required placeholder="OFFICER ID">
        </div>

        <div class="form-group">
          <label class="form-label" for="loginPassword">AUTORISIERUNGSCODE</label>
          <input type="password" id="loginPassword" name="password" class="lcars-input" autocomplete="current-password" required placeholder="••••••••••••">
        </div>

        <label class="checkbox-row">
          <input type="checkbox" id="loginRemember" name="remember" checked>
          <span class="checkbox-label">PERSISTENTE SUBRAUM-SESSION (30 TAGE AKTIV BLEIBEN)</span>
        </label>

        <div class="btn-row">
          <button type="submit" class="lcars-btn btn-submit" id="btnLoginSubmit">
            <span>🔓 AUTORISIEREN</span>
          </button>
          <button type="button" class="lcars-btn btn-reset" onclick="resetForm()">
            <span>CLR</span>
          </button>
        </div>

        <div id="statusBox" class="status-box"></div>
      </form>
    </div>

    <!-- Footer -->
    <div class="lcars-footer">
      <span>RESTRICTED ACCESS // STARFLEET COMMAND</span>
      <span>NODE: BIGGERPIMMEL // LINUX 6.x</span>
    </div>
  </div>

  <script>
    // Audio Synthesizer (Web Audio API)
    let audioCtx = null;
    function playBeep(f1 = 880, f2 = 1400, dur = 0.08) {
      try {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if (audioCtx.state === 'suspended') audioCtx.resume();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.type = 'sine';
        osc.frequency.setValueAtTime(f1, audioCtx.currentTime);
        if (f2) osc.frequency.exponentialRampToValueAtTime(f2, audioCtx.currentTime + dur);
        gain.gain.setValueAtTime(0.12, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + dur);
        osc.start();
        osc.stop(audioCtx.currentTime + dur);
      } catch (e) {}
    }

    function playSuccess() {
      playBeep(587, 880, 0.12);
      setTimeout(() => playBeep(880, 1174, 0.15), 100);
    }

    function playError() {
      playBeep(300, 150, 0.18);
    }

    // Stardate Calc
    function updateStardate() {
      const now = new Date();
      const yr = now.getFullYear();
      const start = new Date(yr, 0, 1);
      const diff = now - start;
      const oneYear = 365.25 * 24 * 60 * 60 * 1000;
      const sd = ((yr - 1923) * 1000) + (diff / oneYear * 1000);
      document.getElementById('stardateDisplay').textContent = 'SD ' + sd.toFixed(1);
    }
    updateStardate();

    // Parse URL params for return_to
    const urlParams = new URLSearchParams(window.location.search);
    const returnTo = urlParams.get('return_to') || '';

    if (returnTo) {
      try {
        const parsed = new URL(returnTo);
        const host = parsed.hostname;
        const sub = host.split('.')[0];
        const titleEl = document.getElementById('targetServiceTitle');
        const descEl = document.getElementById('targetServiceDesc');
        if (titleEl && descEl) {
          titleEl.textContent = 'ZUGANG ZU: ' + host.toUpperCase();
          descEl.textContent = 'Ziel: ' + parsed.pathname;
        }
      } catch (e) {}
    }

    function showStatus(msg, type) {
      const box = document.getElementById('statusBox');
      box.className = 'status-box status-' + type;
      box.textContent = msg;
      box.style.display = 'block';
    }

    function resetForm() {
      playBeep(440, 220, 0.1);
      document.getElementById('lcarsLoginForm').reset();
      document.getElementById('statusBox').style.display = 'none';
      document.getElementById('loginUsername').focus();
    }

    async function handleLoginSubmit(e) {
      e.preventDefault();
      playBeep(900, 1200, 0.06);

      const submitBtn = document.getElementById('btnLoginSubmit');
      const username = document.getElementById('loginUsername').value.trim();
      const password = document.getElementById('loginPassword').value;
      const remember = document.getElementById('loginRemember').checked;

      if (!username || !password) {
        showStatus('BENUTZERNAME UND PASSWORT ERFORDERLICH', 'error');
        playError();
        return;
      }

      submitBtn.disabled = true;
      showStatus('AUTORISIERUNG WIRD GEPRÜFT...', 'info');

      try {
        const resp = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: username,
            password: password,
            remember: remember,
            return_to: returnTo
          })
        });

        const data = await resp.json();

        if (resp.ok && data.success) {
          playSuccess();
          showStatus('AUTORISIERUNG ERFOLGREICH // LEITE WEITER...', 'success');
          setTimeout(() => {
            window.location.href = data.redirect_url || returnTo || '/';
          }, 400);
        } else {
          playError();
          showStatus(data.error || 'ZUGRIFF VERWEIGERT // UNGÜLTIGE ANMELDEDATEN', 'error');
          document.getElementById('loginPassword').value = '';
          document.getElementById('loginPassword').focus();
          submitBtn.disabled = false;
        }
      } catch (err) {
        playError();
        showStatus('SUBRAUM-KOMMUNIKATIONSFEHLER: ' + err, 'error');
        submitBtn.disabled = false;
      }
    }
  </script>
</body>
</html>
"""
