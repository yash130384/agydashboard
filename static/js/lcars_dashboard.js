  var historyChart = null;
  var hermesChart = null;
  var nineRouterTimelineChart = null;
  var nineRouterModelChart = null;
  var currentNineRouterData = initialStats?.nine_router || null;
  var activeRange = '1h';
  var currentHistorySamples = initialStats?.history_samples || [];
  var visibleDatasets = [true, true, true, false]; // 0: Temp, 1: CPU, 2: Throttle, 3: RAM
  var currentCategory = 'system';
  var categoryHistory = ['system'];
  var categoryHistoryIndex = 0;

  function isUserAllowedSection(secId) {
    if (!currentLcarsUser) return true;
    if (currentLcarsUser.is_super_admin) return true;
    const allowed = currentLcarsUser.allowed_sections || [];
    if (allowed.includes('*') || allowed.includes('all') || allowed.includes(secId)) return true;
    const svcs = currentLcarsUser.allowed_services || [];
    if (svcs.includes('*') || svcs.includes('all') || svcs.includes(secId)) return true;
    return false;
  }

  function navigateCategoryHistory(delta) {
    const targetIndex = categoryHistoryIndex + delta;
    if (targetIndex >= 0 && targetIndex < categoryHistory.length) {
      categoryHistoryIndex = targetIndex;
      const catId = categoryHistory[categoryHistoryIndex];
      switchCategory(catId, true);
      return true;
    }
    return false;
  }
  var lastServicesFingerprint = '';
  var lastNrConnFingerprint = '';
  var lastNrHistoryFingerprint = '';
  var lastNrInstancesFingerprint = '';
  var latestDiscoveredServers = initialStats?.discovered_servers || [];

  // Sound Engine (Web Audio API Synthesizer)
  let audioContext = null;
  let soundEnabled = (localStorage.getItem('lcars-sound') !== 'false');

  function getAudioCtx() {
    if (!audioContext) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        try {
          audioContext = new AudioCtx({ sampleRate: 16000 });
        } catch (e) {
          try {
            audioContext = new AudioCtx();
          } catch (e2) {
            audioContext = null;
          }
        }
      }
    }
    return audioContext;
  }

  function playLcarsBeep(f1 = 880, f2 = 1760) {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      if (ctx.state === 'suspended') ctx.resume();

      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(f1, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(f2, ctx.currentTime + 0.08);

      gain.gain.setValueAtTime(0.04, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.08);

      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.08);
    } catch (e) {
      // Audio optional
    }
  }

  function playLcarsChirp() {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      if (ctx.state === 'suspended') ctx.resume();
      const now = ctx.currentTime;
      // Tone 1: E5 (659Hz) -> A5 (880Hz)
      const osc1 = ctx.createOscillator();
      const gain1 = ctx.createGain();
      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(659, now);
      osc1.frequency.exponentialRampToValueAtTime(880, now + 0.07);
      gain1.gain.setValueAtTime(0.06, now);
      gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.075);
      osc1.connect(gain1);
      gain1.connect(ctx.destination);
      osc1.start(now);
      osc1.stop(now + 0.08);

      // Tone 2: E6 (1318Hz) -> A6 (1760Hz)
      const osc2 = ctx.createOscillator();
      const gain2 = ctx.createGain();
      osc2.type = 'sine';
      osc2.frequency.setValueAtTime(1318, now + 0.075);
      osc2.frequency.exponentialRampToValueAtTime(1760, now + 0.16);
      gain2.gain.setValueAtTime(0.07, now + 0.075);
      gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.165);
      osc2.connect(gain2);
      gain2.connect(ctx.destination);
      osc2.start(now + 0.075);
      osc2.stop(now + 0.17);
    } catch(e) {}
  }

  function playLcarsAcknowledge() {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      if (ctx.state === 'suspended') ctx.resume();
      const now = ctx.currentTime;
      // Star Trek affirmative chime: Dual-tone B5 (988Hz) + E6 (1318Hz)
      [988, 1318].forEach(freq => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now);
        gain.gain.setValueAtTime(0.05, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.22);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.23);
      });
    } catch(e) {}
  }

  function playLcarsError() {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      if (ctx.state === 'suspended') ctx.resume();
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(240, now);
      osc.frequency.setValueAtTime(180, now + 0.12);
      gain.gain.setValueAtTime(0.06, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.26);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.27);
    } catch(e) {}
  }

  function toggleAudio() {
    soundEnabled = !soundEnabled;
    localStorage.setItem('lcars-sound', soundEnabled);
    updateAudioUI();
    if (soundEnabled) playLcarsBeep(880, 1760);
  }

  function updateAudioUI() {
    const label = soundEnabled ? 'AUDIO AN' : 'AUDIO AUS';
    const icon = soundEnabled ? '🔊' : '🔇';
    const btn = document.getElementById('audioBtn');
    const cfgLabel = document.getElementById('cfgAudioLabel');
    if (btn) btn.innerHTML = `<span>${icon}</span> <span>${label}</span>`;
    if (cfgLabel) cfgLabel.textContent = soundEnabled ? 'SOUND EFFEKTE: AKTIV' : 'SOUND EFFEKTE: STUMM';
  }

  // Stardate Berechnung gemäß offizieller LCARS-Formel (thelcars.com)
  function calculateStardate(d = new Date()) {
    const currentHour = d.getHours();
    let dateForCalc = new Date(d);
    if (currentHour === 0) {
      dateForCalc.setDate(dateForCalc.getDate() + 1);
    }
    const firstNum = dateForCalc.getFullYear() - 1946;
    const startOfYear = new Date(dateForCalc.getFullYear(), 0, 1);
    const diffTime = Math.abs(dateForCalc - startOfYear);
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    const calculatedSecondNumber = Math.floor(diffDays * 2.732);
    const secondNum = String(calculatedSecondNumber).padStart(3, '0');
    let finalNum;
    if (currentHour === 0) {
      finalNum = "0";
    } else if (currentHour >= 1 && currentHour <= 9) {
      finalNum = String(currentHour).padStart(2, '0');
    } else if (currentHour >= 10 && currentHour <= 11) {
      finalNum = String(currentHour);
    } else if (currentHour >= 12 && currentHour <= 21) {
      let hour12 = currentHour % 12;
      if (hour12 === 0) hour12 = 12;
      finalNum = String(hour12);
    } else {
      finalNum = String(currentHour);
    }
    return `${firstNum}${secondNum}.${finalNum}`;
  }

  function updateStardate() {
    const stardateStr = calculateStardate();
    const elBanner = document.getElementById('stardateValue');
    if (elBanner) elBanner.textContent = stardateStr;
    const elTop = document.getElementById('topStardateVal');
    if (elTop) elTop.textContent = stardateStr;
  }
  setInterval(updateStardate, 1000);
  updateStardate();

  // Fullscreen Handler
  function toggleFullscreen() {
    playLcarsBeep(1200, 1600);
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(err => {
        console.warn("Fullscreen abgelehnt:", err);
      });
    } else {
      if (document.exitFullscreen) document.exitFullscreen();
    }
  }

  document.addEventListener('fullscreenchange', () => {
    const isFs = !!document.fullscreenElement;
    const icon = document.getElementById('fsIcon');
    const label = document.getElementById('fsLabel');
    if (icon && label) {
      icon.textContent = isFs ? '🗗' : '⛶';
      label.textContent = isFs ? 'FENSTER' : 'VOLLBILD';
    }
  });

  // 10 KATEGORIEN NAVIGATION (OHNE ZAHLEN)
  const CATEGORY_NAMES = {
    'system': 'SYSTEM & SENSOR VERLAUF',
    'services': 'SERVICES & PROZESS-SCANNER',
    'ai': 'LCARS KÜNSTLICHE INTELLIGENZ // BEREICHSÜBERSICHT',
    '9router': '9ROUTER COMM-LINK // SUBRAUM CHAT',
    'hermes': 'HERMES AUTONOMOUS RUNTIME // SYSTEM-AGENTEN',
    'ide': 'GOOGLE ANTIGRAVITY // ENTWICKLUNGSUMGEBUNG',
    'agents': 'LCARS SUBRAUM COMM-LINK // KI-AGENTEN',
    'ai-info': 'KI-INFO // 9ROUTER & NEURAL TELEMETRIE',
    'config': 'SYSTEM CONFIG & FARBMODI',
    'fantasy': 'ESPN FANTASY FOOTBALL // INCOMPLETE PASS',
    'personal': 'LCARS PERSÖNLICHER BEREICH // ÜBERSICHT',
    'solar': 'LCARS ENERGIE-MANAGEMENT // BALKONSOLAR',
    'homeassistant': 'LCARS HAUSSTEUERUNG // HOME ASSISTANT',
    'cycle': 'LCARS BIO-TELEMETRIE // PARTNERINNEN-ZYKLUS',
    'pulsecast': 'LCARS PULSECAST // MEDIA & DOWNLOAD HUB',
    'gemini_live': 'LCARS SUBRAUM COMM // CACTUS NEEDLE 3',
    'devteam': 'DEV-TEAM // KANBAN WORKFLOW ENGINE',
    'knowledge': 'LCARS WISSENSDATENBANK // QA, REVIEWS & ADRS'
  };

  let aiNavExpanded = false;

  function toggleAiNav(forceState = null) {
    aiNavExpanded = (forceState !== null) ? forceState : !aiNavExpanded;
    const subpillar = document.getElementById('ai-subpillar');
    const icon = document.getElementById('aiFoldIcon');
    if (subpillar) {
      subpillar.style.display = aiNavExpanded ? 'flex' : 'none';
    }
    if (icon) {
      icon.textContent = aiNavExpanded ? '▼' : '▶';
    }
  }

  function handleAiPillClick() {
    if (currentCategory === 'ai') {
      toggleAiNav();
    } else {
      toggleAiNav(true);
      if (typeof isUserAllowedSection === 'function' && isUserAllowedSection('ai')) {
        switchCategory('ai');
      } else {
        const aiSubs = ['9router', 'hermes', 'ide', 'ai-info', 'gemini_live'];
        const first = aiSubs.find(s => isUserAllowedSection(s));
        if (first) switchCategory(first);
      }
    }
  }

  async function initAiOverview() {
    try {
      const resp = await fetch('/api/status');
      if (resp.ok) {
        const d = await resp.json();
        const nr = d.nine_router;
        const reqEl = document.getElementById('aiOverviewRequests');
        const stEl = document.getElementById('aiOverviewStatus');
        if (nr) {
          if (reqEl && nr.totals) reqEl.textContent = nr.totals.requests !== undefined ? nr.totals.requests : 0;
          if (stEl) stEl.textContent = (nr.status || 'ONLINE').toUpperCase();
        }
      }
    } catch (e) {
      console.warn('AI Overview status error:', e);
    }
    try {
      const resp = await fetch('/api/hermes/profiles');
      if (resp.ok) {
        const d = await resp.json();
        const countEl = document.getElementById('aiHermesCount');
        if (countEl && d.profiles) {
          countEl.textContent = d.profiles.length + ' Profile aktiv';
        }
      }
    } catch (e) {
      console.warn('AI Overview hermes error:', e);
    }
  }

  let personalNavExpanded = false;

  function togglePersonalNav(forceState = null) {
    personalNavExpanded = (forceState !== null) ? forceState : !personalNavExpanded;
    const subpillar = document.getElementById('personal-subpillar');
    const icon = document.getElementById('personalFoldIcon');
    if (subpillar) {
      subpillar.style.display = personalNavExpanded ? 'flex' : 'none';
    }
    if (icon) {
      icon.textContent = personalNavExpanded ? '▼' : '▶';
    }
  }

  function handlePersonalPillClick() {
    if (currentCategory === 'personal') {
      togglePersonalNav();
    } else {
      togglePersonalNav(true);
      if (typeof isUserAllowedSection === 'function' && isUserAllowedSection('personal')) {
        switchCategory('personal');
      } else {
        const persSubs = ['fantasy', 'solar', 'homeassistant', 'cycle', 'pulsecast'];
        const first = persSubs.find(s => isUserAllowedSection(s));
        if (first) switchCategory(first);
      }
    }
  }

  async function initPersonalOverview() {
    try {
      const resp = await fetch('/api/fantasy');
      if (resp.ok) {
        const d = await resp.json();
        const fTeam = document.getElementById('personalFantasyTeam');
        const fRank = document.getElementById('personalFantasyRank');
        const fMatchup = document.getElementById('personalFantasyMatchup');
        if (fTeam) {
          fTeam.textContent = (d.team_name || 'Norderstedt Railsguns') + (d.league_name ? ' (' + d.league_name + ')' : '');
        }
        if (fRank) {
          const myStanding = (d.standings || []).find(s => s.is_my_team);
          const rec = myStanding ? `(${myStanding.wins}-${myStanding.losses}-${myStanding.ties})` : '';
          fRank.textContent = (d.my_rank ? 'Rang #' + d.my_rank + ' ' : '') + rec;
        }
        if (fMatchup) {
          if (d.matchup) {
            fMatchup.textContent = `${d.matchup.my_score || 0} : ${d.matchup.opp_score || 0} PTS`;
          } else {
            fMatchup.textContent = 'Kein aktives Matchup';
          }
        }
      }
    } catch (e) {
      console.warn('Personal Fantasy overview error:', e);
    }

    try {
      const resp = await fetch('/api/solar/data');
      if (resp.ok) {
        const d = await resp.json();
        const pv = d.pv_power !== undefined ? d.pv_power : (d.production !== undefined ? d.production : '--');
        const bat = d.battery_soc !== undefined ? d.battery_soc : (d.soc !== undefined ? d.soc : '--');
        const house = d.house_consumption !== undefined ? d.house_consumption : (d.consumption !== undefined ? d.consumption : '--');
        const pvEl = document.getElementById('personalSolarPv');
        const batEl = document.getElementById('personalSolarBat');
        const houseEl = document.getElementById('personalSolarHouse');
        if (pvEl) pvEl.textContent = (pv !== '--' ? Math.round(pv) + ' W' : '-- W');
        if (batEl) batEl.textContent = (bat !== '--' ? Math.round(bat) + ' %' : '-- %');
        if (houseEl) houseEl.textContent = (house !== '--' ? Math.round(house) + ' W' : '-- W');
      }
    } catch (e) {
      console.warn('Personal Solar overview error:', e);
    }

    try {
      const resp = await fetch('/api/homeassistant/config');
      if (resp.ok) {
        const d = await resp.json();
        const stEl = document.getElementById('personalHaStatus');
        const entEl = document.getElementById('personalHaEntities');
        if (stEl) {
          stEl.textContent = (d.configured && d.enabled) ? 'ONLINE' : 'INAKTIV';
          stEl.style.color = (d.configured && d.enabled) ? '#44dd88' : '#888';
        }
        if (entEl) {
          entEl.textContent = d.configured ? (d.name || 'Assistant') : 'Nicht konfiguriert';
        }
      }
    } catch (e) {
      console.warn('Personal HA overview error:', e);
    }

    try {
      const resp = await fetch('/api/cycle/partners');
      if (resp.ok) {
        const partners = await resp.json();
        const partner = Array.isArray(partners) && partners.length > 0 ? partners[0] : null;
        const pEl = document.getElementById('personalCyclePartner');
        const dEl = document.getElementById('personalCycleDay');
        const phEl = document.getElementById('personalCyclePhase');
        if (partner) {
          if (pEl) pEl.textContent = partner.name || '--';
          if (dEl) dEl.textContent = (partner.current_cycle_day ? 'Tag ' + partner.current_cycle_day : '--');
          if (phEl) phEl.textContent = partner.current_phase || '--';
        } else {
          if (pEl) pEl.textContent = 'Keine Partnerin';
          if (dEl) dEl.textContent = '--';
          if (phEl) phEl.textContent = 'Nicht konfiguriert';
        }
      }
    } catch (e) {
      console.warn('Personal Cycle overview error:', e);
    }

    try {
      const resp = await fetch('/api/pulsecast/downloads');
      if (resp.ok) {
        const d = await resp.json();
        const active = (d.downloads || []).filter(dl => dl.status === 'downloading' || dl.status === 'running').length;
        const dlEl = document.getElementById('personalPulsecastDownloads');
        if (dlEl) dlEl.textContent = active + ' aktiv (' + (d.downloads || []).length + ' gesamt)';
      }
    } catch (e) {
      console.warn('Personal PulseCast overview error:', e);
    }
  }

  function switchCategory(catId, skipHistory = false) {
    if (catId === 'agents') {
      catId = '9router';
    }

    if (typeof isUserAllowedSection === 'function' && !isUserAllowedSection(catId)) {
      playLcarsBeep(300, 150);
      return;
    }

    if (typeof isCategoryLocked === 'function' && isCategoryLocked(catId)) {
      pendingUnlockCategory = catId;
      openAuthModal();
      return;
    }

    if (catId !== 'pulsecast' && typeof stopPulsecastPolling === 'function') {
      stopPulsecastPolling();
    }
    if (catId !== 'gemini_live' && typeof stopSubspaceLiveAudio === 'function') {
      stopSubspaceLiveAudio();
    }

    playLcarsBeep(980, 1400);
    currentCategory = catId;

    const aiCategories = ['ai', '9router', 'hermes', 'ide', 'agents', 'ai-info', 'gemini_live'];
    const aiBtn = document.getElementById('btn-cat-ai');
    if (aiCategories.includes(catId)) {
      toggleAiNav(true);
      if (aiBtn) {
        if (catId === 'ai') {
          aiBtn.classList.add('active');
          aiBtn.classList.remove('active-parent');
        } else {
          aiBtn.classList.remove('active');
          aiBtn.classList.add('active-parent');
        }
      }
    } else {
      toggleAiNav(false);
      if (aiBtn) {
        aiBtn.classList.remove('active', 'active-parent');
      }
    }

    const personalCategories = ['personal', 'fantasy', 'solar', 'homeassistant', 'cycle', 'pulsecast'];
    const personalBtn = document.getElementById('btn-cat-personal');
    if (personalCategories.includes(catId)) {
      togglePersonalNav(true);
      if (personalBtn) {
        if (catId === 'personal') {
          personalBtn.classList.add('active');
          personalBtn.classList.remove('active-parent');
        } else {
          personalBtn.classList.remove('active');
          personalBtn.classList.add('active-parent');
        }
      }
    } else {
      togglePersonalNav(false);
      if (personalBtn) {
        personalBtn.classList.remove('active', 'active-parent');
      }
    }

    if (!skipHistory) {
      if (typeof categoryHistory !== 'undefined' && typeof categoryHistoryIndex !== 'undefined') {
        if (categoryHistory[categoryHistoryIndex] !== catId) {
          categoryHistory = categoryHistory.slice(0, categoryHistoryIndex + 1);
          categoryHistory.push(catId);
          categoryHistoryIndex = categoryHistory.length - 1;
        }
      }
    }

    document.querySelectorAll('.lcars-pill-btn').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById('btn-cat-' + catId);
    if (activeBtn) activeBtn.classList.add('active');

    document.querySelectorAll('.lcars-section').forEach(sec => sec.classList.remove('active-section'));
    const activeSec = document.getElementById('section-' + catId);
    if (activeSec) activeSec.classList.add('active-section');

    const banner = document.getElementById('bannerSectionTitle');
    if (banner && CATEGORY_NAMES[catId]) {
      banner.textContent = CATEGORY_NAMES[catId];
    }

    // Chart Resizing & Re-Render
    if (catId === 'system') {
      setTimeout(() => {
        if (historyChart) {
          historyChart.resize();
          historyChart.update('none');
        } else {
          initHistoryChart();
        }
      }, 60);
    }
    if (catId === 'services') {
      if (latestDiscoveredServers && latestDiscoveredServers.length > 0) {
        updateServicesCards(latestDiscoveredServers);
      }
    }
    if (catId === 'ai') {
      setTimeout(() => {
        initAiOverview();
      }, 60);
    }
    if (catId === '9router') {
      activeAgentSubgroup = '9router';
      setTimeout(() => {
        loadChatModels();
        const inp = document.getElementById('lcarsChatInput');
        if (inp) inp.focus();
      }, 60);
    }
    if (catId === 'hermes') {
      activeAgentSubgroup = 'hermes';
      setTimeout(() => {
        loadHermesProfiles();
        const inp = document.getElementById('hermesChatInput');
        if (inp) inp.focus();
      }, 60);
    }
    if (catId === 'ide') {
      activeAgentSubgroup = 'ide';
      setTimeout(() => {
        loadIdeConfig();
      }, 60);
    }
    if (catId === 'ai-info') {
      setTimeout(() => {
        initNineRouterCharts();
        initHermesChart();
      }, 60);
    }
    if (catId === 'fantasy') {
      setTimeout(() => {
        fantasyCountdownSeconds = 30;
        updateFantasyCountdownUI();
        loadFantasyData(false);
      }, 60);
    }
    if (catId === 'personal') {
      setTimeout(() => {
        initPersonalOverview();
      }, 60);
    }
    if (catId === 'solar') {
      setTimeout(() => {
        solarCountdownSeconds = 15;
        updateSolarCountdownUI();
        loadSolarData(false);
      }, 60);
    }
    if (catId === 'homeassistant') {
      setTimeout(() => {
        haCountdownSeconds = 15;
        updateHaCountdownUI();
        loadHomeAssistantData(false);
      }, 60);
    }
    if (catId === 'cycle') {
      setTimeout(() => {
        loadCycleData();
      }, 60);
    }
    if (catId === 'pulsecast') {
      setTimeout(() => {
        initPulsecastSection();
      }, 60);
    }
    if (catId === 'gemini_live') {
      setTimeout(() => {
        initGeminiLiveSection();
      }, 60);
    }
    if (catId === 'devteam') {
      fetchDevteamData(true);
    }
    if (catId === 'knowledge') {
      fetchKnowledgeData(true);
    }
    if (catId === 'research') {
      fetchResearchData(true);
    }
  }

  // ESPN FANTASY CONTROLLER
  let isFantasyLoading = false;
  let fantasyCountdownSeconds = 30;

  function updateFantasyCountdownUI() {
    const badge = document.getElementById('fantasyCountdownBadge');
    if (!badge) return;
    if (isFantasyLoading) {
      badge.textContent = '● SYNC...';
      badge.style.color = 'var(--c-gold)';
      badge.style.borderColor = 'var(--c-gold)';
      badge.style.background = 'rgba(237, 179, 120, 0.15)';
    } else {
      badge.textContent = `● REFRESH IN ${fantasyCountdownSeconds}S`;
      badge.style.color = '#44dd88';
      badge.style.borderColor = 'rgba(68, 221, 136, 0.4)';
      badge.style.background = 'rgba(68, 221, 136, 0.15)';
    }
  }

  window.testFantasyFlash = async function(e) {
    if (e) e.preventDefault();
    const btn = document.getElementById('fantasyTestFlashBtn');
    if (!btn) return;
    const origText = btn.textContent;
    btn.textContent = '⚡ FLASHING...';
    btn.style.color = 'var(--c-gold)';
    btn.style.borderColor = 'var(--c-gold)';
    btn.disabled = true;
    try {
      const resp = await fetch('/api/fantasy/test-flash');
      const data = await resp.json();
      if (data.success) {
        btn.textContent = '✓ FLASH OK';
        btn.style.color = '#44dd88';
        btn.style.borderColor = '#44dd88';
      } else {
        btn.textContent = '✗ FEHLER';
        btn.style.color = 'var(--c-red)';
        btn.style.borderColor = 'var(--c-red)';
      }
    } catch (err) {
      btn.textContent = '✗ NETZWERK';
      btn.style.color = 'var(--c-red)';
      btn.style.borderColor = 'var(--c-red)';
    }
    setTimeout(() => {
      btn.textContent = origText;
      btn.style.color = 'var(--c-primary)';
      btn.style.borderColor = 'rgba(235,148,58,0.4)';
      btn.disabled = false;
    }, 2200);
  };

  // ESPN KI-MANAGER STEUERUNG & SETTINGS
  let currentFantasyMode = 'manual';
  let currentFantasyRiskLevel = 3;
  let currentFlashEnabled = true;

  const FANTASY_RISK_LABELS = {
    1: '1: ULTRA-KONSERVATIV (FLOOR)',
    2: '2: KONSERVATIV',
    3: '3: AUSGEWOGEN (STANDARD)',
    4: '4: OFFENSIV (CEILING)',
    5: '5: BOOM-OR-BUST (MAX UPSIDE)'
  };

  function updateFantasyModeButtons(mode) {
    const modes = ['manual', 'semi', 'full'];
    modes.forEach(m => {
      const btn = document.getElementById(`btn-fantasy-mode-${m}`);
      if (!btn) return;
      if (m === mode) {
        btn.classList.add('active');
        if (m === 'manual') {
          btn.style.background = 'var(--c-butterscotch)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(218,165,32,0.4)';
        } else if (m === 'semi') {
          btn.style.background = 'var(--c-gold)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(237,179,120,0.6)';
        } else if (m === 'full') {
          btn.style.background = 'var(--c-primary)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 12px rgba(235,148,58,0.7)';
        }
      } else {
        btn.classList.remove('active');
        btn.style.background = 'transparent';
        btn.style.color = '#888';
        btn.style.boxShadow = 'none';
      }
    });
  }

  function updateFantasyRiskButtons(level) {
    const lvl = parseInt(level, 10) || 3;
    currentFantasyRiskLevel = lvl;
    for (let i = 1; i <= 5; i++) {
      const btn = document.getElementById(`btn-fantasy-risk-${i}`);
      if (!btn) continue;
      if (i === lvl) {
        btn.classList.add('active');
        if (i === 1 || i === 2) {
          btn.style.background = 'var(--c-blue, #6688cc)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(102,136,204,0.6)';
        } else if (i === 3) {
          btn.style.background = 'var(--c-butterscotch, #cc9933)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(218,165,32,0.4)';
        } else if (i === 4) {
          btn.style.background = 'var(--c-primary, #eb943a)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(235,148,58,0.6)';
        } else if (i === 5) {
          btn.style.background = 'var(--c-red, #eb4444)';
          btn.style.color = '#fff';
          btn.style.boxShadow = '0 0 12px rgba(235,68,68,0.7)';
        }
      } else {
        btn.classList.remove('active');
        btn.style.background = 'transparent';
        btn.style.color = '#888';
        btn.style.boxShadow = 'none';
      }
    }
    const statusEl = document.getElementById('fantasyAiRiskStatus');
    if (statusEl) {
      statusEl.textContent = `STRATEGIE: ${FANTASY_RISK_LABELS[lvl] || ('STUFE ' + lvl)}`;
    }
  }

  let currentFantasyInterval = 4;

  function updateFantasyIntervalButtons(hours) {
    const h = parseInt(hours, 10) || 4;
    currentFantasyInterval = h;
    [4, 8, 12, 24].forEach(val => {
      const btn = document.getElementById(`btn-fantasy-interval-${val}`);
      if (!btn) return;
      if (val === h) {
        btn.classList.add('active');
        btn.style.background = 'var(--c-butterscotch, #cc9933)';
        btn.style.color = '#000';
        btn.style.boxShadow = '0 0 10px rgba(218,165,32,0.4)';
      } else {
        btn.classList.remove('active');
        btn.style.background = 'transparent';
        btn.style.color = '#888';
        btn.style.boxShadow = 'none';
      }
    });
    const intervalStatusEl = document.getElementById('fantasyAiIntervalStatus');
    if (intervalStatusEl) {
      intervalStatusEl.textContent = `INTERVALL: ${h}H + KICKOFF (T-20M)`;
    }
  }

  window.setFantasyInterval = async function(hours) {
    const h = parseInt(hours, 10);
    if (![4, 8, 12, 24].includes(h)) return;
    updateFantasyIntervalButtons(h);
    try {
      const resp = await fetch('/api/espn/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interval_hours: h })
      });
      const res = await resp.json();
      if (res.status === 'ok' || res.interval_hours) {
        updateFantasyIntervalButtons(res.interval_hours || h);
        loadFantasyData(false);
      }
    } catch (e) {
      console.error('Fehler beim Setzen des Fantasy-Intervalls:', e);
    }
  };

  window.setFantasyRiskLevel = async function(level) {
    const lvl = parseInt(level, 10);
    if (!lvl || lvl < 1 || lvl > 5) return;
    updateFantasyRiskButtons(lvl);
    try {
      const resp = await fetch('/api/espn/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ risk_level: lvl })
      });
      const res = await resp.json();
      if (res.status === 'ok' || res.risk_level) {
        updateFantasyRiskButtons(res.risk_level || lvl);
      }
    } catch (e) {
      console.error('Fehler beim Setzen des Risk-Levels:', e);
    }
  };

  function updateFantasyFlashToggle(enabled) {
    currentFlashEnabled = Boolean(enabled);
    const btn = document.getElementById('fantasyFlashToggleBtn');
    if (!btn) return;
    if (currentFlashEnabled) {
      btn.textContent = '⚡ FLASH: AN';
      btn.style.color = '#44dd88';
      btn.style.background = 'rgba(68,221,136,0.15)';
      btn.style.borderColor = 'rgba(68,221,136,0.5)';
      btn.title = 'Flash-Lichtsignal bei Score aktiviert (Klicken zum Ausschalten)';
    } else {
      btn.textContent = '⚡ FLASH: AUS';
      btn.style.color = '#888';
      btn.style.background = 'rgba(255,255,255,0.05)';
      btn.style.borderColor = 'rgba(255,255,255,0.15)';
      btn.title = 'Flash-Lichtsignal deaktiviert (Klicken zum Einschalten)';
    }
  }

  window.toggleFantasyFlash = async function(event) {
    if (event) event.stopPropagation();
    const nextState = !currentFlashEnabled;
    updateFantasyFlashToggle(nextState);
    try {
      const resp = await fetch('/api/espn/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ flash_enabled: nextState })
      });
      const res = await resp.json();
      if (res.status === 'ok' || res.flash_enabled !== undefined) {
        updateFantasyFlashToggle(res.flash_enabled !== undefined ? res.flash_enabled : nextState);
      }
    } catch (e) {
      console.error('Fehler beim Umschalten des Flash-Signals:', e);
    }
  };

  function updateFantasyAiStats(stats) {
    const modelEl = document.getElementById('fantasyAiModelText');
    if (!modelEl || !stats) return;
    const tokens = (stats.last_token_usage && stats.last_token_usage.total_tokens) ? stats.last_token_usage.total_tokens : 1200;
    const tokenStr = tokens >= 1000 ? (tokens / 1000).toFixed(1) + 'k' : tokens;
    const cost = stats.estimated_cost_usd || 0.0002;
    const costCt = (cost * 100).toFixed(2).replace('.', ',');
    modelEl.textContent = `Gemini 3.8 Flash | ~${tokenStr} Tokens / Run (<${costCt}ct)`;
  }

  window.setFantasyMode = async function(mode) {
    if (!mode) return;
    updateFantasyModeButtons(mode);
    try {
      const resp = await fetch('/api/espn/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode })
      });
      const res = await resp.json();
      if (res.status === 'ok' || res.success) {
        currentFantasyMode = res.mode || mode;
        updateFantasyModeButtons(currentFantasyMode);
        loadFantasyData(false);
      } else {
        console.error('Fehler beim Setzen des Fantasy-Modus:', res.message || res.error);
        loadFantasyData(false);
      }
    } catch (e) {
      console.error('Netzwerkfehler beim Setzen des Fantasy-Modus:', e);
      loadFantasyData(false);
    }
  };

  window.applyFantasyProposal = async function(proposalId) {
    if (!proposalId) return;
    const applyBtn = document.getElementById('fantasyProposalApplyBtn');
    const dismissBtn = document.getElementById('fantasyProposalDismissBtn');
    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.textContent = '⚡ AUSFÜHREN...';
      applyBtn.style.opacity = '0.7';
    }
    if (dismissBtn) dismissBtn.disabled = true;

    try {
      const resp = await fetch(`/api/espn/proposals/${encodeURIComponent(proposalId)}/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const res = await resp.json();
      if (res.success) {
        if (applyBtn) {
          applyBtn.textContent = '✓ FREIGEGEBEN';
          applyBtn.style.background = '#44dd88';
          applyBtn.style.color = '#000';
        }
        setTimeout(() => {
          loadFantasyData(true);
        }, 1200);
      } else {
        alert(`Fehler beim Ausführen des Vorschlags: ${res.error || res.message || 'Safeguard-Abweisung'}`);
        if (applyBtn) {
          applyBtn.disabled = false;
          applyBtn.textContent = '⚡ FREIGEBEN';
          applyBtn.style.opacity = '1';
        }
        if (dismissBtn) dismissBtn.disabled = false;
      }
    } catch (e) {
      alert(`Netzwerkfehler: ${e.message}`);
      if (applyBtn) {
        applyBtn.disabled = false;
        applyBtn.textContent = '⚡ FREIGEBEN';
        applyBtn.style.opacity = '1';
      }
      if (dismissBtn) dismissBtn.disabled = false;
    }
  };

  window.dismissFantasyProposal = async function(proposalId) {
    if (!proposalId) return;
    const banner = document.getElementById('fantasyProposalBanner');
    if (banner) banner.style.display = 'none';
    try {
      await fetch(`/api/espn/proposals/${encodeURIComponent(proposalId)}/dismiss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      loadFantasyData(true);
    } catch (e) {
      console.error('Fehler beim Verwerfen des Vorschlags:', e);
      loadFantasyData(false);
    }
  };

  let currentFantasyLog = [];
  let currentFantasyLogFilter = 'all';
  let botNextRunSeconds = null;
  let botNextRunType = 'interval';
  let botNextKickoffSlot = null;

  function updateFantasyBotStatus(status) {
    if (!status) return;
    const badge = document.getElementById('fantasyBotStatusBadge');
    const lastRunEl = document.getElementById('fantasyBotLastRun');
    const lastActionEl = document.getElementById('fantasyBotLastAction');
    const lastReasonEl = document.getElementById('fantasyBotLastReason');
    const lastReasonWrap = document.getElementById('fantasyBotLastReasonWrapper');

    if (badge) {
      if (status.mode === 'full') {
        badge.textContent = 'BOT: VOLLAUTONOM (FULL)';
        badge.style.background = 'var(--c-primary)';
        badge.style.color = '#000';
      } else if (status.mode === 'semi') {
        badge.textContent = 'BOT: SEMI-AUTONOM (SEMI)';
        badge.style.background = 'var(--c-gold)';
        badge.style.color = '#000';
      } else {
        badge.textContent = 'BOT: MANUELL (PAUSIERT)';
        badge.style.background = '#555';
        badge.style.color = '#aaa';
      }
    }

    if (lastRunEl) {
      lastRunEl.textContent = status.last_run_datetime || 'Noch kein Lauf';
    }

    if (status.next_run_in_seconds !== null && status.next_run_in_seconds !== undefined) {
      botNextRunSeconds = parseInt(status.next_run_in_seconds, 10);
    } else {
      botNextRunSeconds = null;
    }
    botNextRunType = status.next_run_type || 'interval';
    botNextKickoffSlot = status.next_kickoff_slot || null;
    updateFantasyBotCountdownUI(status.next_run_text);

    if (lastActionEl) {
      lastActionEl.textContent = status.last_action || 'Kader geprüft: Keine Änderungen erforderlich';
    }

    if (lastReasonEl && lastReasonWrap) {
      if (status.last_reason && String(status.last_reason).trim()) {
        lastReasonEl.textContent = status.last_reason;
        lastReasonWrap.style.display = 'inline-flex';
      } else {
        lastReasonWrap.style.display = 'none';
      }
    }

    const slotChecksEl = document.getElementById('fantasyBotSlotChecks');
    if (slotChecksEl) {
      if (status.mode === 'manual') {
        slotChecksEl.textContent = 'PAUSIERT';
        slotChecksEl.style.color = '#888';
      } else {
        slotChecksEl.textContent = 'AKTIV (T-20M)';
        slotChecksEl.style.color = '#44dd88';
      }
    }

    if (status.interval_hours) {
      updateFantasyIntervalButtons(status.interval_hours);
    }
  }

  function updateFantasyBotCountdownUI(fallbackText) {
    const nextRunEl = document.getElementById('fantasyBotNextRun');
    if (!nextRunEl) return;
    if (currentFantasyMode === 'manual') {
      nextRunEl.textContent = 'Pausiert (Modus Manuell)';
      nextRunEl.style.color = '#888';
    } else if (botNextRunSeconds !== null) {
      if (botNextRunSeconds <= 0) {
        nextRunEl.textContent = '● KI-CHECK LÄUFT...';
        nextRunEl.style.color = 'var(--c-gold)';
      } else {
        const hours = Math.floor(botNextRunSeconds / 3600);
        const mins = Math.floor((botNextRunSeconds % 3600) / 60);
        const secs = botNextRunSeconds % 60;
        let timeStr = '';
        if (hours > 0) {
          timeStr = `${hours}h ${mins}m`;
        } else if (mins > 0) {
          timeStr = `${mins}m ${secs < 10 ? '0' : ''}${secs}s`;
        } else {
          timeStr = `${secs}s`;
        }
        if (botNextRunType === 'kickoff_slot' && botNextKickoffSlot) {
          nextRunEl.textContent = `In ${timeStr} (Kickoff ${botNextKickoffSlot})`;
        } else {
          nextRunEl.textContent = `In ${timeStr}`;
        }
        nextRunEl.style.color = '#44dd88';
      }
    } else {
      nextRunEl.textContent = fallbackText || 'In Kürze...';
      nextRunEl.style.color = '#44dd88';
    }
  }

  function updateFantasyDecisionLog(history) {
    if (Array.isArray(history)) {
      currentFantasyLog = history;
    }
    renderFantasyDecisionLogTable();
  }

  window.filterFantasyLog = function(filter) {
    currentFantasyLogFilter = filter;
    ['all', 'moves', 'checks', 'config'].forEach(f => {
      const btn = document.getElementById(`flt-${f}`);
      if (!btn) return;
      if (f === filter) {
        btn.classList.add('active');
        btn.style.background = 'var(--c-butterscotch)';
        btn.style.color = '#000';
      } else {
        btn.classList.remove('active');
        btn.style.background = 'transparent';
        btn.style.color = '#888';
      }
    });
    renderFantasyDecisionLogTable();
  };

  function renderFantasyDecisionLogTable() {
    const tbody = document.getElementById('fantasyDecisionLogTableBody');
    const badgeCount = document.getElementById('fantasyLogCountBadge');
    if (!tbody) return;

    const escapeFn = typeof escapeHtml === 'function' ? escapeHtml : (s => String(s || ''));

    let items = (currentFantasyLog || []).slice().reverse(); // neueste zuerst
    if (currentFantasyLogFilter === 'moves') {
      items = items.filter(e => ['AUTO_MOVE_EXECUTED', 'TRANSACTION_EXECUTED', 'PROPOSAL_CREATED', 'PROPOSAL_APPLIED', 'AUTO_MOVE_FAILED', 'TRANSACTION_FAILED'].includes(e.action));
    } else if (currentFantasyLogFilter === 'checks') {
      items = items.filter(e => e.action === 'ROSTER_CHECK' || e.action === 'ROSTER_ANALYSIS_MANUAL' || e.action === 'MANUAL_CHECK_RESULT');
    } else if (currentFantasyLogFilter === 'config') {
      items = items.filter(e => ['MODE_CHANGE', 'RISK_LEVEL_CHANGE', 'FLASH_TOGGLE', 'INTERVAL_CHANGE'].includes(e.action));
    }

    if (badgeCount) {
      badgeCount.textContent = `${items.length} EINTRÄGE`;
    }

    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="padding:1.2rem; text-align:center; color:#888;">Keine Einträge für diese Filterauswahl vorhanden.</td></tr>';
      return;
    }

    let html = '';
    items.forEach(e => {
      const meta = e.metadata || {};
      const action = e.action || '';
      let typeBadge = '<span style="background:#555; color:#fff; font-size:0.7rem; font-weight:700; padding:2px 6px; border-radius:6px;">INFO</span>';
      
      if (action === 'AUTO_MOVE_EXECUTED' || action === 'TRANSACTION_EXECUTED' || action === 'PROPOSAL_APPLIED') {
        typeBadge = '<span style="background:#44dd88; color:#000; font-size:0.7rem; font-weight:800; padding:2px 6px; border-radius:6px;">WECHSEL</span>';
      } else if (action === 'PROPOSAL_CREATED') {
        typeBadge = '<span style="background:var(--c-gold); color:#000; font-size:0.7rem; font-weight:800; padding:2px 6px; border-radius:6px;">VORSCHLAG</span>';
      } else if (action === 'PROPOSAL_DISMISSED') {
        typeBadge = '<span style="background:#777; color:#fff; font-size:0.7rem; font-weight:700; padding:2px 6px; border-radius:6px;">ABGELEHNT</span>';
      } else if (action === 'ROSTER_CHECK' || action === 'ROSTER_ANALYSIS_MANUAL' || action === 'MANUAL_CHECK_RESULT') {
        typeBadge = '<span style="background:var(--c-secondary); color:#000; font-size:0.7rem; font-weight:700; padding:2px 6px; border-radius:6px;">KADER-CHECK</span>';
      } else if (action === 'MODE_CHANGE' || action === 'RISK_LEVEL_CHANGE' || action === 'FLASH_TOGGLE' || action === 'INTERVAL_CHANGE') {
        typeBadge = '<span style="background:var(--c-primary); color:#000; font-size:0.7rem; font-weight:700; padding:2px 6px; border-radius:6px;">EINSTELLUNG</span>';
      } else if (action === 'AUTO_MOVE_FAILED' || action === 'TRANSACTION_FAILED' || action === 'TRANSACTION_ERROR' || !e.success) {
        typeBadge = '<span style="background:var(--c-red); color:#fff; font-size:0.7rem; font-weight:800; padding:2px 6px; border-radius:6px;">FEHLER</span>';
      }

      let modeLabel = (e.mode || 'manual').toUpperCase();
      if (modeLabel === 'FULL') modeLabel = '<span style="color:var(--c-primary); font-weight:700;">FULL</span>';
      else if (modeLabel === 'SEMI') modeLabel = '<span style="color:var(--c-gold); font-weight:700;">SEMI</span>';
      else modeLabel = '<span style="color:#888;">MANUELL</span>';

      let detailsHtml = `<div style="font-weight:600; color:#fff; margin-bottom:2px;">${escapeFn(e.details || action)}</div>`;
      
      if (meta.player_in && meta.player_out) {
        const gainStr = meta.projected_gain != null ? ` | Erwartet: <strong>+${Number(meta.projected_gain).toFixed(1)} PTS</strong>` : '';
        detailsHtml += `<div style="font-size:0.78rem; color:var(--c-gold); margin-top:2px;">
          🔄 Tausch: <strong>${escapeFn(meta.player_in)}</strong> (Bank ➔ Start) für <strong>${escapeFn(meta.player_out)}</strong>${gainStr}
        </div>`;
      }

      const reason = meta.reason || meta.assessment;
      if (reason && String(reason).trim()) {
        detailsHtml += `<div style="font-size:0.76rem; color:var(--c-butterscotch); margin-top:2px; font-style:italic;">
          💡 Begründung: ${escapeFn(reason)}
        </div>`;
      }

      html += `<tr style="border-bottom:1px solid rgba(255,255,255,0.06); font-family:var(--mono-family);">
        <td style="padding:0.45rem 0.5rem; color:#aaa; font-size:0.78rem; white-space:nowrap; vertical-align:top;">${escapeFn(e.datetime || '')}</td>
        <td style="padding:0.45rem 0.5rem; vertical-align:top;">${typeBadge}</td>
        <td style="padding:0.45rem 0.5rem; vertical-align:top; font-size:0.75rem;">${modeLabel}</td>
        <td style="padding:0.45rem 0.5rem; vertical-align:top;">${detailsHtml}</td>
      </tr>`;
    });

    tbody.innerHTML = html;
  }

  window.triggerFantasyBotCheck = async function() {
    const btn = document.getElementById('btnFantasyRunNow');
    const origText = btn ? btn.textContent : '';
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⚡ PRÜFE...';
      btn.style.opacity = '0.7';
    }

    try {
      const resp = await fetch('/api/espn/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await resp.json();
      if (btn) {
        btn.textContent = '✓ FERTIG';
        btn.style.background = '#44dd88';
        btn.style.color = '#000';
      }
      setTimeout(() => {
        loadFantasyData(true);
      }, 500);
    } catch (e) {
      console.error('Fehler bei manueller Bot-Prüfung:', e);
      if (btn) {
        btn.textContent = '✗ FEHLER';
        btn.style.background = 'var(--c-red)';
        btn.style.color = '#fff';
      }
    } finally {
      setTimeout(() => {
        if (btn) {
          btn.disabled = false;
          btn.textContent = origText || '⚡ JETZT PRÜFEN';
          btn.style.background = 'var(--c-gold)';
          btn.style.color = '#000';
          btn.style.opacity = '1';
        }
      }, 2500);
    }
  };

  async function loadFantasyData(force = false) {
    if (isFantasyLoading) return;
    isFantasyLoading = true;
    updateFantasyCountdownUI();

    try {
      const url = force ? '/api/fantasy/refresh' : '/api/fantasy';
      const resp = await fetch(url);
      const data = await resp.json();

      if (data.status === 'error') {
        const badge = document.getElementById('fantasyCountdownBadge');
        if (badge) {
          badge.textContent = '● FEHLER BEIM ABRUF';
          badge.style.color = 'var(--c-red)';
          badge.style.borderColor = 'var(--c-red)';
        }
        isFantasyLoading = false;
        return;
      }

      // Modus-Button anhand von data.mode markieren
      if (data.mode) {
        currentFantasyMode = data.mode;
        updateFantasyModeButtons(data.mode);
      }
      if (data.risk_level !== undefined) {
        updateFantasyRiskButtons(data.risk_level);
      }
      if (data.interval_hours !== undefined) {
        updateFantasyIntervalButtons(data.interval_hours);
      }
      if (data.flash_enabled !== undefined) {
        updateFantasyFlashToggle(data.flash_enabled);
      }
      if (data.ai_stats) {
        updateFantasyAiStats(data.ai_stats);
      }
      if (data.bot_status) {
        updateFantasyBotStatus(data.bot_status);
      }
      if (data.decision_history) {
        updateFantasyDecisionLog(data.decision_history);
      }

      // Aktiven Vorschlag rendern
      const banner = document.getElementById('fantasyProposalBanner');
      if (banner) {
        const prop = data.active_proposal;
        if (prop && prop.status === 'pending') {
          banner.style.display = 'block';
          const reasonEl = document.getElementById('fantasyProposalReason');
          const detailsEl = document.getElementById('fantasyProposalDetails');
          const confEl = document.getElementById('fantasyProposalConfidence');
          const applyBtn = document.getElementById('fantasyProposalApplyBtn');
          const dismissBtn = document.getElementById('fantasyProposalDismissBtn');

          if (reasonEl) reasonEl.textContent = prop.reason || 'Strategische Aufstellungsoptimierung empfohlen.';

          const escapeFn = typeof escapeHtml === 'function' ? escapeHtml : (s => String(s || ''));
          let detailsText = '';
          if (prop.player_in_name && prop.player_out_name) {
            detailsText = `🔄 TAUSCH: <strong>${escapeFn(prop.player_in_name)}</strong> (Bank ➔ Start) für <strong>${escapeFn(prop.player_out_name)}</strong> (Start ➔ Bank)`;
          } else if (prop.moves && prop.moves.length > 0) {
            detailsText = `🔄 TAUSCH: ${prop.moves.length} Spielerwechsel`;
          }
          if (prop.projected_gain != null && prop.projected_gain !== 0) {
            const gainSign = prop.projected_gain > 0 ? '+' : '';
            detailsText += ` | Erwarteter Zuwachs: ${gainSign}${Number(prop.projected_gain).toFixed(1)} PTS`;
          }
          if (detailsEl) detailsEl.innerHTML = detailsText;

          if (confEl) {
            const confPct = Math.round((prop.confidence || 0.9) * 100);
            confEl.textContent = `KONFIDENZ: ${confPct}%`;
          }

          if (applyBtn) {
            applyBtn.disabled = false;
            applyBtn.textContent = '⚡ FREIGEBEN';
            applyBtn.style.opacity = '1';
            applyBtn.style.background = '#44dd88';
            applyBtn.style.color = '#000';
            applyBtn.onclick = () => applyFantasyProposal(prop.id);
          }
          if (dismissBtn) {
            dismissBtn.disabled = false;
            dismissBtn.textContent = '✕ ABLEHNEN';
            dismissBtn.onclick = () => dismissFantasyProposal(prop.id);
          }
        } else {
          banner.style.display = 'none';
        }
      }

      // League & Team info
      if (document.getElementById('fantasySectionTitle') && data.league_name) {
        document.getElementById('fantasySectionTitle').textContent = `LCARS SUBRAUM RELAY // ${data.league_name.toUpperCase()} LIGA`;
      }
      if (document.getElementById('fantasyCardTeamName')) {
        document.getElementById('fantasyCardTeamName').textContent = data.team_name || 'NORDERSTEDT RAILSGUNS';
      }
      if (document.getElementById('fantasyCardLeagueName')) {
        document.getElementById('fantasyCardLeagueName').textContent = data.league_name || 'Incomplete Pass';
      }
      if (document.getElementById('fantasyCardRank')) {
        document.getElementById('fantasyCardRank').textContent = `RANG #${data.my_rank || '--'}`;
      }

      // My team record & points in standings
      const myTeamStanding = (data.standings || []).find(s => s.is_my_team);
      if (myTeamStanding) {
        if (document.getElementById('fantasyCardRecord')) {
          document.getElementById('fantasyCardRecord').textContent = `(${myTeamStanding.wins}-${myTeamStanding.losses}-${myTeamStanding.ties})`;
        }
        if (document.getElementById('fantasyCardTotalPoints')) {
          document.getElementById('fantasyCardTotalPoints').textContent = `${Number(myTeamStanding.points_for).toFixed(1)} PTS`;
        }
      }

      if (document.getElementById('fantasyCardWeek')) {
        document.getElementById('fantasyCardWeek').textContent = `WEEK ${data.current_week || 1}`;
      }

      // Matchup
      if (data.matchup) {
        const m = data.matchup;
        if (document.getElementById('fantasyMatchupWeekBadge')) {
          document.getElementById('fantasyMatchupWeekBadge').textContent = `WEEK ${m.week}`;
        }
        if (document.getElementById('fantasyMatchupMyName')) {
          document.getElementById('fantasyMatchupMyName').textContent = m.my_team.name;
        }
        if (document.getElementById('fantasyMatchupMyScore')) {
          document.getElementById('fantasyMatchupMyScore').textContent = Number(m.my_team.score).toFixed(1);
        }
        if (document.getElementById('fantasyMatchupMyProj')) {
          document.getElementById('fantasyMatchupMyProj').textContent = m.my_team.projected != null ? Number(m.my_team.projected).toFixed(1) : '--';
        }
        if (document.getElementById('fantasyMatchupMyWinProb')) {
          document.getElementById('fantasyMatchupMyWinProb').textContent = m.my_team.win_prob;
        }

        if (document.getElementById('fantasyMatchupOppName')) {
          document.getElementById('fantasyMatchupOppName').textContent = m.opponent.name;
        }
        if (document.getElementById('fantasyMatchupOppScore')) {
          document.getElementById('fantasyMatchupOppScore').textContent = Number(m.opponent.score).toFixed(1);
        }
        if (document.getElementById('fantasyMatchupOppProj')) {
          document.getElementById('fantasyMatchupOppProj').textContent = m.opponent.projected != null ? Number(m.opponent.projected).toFixed(1) : '--';
        }
        if (document.getElementById('fantasyMatchupOppWinProb')) {
          document.getElementById('fantasyMatchupOppWinProb').textContent = m.opponent.win_prob;
        }

        // Win prob bar
        if (document.getElementById('fantasyProbLabelMy')) {
          document.getElementById('fantasyProbLabelMy').textContent = `${m.my_team.name}: ${m.my_team.win_prob}%`;
        }
        if (document.getElementById('fantasyProbLabelOpp')) {
          document.getElementById('fantasyProbLabelOpp').textContent = `${m.opponent.name}: ${m.opponent.win_prob}%`;
        }
        if (document.getElementById('fantasyProbBarMy')) {
          document.getElementById('fantasyProbBarMy').style.width = `${Math.max(5, Math.min(95, m.my_team.win_prob))}%`;
        }
        if (document.getElementById('fantasyProbBarOpp')) {
          document.getElementById('fantasyProbBarOpp').style.width = `${Math.max(5, Math.min(95, m.opponent.win_prob))}%`;
        }
      }

      // Roster Table
      const rosterBody = document.getElementById('fantasyRosterBody');
      if (rosterBody && data.roster) {
        let html = '';
        let benchStarted = false;
        data.roster.forEach(p => {
          if (!p.is_starter && !benchStarted) {
            benchStarted = true;
            html += `<tr style="border-top:2px solid var(--c-primary); background:rgba(255,255,255,0.03);">
              <td colspan="6" style="padding:0.4rem; font-size:0.75rem; color:var(--c-gold); font-weight:700; letter-spacing:0.08em;">-- BENCH & RESERVES --</td>
            </tr>`;
          }

          let injBadge = '<span style="color:#44dd88; font-size:0.75rem;">AKTIV</span>';
          if (p.injury === 'QUESTIONABLE') {
            injBadge = '<span style="color:var(--c-gold); font-weight:700; font-size:0.75rem;">QUESTIONABLE</span>';
          } else if (p.injury === 'DOUBTFUL') {
            injBadge = '<span style="color:var(--c-butterscotch); font-weight:700; font-size:0.75rem;">DOUBTFUL</span>';
          } else if (p.injury === 'OUT') {
            injBadge = '<span style="color:var(--c-red); font-weight:700; font-size:0.75rem;">OUT</span>';
          } else if (p.injury === 'INJURY_RESERVE') {
            injBadge = '<span style="color:var(--c-secondary); font-weight:700; font-size:0.75rem;">IR</span>';
          }

          const slotColor = p.is_starter ? 'var(--c-primary)' : '#888';
          const pointsColor = (p.actual > 0) ? 'var(--c-gold)' : '#aaa';
          const isRecentScore = !!p.is_recently_scored;
          const rowClass = isRecentScore ? 'class="player-scored-highlight"' : '';
          const scoreBadge = isRecentScore 
            ? `<span style="display:inline-block; margin-left:6px; background:#44dd88; color:#000; font-weight:800; font-size:0.7rem; padding:1px 5px; border-radius:4px; vertical-align:middle;" title="Punkte vor ca. ${p.gain_minutes_ago || 1} Min. erhalten">▲ +${p.score_gain || ''}</span>` 
            : '';

          html += `<tr ${rowClass} style="border-bottom:1px solid rgba(255,255,255,0.06); font-family:var(--mono-family);">
            <td style="padding:0.4rem 0.3rem; font-weight:700; color:${slotColor};">${p.slot}</td>
            <td style="padding:0.4rem 0.4rem; font-family:var(--font-family); font-weight:600; color:#fff;">${p.name}${scoreBadge}</td>
            <td style="padding:0.4rem 0.3rem; color:var(--c-blue);">${p.pro_team}</td>
            <td style="padding:0.4rem 0.3rem;">${injBadge}</td>
            <td style="padding:0.4rem 0.4rem; text-align:right; color:#888;">${p.projected != null ? Number(p.projected).toFixed(1) : '--'}</td>
            <td style="padding:0.4rem 0.4rem; text-align:right; font-weight:700; color:${isRecentScore ? '#44dd88' : pointsColor}; font-size:0.95rem;">${Number(p.actual).toFixed(1)}</td>
          </tr>`;
        });
        rosterBody.innerHTML = html;
      }

      // Standings Table
      const standingsBody = document.getElementById('fantasyStandingsBody');
      if (standingsBody && data.standings) {
        let html = '';
        data.standings.forEach((s, idx) => {
          const isMe = s.is_my_team;
          const rowBg = isMe ? 'background:rgba(235,148,58,0.18); border-left:3px solid var(--c-primary);' : 'border-bottom:1px solid rgba(255,255,255,0.06);';
          const nameColor = isMe ? 'var(--c-gold)' : '#fff';
          const fontW = isMe ? 'font-weight:700;' : '';

          html += `<tr style="${rowBg} font-family:var(--mono-family);">
            <td style="padding:0.4rem 0.3rem; color:var(--c-primary); font-weight:700;">#${idx + 1}</td>
            <td style="padding:0.4rem 0.4rem; font-family:var(--font-family); ${fontW} color:${nameColor};">
              ${s.name} ${isMe ? '<span style="font-size:0.75rem; color:var(--c-primary); margin-left:4px;">★ MEIN TEAM</span>' : ''}
            </td>
            <td style="padding:0.4rem 0.4rem; text-align:center; color:#ccc;">${s.wins}-${s.losses}-${s.ties}</td>
            <td style="padding:0.4rem 0.4rem; text-align:right; font-weight:700; color:var(--c-blue);">${Number(s.points_for).toFixed(1)}</td>
            <td style="padding:0.4rem 0.4rem; text-align:right; color:#888;">${Number(s.points_against).toFixed(1)}</td>
          </tr>`;
        });
        standingsBody.innerHTML = html;
      }

    } catch (e) {
      console.error('ESPN Fantasy Ladefehler:', e);
      const badge = document.getElementById('fantasyCountdownBadge');
      if (badge) {
        badge.textContent = '● FEHLER BEIM ABRUF';
        badge.style.color = 'var(--c-red)';
        badge.style.borderColor = 'var(--c-red)';
      }
    } finally {
      isFantasyLoading = false;
      fantasyCountdownSeconds = 30;
      updateFantasyCountdownUI();
    }
  }

  // Auto-Refresh für Fantasy (sekündlicher Countdown, alle 30s Refresh)
  let fantasyAutoRefreshTimer = null;
  function startFantasyAutoRefresh() {
    if (fantasyAutoRefreshTimer) clearInterval(fantasyAutoRefreshTimer);
    fantasyCountdownSeconds = 30;
    updateFantasyCountdownUI();

    fantasyAutoRefreshTimer = setInterval(() => {
      const sec = document.getElementById('section-fantasy');
      const isVisible = sec && sec.classList.contains('active-section') && !document.hidden;

      if (!isVisible) {
        fantasyCountdownSeconds = 30;
        return;
      }

      fantasyCountdownSeconds--;
      if (botNextRunSeconds !== null && currentFantasyMode !== 'manual') {
        botNextRunSeconds = Math.max(0, botNextRunSeconds - 1);
        updateFantasyBotCountdownUI();
      }
      if (fantasyCountdownSeconds <= 0) {
        fantasyCountdownSeconds = 30;
        updateFantasyCountdownUI();
        loadFantasyData(false);
      } else {
        updateFantasyCountdownUI();
      }
    }, 1000);
  }

  // ===========================================================================
  // HOME ASSISTANT CONTROLLER
  // ===========================================================================
  let haCachedData = null;
  let haActiveRoomFilter = 'all';
  let haCountdownSeconds = 15;
  let isHaLoading = false;
  let haAutoRefreshTimer = null;

  function updateHaCountdownUI() {
    const badge = document.getElementById('haCountdownBadge');
    if (!badge) return;
    if (isHaLoading) {
      badge.textContent = '● LÄDT DATEN...';
      badge.style.color = 'var(--c-primary)';
    } else {
      badge.textContent = `AUTO-REFRESH: ${haCountdownSeconds}S`;
      badge.style.color = '#888';
    }
  }

  async function checkHomeAssistantConfig() {
    try {
      const resp = await fetch('/api/homeassistant/config');
      if (!resp.ok) return;
      const data = await resp.json();

      const btn = document.getElementById('btn-cat-homeassistant');
      const cfgName = (data.name || 'ASSISTANT').toUpperCase();

      if (data.configured && data.enabled) {
        if (btn) {
          btn.style.display = 'flex';
          btn.textContent = cfgName;
        }
        CATEGORY_NAMES['homeassistant'] = 'LCARS HAUSSTEUERUNG // ' + cfgName;
        const secTitle = document.getElementById('haSectionTitle');
        if (secTitle) secTitle.textContent = 'LCARS HAUSSTEUERUNG // ' + cfgName;
      } else {
        if (btn) btn.style.display = 'none';
      }

      // Populate form in Config section
      const urlInp = document.getElementById('cfgHaUrl');
      const nameInp = document.getElementById('cfgHaName');
      const userInp = document.getElementById('cfgHaUser');
      const passInp = document.getElementById('cfgHaPass');
      const tokInp = document.getElementById('cfgHaToken');
      const enInp = document.getElementById('cfgHaEnabled');
      const badge = document.getElementById('cfgHaBadge');
      const tokStatus = document.getElementById('cfgHaTokenStatus');
      const statusMsg = document.getElementById('cfgHaStatusMsg');

      if (urlInp && data.url) urlInp.value = data.url;
      if (nameInp && data.name) nameInp.value = data.name;
      if (userInp && data.username) userInp.value = data.username;
      if (passInp && data.has_creds) passInp.value = '********';
      if (tokInp && data.token_masked) tokInp.placeholder = data.token_masked;
      if (enInp) enInp.checked = data.enabled !== false;

      if (badge && statusMsg) {
        if (data.configured) {
          badge.className = 'badge-status badge-online';
          badge.textContent = 'ONLINE';
          statusMsg.textContent = 'Verbindung konfiguriert';
        } else {
          badge.className = 'badge-status badge-warn';
          badge.textContent = 'UNVOLLSTÄNDIG';
          statusMsg.textContent = 'Zugangsdaten fehlen oder deaktiviert';
        }
      }

      if (tokStatus) {
        if (data.has_token) {
          tokStatus.textContent = '● TOKEN AKTIV (' + (data.token_masked || 'GÜLTIG') + ')';
          tokStatus.style.color = '#44dd88';
        } else if (data.has_creds) {
          tokStatus.textContent = '● BENUTZER: ' + data.username;
          tokStatus.style.color = 'var(--c-primary)';
        } else {
          tokStatus.textContent = 'KEIN TOKEN';
          tokStatus.style.color = '#888';
        }
      }
    } catch(e) {
      console.warn('Home Assistant Config Check Error:', e);
    }
  }

  async function saveHomeAssistantConfig(e) {
    if (e) e.preventDefault();
    playLcarsBeep(980, 1400);

    const url = document.getElementById('cfgHaUrl')?.value?.trim();
    const name = document.getElementById('cfgHaName')?.value?.trim() || 'Assistant';
    const username = document.getElementById('cfgHaUser')?.value?.trim() || '';
    const password = document.getElementById('cfgHaPass')?.value?.trim() || '';
    const token = document.getElementById('cfgHaToken')?.value?.trim() || '';
    const enabled = document.getElementById('cfgHaEnabled')?.checked ?? true;

    const payload = { url, name, username, password, token, enabled };
    const msgEl = document.getElementById('cfgHaSaveMsg');

    try {
      const resp = await fetch('/api/config/homeassistant', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const res = await resp.json();
      if (res.success) {
        if (msgEl) {
          msgEl.style.display = 'block';
          msgEl.style.color = 'var(--c-primary)';
          msgEl.textContent = '✓ HOME ASSISTANT KONFIGURATION ERFOLGREICH GESPEICHERT';
          setTimeout(() => { msgEl.style.display = 'none'; }, 4000);
        }
        playLcarsBeep(1400, 2100);
        await checkHomeAssistantConfig();
        loadHomeAssistantData(true);
      } else {
        throw new Error(res.error || 'Fehler beim Speichern');
      }
    } catch(err) {
      if (msgEl) {
        msgEl.style.display = 'block';
        msgEl.style.color = 'var(--c-red)';
        msgEl.textContent = '✗ FEHLER: ' + err.message;
      }
      playLcarsBeep(440, 220);
    }
  }

  async function testHomeAssistantConnection() {
    playLcarsBeep(980, 1400);
    const btn = document.getElementById('btnTestHa');
    if (btn) btn.disabled = true;

    const badge = document.getElementById('cfgHaBadge');
    const msg = document.getElementById('cfgHaStatusMsg');
    if (badge) {
      badge.className = 'badge-status badge-warn';
      badge.textContent = 'TESTET...';
    }

    const url = document.getElementById('cfgHaUrl')?.value?.trim();
    const username = document.getElementById('cfgHaUser')?.value?.trim();
    const password = document.getElementById('cfgHaPass')?.value?.trim();
    const token = document.getElementById('cfgHaToken')?.value?.trim();

    try {
      const resp = await fetch('/api/homeassistant/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, username, password, token })
      });
      const data = await resp.json();
      if (data.success) {
        if (badge) {
          badge.className = 'badge-status badge-online';
          badge.textContent = 'ONLINE';
        }
        if (msg) msg.textContent = '✓ ' + (data.message || 'Verbindung erfolgreich');
        playLcarsBeep(1400, 2100);
      } else {
        if (badge) {
          badge.className = 'badge-status badge-offline';
          badge.textContent = 'FEHLER';
        }
        if (msg) msg.textContent = '✗ ' + (data.error || 'Fehlgeschlagen');
        playLcarsBeep(440, 220);
      }
    } catch(err) {
      if (badge) {
        badge.className = 'badge-status badge-offline';
        badge.textContent = 'OFFLINE';
      }
      if (msg) msg.textContent = '✗ ' + err.message;
      playLcarsBeep(440, 220);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function loadHomeAssistantData(force = false) {
    if (isHaLoading && !force) return;
    isHaLoading = true;
    updateHaCountdownUI();

    try {
      const resp = await fetch('/api/homeassistant/data');
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();

      if (data.success) {
        haCachedData = data;
        renderHaRooms();
        const liveBadge = document.getElementById('haLiveBadge');
        const statsBadge = document.getElementById('haStatsBadge');
        if (liveBadge) {
          liveBadge.className = 'badge-status badge-online';
          liveBadge.textContent = '● ONLINE';
        }
        if (statsBadge && data.stats) {
          statsBadge.textContent = `${data.stats.areas_count} RÄUME // ${data.stats.total_entities} ENTITIES (${data.stats.active_count} AKTIV)`;
        }
      } else {
        const container = document.getElementById('haRoomsContainer');
        if (container) {
          container.innerHTML = `
            <div class="ha-room-card" style="border-left-color:var(--c-red); text-align:center; padding:2.5rem 1.5rem;">
              <div style="font-size:2.2rem; margin-bottom:0.6rem;">⚠️</div>
              <div style="font-size:1.3rem; font-weight:700; color:var(--c-red); margin-bottom:0.5rem;">HOME ASSISTANT NICHT ERREICHBAR</div>
              <div style="color:#bbb; font-family:var(--mono-family); max-width:500px; margin:0 auto 1.2rem auto;">${escapeHtml(data.error || 'Bitte Zugangsdaten in der Config prüfen.')}</div>
              <button class="left-action-btn" onclick="switchCategory('config')" style="display:inline-flex; border-color:var(--c-gold); color:var(--c-gold);">
                ⚙️ ZUR CONFIG GEHEN
              </button>
            </div>
          `;
        }
        const liveBadge = document.getElementById('haLiveBadge');
        if (liveBadge) {
          liveBadge.className = 'badge-status badge-offline';
          liveBadge.textContent = 'OFFLINE';
        }
      }
    } catch(err) {
      console.warn('loadHomeAssistantData error:', err);
      const liveBadge = document.getElementById('haLiveBadge');
      if (liveBadge) {
        liveBadge.className = 'badge-status badge-offline';
        liveBadge.textContent = 'OFFLINE';
      }
    } finally {
      isHaLoading = false;
      haCountdownSeconds = 15;
      updateHaCountdownUI();
    }
  }

  function setHaRoomFilter(roomId) {
    haActiveRoomFilter = roomId;
    playLcarsBeep(980, 1400);

    document.querySelectorAll('#haRoomFilterBar button').forEach(b => {
      b.classList.remove('active-room-filter');
      b.style.borderColor = 'rgba(255,255,255,0.2)';
      b.style.color = '#bbb';
    });
    const curBtn = document.getElementById('ha-room-btn-' + roomId);
    if (curBtn) {
      curBtn.classList.add('active-room-filter');
      curBtn.style.borderColor = 'var(--c-primary)';
      curBtn.style.color = 'var(--c-primary)';
    }
    filterHaEntities();
  }

  function filterHaEntities() {
    const domain = document.getElementById('haDomainFilter')?.value || 'all';
    const search = (document.getElementById('haSearchInput')?.value || '').toLowerCase().trim();

    document.querySelectorAll('.ha-room-card').forEach(roomCard => {
      const roomId = roomCard.getAttribute('data-room-id');
      const roomMatch = (haActiveRoomFilter === 'all' || haActiveRoomFilter === roomId);

      let visibleInRoom = 0;
      roomCard.querySelectorAll('.ha-entity-tile').forEach(tile => {
        const tDomain = tile.getAttribute('data-domain');
        const tName = (tile.getAttribute('data-name') || '').toLowerCase();
        const tId = (tile.getAttribute('data-entity-id') || '').toLowerCase();
        const tControllable = tile.getAttribute('data-controllable') === 'true';

        let domainMatch = true;
        if (domain === 'controllable') {
          domainMatch = tControllable;
        } else if (domain !== 'all') {
          domainMatch = (tDomain === domain);
        }

        const searchMatch = !search || tName.includes(search) || tId.includes(search);

        if (domainMatch && searchMatch) {
          tile.style.display = 'flex';
          visibleInRoom++;
        } else {
          tile.style.display = 'none';
        }
      });

      if (roomMatch && visibleInRoom > 0) {
        roomCard.style.display = 'block';
      } else {
        roomCard.style.display = 'none';
      }
    });
  }

  function renderHaRooms() {
    if (!haCachedData) return;
    const areas = haCachedData.areas || [];
    const unassigned = haCachedData.unassigned || [];
    const container = document.getElementById('haRoomsContainer');
    const filterBar = document.getElementById('haRoomFilterBar');

    if (!container || !filterBar) return;

    // 1. Build Filter Tabs
    let filterHtml = `
      <button class="left-action-btn ${haActiveRoomFilter === 'all' ? 'active-room-filter' : ''}" onclick="setHaRoomFilter('all')" id="ha-room-btn-all" style="padding:0.3rem 0.75rem; font-size:0.82rem; ${haActiveRoomFilter === 'all' ? 'border-color:var(--c-primary); color:var(--c-primary);' : 'border-color:rgba(255,255,255,0.2); color:#bbb;'}">
        ALLE RÄUME (${areas.length})
      </button>
    `;

    areas.forEach(a => {
      const isActive = (haActiveRoomFilter === a.id);
      filterHtml += `
        <button class="left-action-btn ${isActive ? 'active-room-filter' : ''}" onclick="setHaRoomFilter('${escapeHtml(a.id)}')" id="ha-room-btn-${escapeHtml(a.id)}" style="padding:0.3rem 0.75rem; font-size:0.82rem; ${isActive ? 'border-color:var(--c-primary); color:var(--c-primary);' : 'border-color:rgba(255,255,255,0.2); color:#bbb;'}">
          ${escapeHtml(a.icon || '🚪')} ${escapeHtml(a.name.toUpperCase())} (${a.entities.length})
        </button>
      `;
    });

    if (unassigned.length > 0) {
      const isUn = (haActiveRoomFilter === 'unassigned');
      filterHtml += `
        <button class="left-action-btn ${isUn ? 'active-room-filter' : ''}" onclick="setHaRoomFilter('unassigned')" id="ha-room-btn-unassigned" style="padding:0.3rem 0.75rem; font-size:0.82rem; ${isUn ? 'border-color:var(--c-primary); color:var(--c-primary);' : 'border-color:rgba(255,255,255,0.2); color:#bbb;'}">
          🌐 WEITERE (${unassigned.length})
        </button>
      `;
    }

    filterBar.innerHTML = filterHtml;

    // 2. Build Room Cards & Entity Grids
    let roomsHtml = '';

    areas.forEach(a => {
      const lightEntities = a.entities.filter(e => e.domain === 'light');
      const hasLights = lightEntities.length > 0;
      const anyLightOn = lightEntities.some(e => e.state === 'on');

      roomsHtml += `
        <div class="ha-room-card" data-room-id="${escapeHtml(a.id)}">
          <div class="ha-room-header">
            <div class="ha-room-title">
              <span>${escapeHtml(a.icon || '🚪')}</span>
              <span>${escapeHtml(a.name)}</span>
              <span style="font-size:0.8rem; color:var(--c-gold); font-family:var(--mono-family); font-weight:normal; margin-left:0.5rem;">
                (${a.active_count > 0 ? a.active_count + ' AKTIV / ' : ''}${a.entities.length} OBJEKTE)
              </span>
            </div>
            <div style="display:flex; align-items:center; gap:0.5rem;">
              ${hasLights ? `
                <button class="left-action-btn" onclick="toggleRoomLights('${escapeHtml(a.id)}', ${!anyLightOn})" style="padding:0.25rem 0.65rem; font-size:0.75rem; border-color:${anyLightOn ? '#44dd88' : 'rgba(255,255,255,0.2)'}; color:${anyLightOn ? '#44dd88' : '#bbb'};">
                  💡 ${anyLightOn ? 'LICHTER AUS' : 'LICHTER AN'}
                </button>
              ` : ''}
            </div>
          </div>
          <div class="ha-entities-grid">
            ${a.entities.map(e => renderHaEntityTile(e)).join('')}
          </div>
        </div>
      `;
    });

    if (unassigned.length > 0) {
      roomsHtml += `
        <div class="ha-room-card" data-room-id="unassigned" style="border-left-color:var(--c-gold);">
          <div class="ha-room-header">
            <div class="ha-room-title">
              <span>🌐</span>
              <span>WEITERE GERÄTE &amp; SENSOREN</span>
              <span style="font-size:0.8rem; color:var(--c-gold); font-family:var(--mono-family); font-weight:normal; margin-left:0.5rem;">
                (${unassigned.length} OBJEKTE)
              </span>
            </div>
          </div>
          <div class="ha-entities-grid">
            ${unassigned.map(e => renderHaEntityTile(e)).join('')}
          </div>
        </div>
      `;
    }

    container.innerHTML = roomsHtml;
    filterHaEntities();
  }

  function renderHaEntityTile(e) {
    const isAuto = (e.domain === 'automation');
    const isOn = ['on', 'open', 'playing', 'cleaning'].includes(e.state);
    const isActiveState = (isOn || (e.domain === 'vacuum' && e.state !== 'docked') || (e.domain === 'climate' && e.state !== 'off'));
    
    let displayState = e.state;
    if (isAuto) {
      displayState = (e.state === 'on') ? 'AKTIV' : 'INAKTIV';
    } else if (e.state === 'on') displayState = 'AN';
    else if (e.state === 'off') displayState = 'AUS';
    else if (e.state === 'docked') displayState = 'DOCK';
    else if (e.state === 'cleaning') displayState = 'REINIGT';
    else if (e.state === 'open') displayState = 'OFFEN';
    else if (e.state === 'closed') displayState = 'ZU';
    else if (e.controls?.unit) displayState = `${e.state} ${e.controls.unit}`;

    const badgeClass = isOn ? 'ha-tile-state-badge state-on' : (isActiveState ? 'ha-tile-state-badge state-active' : 'ha-tile-state-badge');

    return `
      <div class="ha-entity-tile ${isActiveState ? 'entity-active' : ''}" 
           data-entity-id="${escapeHtml(e.entity_id)}"
           data-domain="${escapeHtml(e.domain)}"
           data-name="${escapeHtml(e.friendly_name)}"
           data-controllable="${e.controllable}"
           onclick="openHaControlModal('${escapeHtml(e.entity_id)}')">
        <div class="ha-tile-top">
          <div style="min-width:0;">
            <div class="ha-tile-name" title="${escapeHtml(e.friendly_name)}">${escapeHtml(e.friendly_name)}</div>
            <div class="ha-tile-id" title="${escapeHtml(e.entity_id)}">${escapeHtml(e.entity_id)}</div>
          </div>
          <span style="font-size:1.3rem; flex-shrink:0;">${escapeHtml(e.icon)}</span>
        </div>
        <div class="ha-tile-bottom">
          <span class="${badgeClass}">${escapeHtml(displayState)}</span>
          ${isAuto ? `
            <div style="display:flex; gap:0.35rem; align-items:center;" onclick="event.stopPropagation()">
              <button class="ha-tile-toggle-btn" style="border-color:var(--c-primary); color:var(--c-primary); padding:0.2rem 0.5rem; font-size:0.75rem;" 
                      onclick="triggerHaAutomation(event, '${escapeHtml(e.entity_id)}')" title="Automation jetzt ausführen">
                ▶ START
              </button>
              <button class="ha-tile-toggle-btn ${isOn ? 'btn-active' : ''}" 
                      onclick="toggleHaEntity(event, '${escapeHtml(e.entity_id)}', '${escapeHtml(e.domain)}')" title="${isOn ? 'Deaktivieren' : 'Aktivieren'}">
                ${isOn ? 'AUS' : 'AN'}
              </button>
            </div>
          ` : (e.controls?.can_toggle ? `
            <button class="ha-tile-toggle-btn ${isOn ? 'btn-active' : ''}" 
                    onclick="toggleHaEntity(event, '${escapeHtml(e.entity_id)}', '${escapeHtml(e.domain)}')">
              ${isOn ? 'AUS' : 'AN'}
            </button>
          ` : (e.controllable ? `
            <span style="font-size:0.75rem; color:var(--c-primary); font-family:var(--mono-family); font-weight:700;">
              STEUERN ▶
            </span>
          ` : ''))}
        </div>
      </div>
    `;
  }

  async function triggerHaAutomation(event, entityId) {
    if (event) event.stopPropagation();
    playLcarsBeep(1400, 1800);
    await callHaService('automation', 'trigger', { entity_id: entityId });
  }

  async function toggleHaEntity(event, entityId, domain) {
    if (event) event.stopPropagation();
    playLcarsBeep(1200, 1600);

    const srv = (domain === 'light' || domain === 'switch' || domain === 'input_boolean' || domain === 'automation') ? 'toggle' : 'toggle';
    await callHaService(domain, srv, { entity_id: entityId });
  }

  async function toggleRoomLights(roomId, turnOn) {
    playLcarsBeep(1200, 1600);
    if (!haCachedData) return;
    const area = haCachedData.areas.find(a => a.id === roomId);
    if (!area) return;

    const lights = area.entities.filter(e => e.domain === 'light');
    const srv = turnOn ? 'turn_on' : 'turn_off';

    for (const l of lights) {
      callHaService('light', srv, { entity_id: l.entity_id });
    }
  }

  function openHaControlModal(entityId) {
    if (!haCachedData) return;
    playLcarsBeep(980, 1400);

    let found = null;
    for (const a of (haCachedData.areas || [])) {
      found = a.entities.find(e => e.entity_id === entityId);
      if (found) break;
    }
    if (!found && haCachedData.unassigned) {
      found = haCachedData.unassigned.find(e => e.entity_id === entityId);
    }
    if (!found) return;

    const modal = document.getElementById('haControlModal');
    const iconEl = document.getElementById('haModalIcon');
    const titleEl = document.getElementById('haModalTitle');
    const eidEl = document.getElementById('haModalEntityId');
    const bodyEl = document.getElementById('haModalBody');

    if (!modal || !bodyEl) return;

    iconEl.textContent = found.icon || '💡';
    titleEl.textContent = found.friendly_name;
    eidEl.textContent = found.entity_id;

    const ctrl = found.controls || {};
    const isOn = ['on', 'open', 'playing', 'cleaning'].includes(found.state);
    let controlsHtml = '';

    // Status Banner
    controlsHtml += `
      <div style="background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:0.85rem 1rem; margin-bottom:1.25rem; display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div style="font-size:0.75rem; color:#888; text-transform:uppercase;">Aktueller Status</div>
          <div style="font-size:1.3rem; font-weight:700; color:${isOn ? '#44dd88' : 'var(--c-primary)'}; font-family:var(--mono-family); margin-top:0.2rem;">
            ${escapeHtml(found.state.toUpperCase())} ${ctrl.unit ? escapeHtml(ctrl.unit) : ''}
          </div>
        </div>
        ${ctrl.can_toggle ? `
          <div style="display:flex; gap:0.5rem;">
            <button class="left-action-btn" onclick="callHaService('${found.domain}', 'turn_off', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.4rem 0.9rem; font-size:0.85rem; border-color:${!isOn ? 'var(--c-red)' : '#666'}; color:${!isOn ? '#fff' : '#888'}; background:${!isOn ? 'rgba(207,79,79,0.3)' : 'transparent'};">
              AUS
            </button>
            <button class="left-action-btn" onclick="callHaService('${found.domain}', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.4rem 0.9rem; font-size:0.85rem; border-color:${isOn ? '#44dd88' : '#666'}; color:${isOn ? '#000' : '#888'}; background:${isOn ? '#44dd88' : 'transparent'}; font-weight:700;">
              AN
            </button>
          </div>
        ` : ''}
      </div>
    `;

    // 1. LIGHT CONTROLS
    if (found.domain === 'light') {
      const curBri = ctrl.brightness_pct ?? (isOn ? 100 : 0);
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>💡 HELLIGKEIT</span>
            <span id="haBriVal" style="font-family:var(--mono-family);">${curBri}%</span>
          </div>
          <input type="range" min="1" max="100" value="${curBri}" class="ha-slider" 
                 oninput="document.getElementById('haBriVal').textContent = this.value + '%'"
                 onchange="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', brightness_pct: parseInt(this.value)})">
        </div>

        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>🌡️ FARBTEMPERATUR</span>
            <span id="haTempLabel">WEISS-TÖNE</span>
          </div>
          <div style="display:flex; gap:0.4rem; margin-top:0.4rem;">
            <button class="left-action-btn" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', color_temp_kelvin: 2700})" style="flex:1; padding:0.35rem; font-size:0.75rem; border-color:#ffaa44; color:#ffaa44;">
              WARM (2700K)
            </button>
            <button class="left-action-btn" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', color_temp_kelvin: 4000})" style="flex:1; padding:0.35rem; font-size:0.75rem; border-color:#ffeecc; color:#ffeecc;">
              NEUTRAL (4000K)
            </button>
            <button class="left-action-btn" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', color_temp_kelvin: 6500})" style="flex:1; padding:0.35rem; font-size:0.75rem; border-color:#88bbff; color:#88bbff;">
              KALT (6500K)
            </button>
          </div>
        </div>

        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>🎨 FARBAUSWAHL (RGB)</span>
          </div>
          <div style="display:flex; gap:0.6rem; flex-wrap:wrap; margin-top:0.4rem;">
            <div class="ha-color-circle" style="background:#ff3333;" title="Rot" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [255, 51, 51]})"></div>
            <div class="ha-color-circle" style="background:#ff9933;" title="Orange" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [255, 153, 51]})"></div>
            <div class="ha-color-circle" style="background:#ffff33;" title="Gelb" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [255, 255, 51]})"></div>
            <div class="ha-color-circle" style="background:#33cc33;" title="Grün" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [51, 204, 51]})"></div>
            <div class="ha-color-circle" style="background:#33ccff;" title="Cyan" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [51, 204, 255]})"></div>
            <div class="ha-color-circle" style="background:#3366ff;" title="Blau" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [51, 102, 255]})"></div>
            <div class="ha-color-circle" style="background:#cc33ff;" title="Lila" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [204, 51, 255]})"></div>
            <div class="ha-color-circle" style="background:#ffffff;" title="Weiß" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [255, 255, 255]})"></div>
          </div>
        </div>
      `;
    }

    // 2. CLIMATE CONTROLS
    else if (found.domain === 'climate') {
      const curTemp = ctrl.current_temperature ?? '--';
      const targetTemp = ctrl.temperature ?? 21.0;
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div style="display:flex; justify-content:space-around; align-items:center; background:rgba(0,0,0,0.4); border-radius:8px; padding:1rem; margin-bottom:1rem;">
            <div style="text-align:center;">
              <div style="font-size:0.75rem; color:#888;">IST-TEMPERATUR</div>
              <div style="font-size:2rem; font-weight:800; color:var(--c-blue); font-family:var(--mono-family);">${curTemp}°C</div>
            </div>
            <div style="text-align:center;">
              <div style="font-size:0.75rem; color:#888;">SOLL-TEMPERATUR</div>
              <div style="font-size:2rem; font-weight:800; color:var(--c-gold); font-family:var(--mono-family);" id="haTargetTempVal">${targetTemp}°C</div>
            </div>
          </div>

          <div style="display:flex; gap:0.5rem; justify-content:center; align-items:center; margin-bottom:1rem;">
            <button class="left-action-btn" onclick="adjustClimateTemp('${escapeHtml(found.entity_id)}', -0.5)" style="padding:0.4rem 1rem; font-size:1.1rem; font-weight:700;">- 0.5°</button>
            <button class="left-action-btn" onclick="adjustClimateTemp('${escapeHtml(found.entity_id)}', +0.5)" style="padding:0.4rem 1rem; font-size:1.1rem; font-weight:700;">+ 0.5°</button>
          </div>

          <div class="ha-ctrl-label">
            <span>BETRIEBSMODUS</span>
          </div>
          <div style="display:flex; gap:0.4rem; flex-wrap:wrap;">
            ${(ctrl.hvac_modes || ['heat', 'cool', 'auto', 'off']).map(m => `
              <button class="left-action-btn" onclick="callHaService('climate', 'set_hvac_mode', {entity_id: '${escapeHtml(found.entity_id)}', hvac_mode: '${m}'})" style="padding:0.35rem 0.75rem; font-size:0.8rem; text-transform:uppercase; ${found.state === m ? 'border-color:var(--c-primary); color:var(--c-primary); font-weight:700;' : 'color:#aaa;'}">
                ${m}
              </button>
            `).join('')}
          </div>
        </div>
      `;
    }

    // 3. COVER / ROLLO CONTROLS
    else if (found.domain === 'cover') {
      const pos = ctrl.current_position ?? 50;
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>STEUERUNG</span>
          </div>
          <div style="display:flex; gap:0.6rem; justify-content:space-between; margin-bottom:1.2rem;">
            <button class="left-action-btn" onclick="callHaService('cover', 'open_cover', {entity_id: '${escapeHtml(found.entity_id)}'})" style="flex:1; padding:0.6rem; font-size:0.9rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
              ▲ ÖFFNEN
            </button>
            <button class="left-action-btn" onclick="callHaService('cover', 'stop_cover', {entity_id: '${escapeHtml(found.entity_id)}'})" style="flex:1; padding:0.6rem; font-size:0.9rem; border-color:var(--c-red); color:var(--c-red);">
              ■ STOPP
            </button>
            <button class="left-action-btn" onclick="callHaService('cover', 'close_cover', {entity_id: '${escapeHtml(found.entity_id)}'})" style="flex:1; padding:0.6rem; font-size:0.9rem; border-color:var(--c-blue); color:var(--c-blue);">
              ▼ SCHLIESSEN
            </button>
          </div>

          <div class="ha-ctrl-label">
            <span>POSITION</span>
            <span id="haCoverPosVal" style="font-family:var(--mono-family);">${pos}%</span>
          </div>
          <input type="range" min="0" max="100" value="${pos}" class="ha-slider" 
                 oninput="document.getElementById('haCoverPosVal').textContent = this.value + '%'"
                 onchange="callHaService('cover', 'set_cover_position', {entity_id: '${escapeHtml(found.entity_id)}', position: parseInt(this.value)})">
        </div>
      `;
    }

    // 4. MEDIA PLAYER CONTROLS
    else if (found.domain === 'media_player') {
      const vol = ctrl.volume_pct ?? 50;
      controlsHtml += `
        <div class="ha-ctrl-row">
          ${ctrl.media_title ? `
            <div style="background:rgba(0,0,0,0.4); border-radius:6px; padding:0.75rem; margin-bottom:1rem; text-align:center;">
              <div style="font-size:1.1rem; font-weight:700; color:#fff;">${escapeHtml(ctrl.media_title)}</div>
              <div style="font-size:0.85rem; color:var(--c-gold);">${escapeHtml(ctrl.media_artist || '')}</div>
            </div>
          ` : ''}

          <div class="ha-ctrl-label">
            <span>WIEDERGABE</span>
          </div>
          <div style="display:flex; gap:0.4rem; justify-content:center; margin-bottom:1.2rem;">
            <button class="left-action-btn" onclick="callHaService('media_player', 'media_previous_track', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.5rem 0.8rem; font-size:1rem;">⏮</button>
            <button class="left-action-btn" onclick="callHaService('media_player', 'media_play_pause', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.5rem 1.2rem; font-size:1.1rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">⏯ PLAY / PAUSE</button>
            <button class="left-action-btn" onclick="callHaService('media_player', 'media_stop', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.5rem 0.8rem; font-size:1rem;">⏹</button>
            <button class="left-action-btn" onclick="callHaService('media_player', 'media_next_track', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.5rem 0.8rem; font-size:1rem;">⏭</button>
          </div>

          <div class="ha-ctrl-label">
            <span>LAUTSTÄRKE</span>
            <span id="haVolVal" style="font-family:var(--mono-family);">${vol}%</span>
          </div>
          <input type="range" min="0" max="100" value="${vol}" class="ha-slider" 
                 oninput="document.getElementById('haVolVal').textContent = this.value + '%'"
                 onchange="callHaService('media_player', 'volume_set', {entity_id: '${escapeHtml(found.entity_id)}', volume_level: parseFloat(this.value) / 100})">
        </div>
      `;
    }

    // 5. VACUUM CONTROLS
    else if (found.domain === 'vacuum') {
      const bat = ctrl.battery_level;
      controlsHtml += `
        <div class="ha-ctrl-row">
          ${bat !== undefined ? `
            <div style="background:rgba(0,0,0,0.4); border-radius:6px; padding:0.6rem 1rem; margin-bottom:1rem; display:flex; justify-content:space-between; align-items:center;">
              <span style="font-size:0.85rem; color:#aaa;">BATTERIE</span>
              <span style="font-size:1.1rem; font-weight:700; color:${bat > 20 ? '#44dd88' : 'var(--c-red)'}; font-family:var(--mono-family);">${bat}% 🔋</span>
            </div>
          ` : ''}

          <div class="ha-ctrl-label">
            <span>REINIGUNG STEUERN</span>
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.6rem; margin-top:0.4rem;">
            <button class="left-action-btn" onclick="callHaService('vacuum', 'start', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.6rem; font-size:0.85rem; border-color:#44dd88; color:#44dd88; font-weight:700;">
              ▶ REINIGEN STARTEN
            </button>
            <button class="left-action-btn" onclick="callHaService('vacuum', 'pause', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.6rem; font-size:0.85rem; border-color:var(--c-gold); color:var(--c-gold);">
              ⏸ PAUSIEREN
            </button>
            <button class="left-action-btn" onclick="callHaService('vacuum', 'return_to_base', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.6rem; font-size:0.85rem; border-color:var(--c-blue); color:var(--c-blue); font-weight:700;">
              🏠 ZUR LADESTATION
            </button>
            <button class="left-action-btn" onclick="callHaService('vacuum', 'locate', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.6rem; font-size:0.85rem; border-color:var(--c-secondary); color:var(--c-secondary);">
              🔊 LOKALISIEREN (BEEP)
            </button>
          </div>
        </div>
      `;
    }

    // 6. NUMBER CONTROLS
    else if (found.domain === 'number' || found.domain === 'input_number') {
      const min = ctrl.min_val ?? 0;
      const max = ctrl.max_val ?? 255;
      const step = ctrl.step_val ?? 1;
      const cur = parseFloat(found.state) || min;
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>WERT EINSTELLEN</span>
            <span id="haNumVal" style="font-family:var(--mono-family);">${cur}</span>
          </div>
          <input type="range" min="${min}" max="${max}" step="${step}" value="${cur}" class="ha-slider" 
                 oninput="document.getElementById('haNumVal').textContent = this.value"
                 onchange="callHaService('${found.domain}', 'set_value', {entity_id: '${escapeHtml(found.entity_id)}', value: parseFloat(this.value)})">
        </div>
      `;
    }

    // 7. SELECT CONTROLS
    else if (found.domain === 'select' || found.domain === 'input_select') {
      const opts = ctrl.options || [];
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>OPTION WÄHLEN</span>
          </div>
          <select class="lcars-input" style="width:100%; margin-top:0.4rem;" onchange="callHaService('${found.domain}', 'select_option', {entity_id: '${escapeHtml(found.entity_id)}', option: this.value})">
            ${opts.map(o => `
              <option value="${escapeHtml(o)}" ${found.state === o ? 'selected' : ''}>${escapeHtml(o)}</option>
            `).join('')}
          </select>
        </div>
      `;
    }

    // 8. AUTOMATION CONTROLS
    else if (found.domain === 'automation') {
      const lastTrig = ctrl.last_triggered ? ctrl.last_triggered.replace('T', ' ').slice(0, 19) : 'Nie';
      const mode = ctrl.mode || 'single';
      controlsHtml += `
        <div class="ha-ctrl-row" style="text-align:center; padding:0.5rem 0 1rem 0;">
          <button class="left-action-btn" onclick="callHaService('automation', 'trigger', {entity_id: '${escapeHtml(found.entity_id)}'})" style="width:100%; padding:0.85rem 1.25rem; font-size:1.05rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700; background:rgba(255,153,0,0.12);">
            ⚡ AUTOMATION JETZT AUSFÜHREN (TRIGGERN)
          </button>
        </div>
        <div class="ha-ctrl-row">
          <div style="background:rgba(0,0,0,0.4); border-radius:6px; padding:1rem; font-family:var(--mono-family); font-size:0.85rem; color:#ccc;">
            <div style="margin-bottom:0.5rem;"><strong style="color:var(--c-gold);">Status:</strong> ${isOn ? '<span style="color:#44dd88; font-weight:bold;">AKTIV (Eingeschaltet)</span>' : '<span style="color:#888; font-weight:bold;">INAKTIV (Deaktiviert)</span>'}</div>
            <div style="margin-bottom:0.5rem;"><strong style="color:var(--c-gold);">Modus:</strong> ${escapeHtml(mode)}</div>
            <div><strong style="color:var(--c-gold);">Zuletzt ausgeführt:</strong> ${escapeHtml(lastTrig)}</div>
          </div>
        </div>
      `;
    }

    // 9. BUTTON / SCENE / SCRIPT
    else if (['button', 'scene', 'script', 'input_button'].includes(found.domain)) {
      const srv = found.domain === 'scene' ? 'turn_on' : (found.domain === 'button' ? 'press' : 'turn_on');
      controlsHtml += `
        <div class="ha-ctrl-row" style="text-align:center; padding:1rem 0;">
          <button class="left-action-btn" onclick="callHaService('${found.domain}', '${srv}', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.75rem 2rem; font-size:1.1rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
            🔘 AKTION JETZT AUSFÜHREN
          </button>
        </div>
      `;
    }

    // 9. SENSORS & ATTRIBUTES
    else {
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div style="background:rgba(0,0,0,0.4); border-radius:6px; padding:1rem; font-family:var(--mono-family); font-size:0.85rem; color:#ccc;">
            <div style="margin-bottom:0.4rem;"><strong style="color:var(--c-gold);">Zustand:</strong> ${escapeHtml(found.state)} ${escapeHtml(ctrl.unit || '')}</div>
            <div style="margin-bottom:0.4rem;"><strong style="color:var(--c-gold);">Typ:</strong> ${escapeHtml(ctrl.device_class || found.domain)}</div>
            ${found.last_changed ? `<div><strong style="color:var(--c-gold);">Aktualisiert:</strong> ${escapeHtml(found.last_changed.replace('T', ' ').slice(0, 19))}</div>` : ''}
          </div>
        </div>
      `;
    }

    bodyEl.innerHTML = controlsHtml;
    modal.style.display = 'flex';
  }

  function closeHaControlModal() {
    playLcarsBeep(880, 440);
    const modal = document.getElementById('haControlModal');
    if (modal) modal.style.display = 'none';
  }

  function handleModalBackdropClick(e) {
    if (e.target && e.target.id === 'haControlModal') {
      closeHaControlModal();
    }
  }

  async function adjustClimateTemp(entityId, delta) {
    if (!haCachedData) return;
    let found = null;
    for (const a of (haCachedData.areas || [])) {
      found = a.entities.find(e => e.entity_id === entityId);
      if (found) break;
    }
    if (!found) return;

    const cur = found.controls?.temperature || 21.0;
    const next = Math.round((cur + delta) * 10) / 10;
    const labelEl = document.getElementById('haTargetTempVal');
    if (labelEl) labelEl.textContent = next + '°C';
    await callHaService('climate', 'set_temperature', { entity_id: entityId, temperature: next });
  }

  async function callHaService(domain, service, serviceData) {
    playLcarsBeep(1200, 1600);
    try {
      const resp = await fetch('/api/homeassistant/service', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain, service, service_data: serviceData })
      });
      const res = await resp.json();
      if (!res.success) {
        console.warn('Service Call Error:', res.error);
        playLcarsBeep(440, 220);
      }
      setTimeout(() => { loadHomeAssistantData(true); }, 350);
    } catch(err) {
      console.warn('callHaService error:', err);
      playLcarsBeep(440, 220);
    }
  }

  function startHaAutoRefresh() {
    if (haAutoRefreshTimer) clearInterval(haAutoRefreshTimer);
    haCountdownSeconds = 15;
    updateHaCountdownUI();

    haAutoRefreshTimer = setInterval(() => {
      const sec = document.getElementById('section-homeassistant');
      const isVisible = sec && sec.classList.contains('active-section') && !document.hidden;

      if (!isVisible) {
        haCountdownSeconds = 15;
        return;
      }

      haCountdownSeconds--;
      if (haCountdownSeconds <= 0) {
        haCountdownSeconds = 15;
        updateHaCountdownUI();
        loadHomeAssistantData(false);
      } else {
        updateHaCountdownUI();
      }
    }, 1000);
  }

  // BALKONSOLAR & DACHTERRASSE CONTROLLER
  let isSolarLoading = false;
  let solarCountdownSeconds = 15;
  let solarAutoRefreshTimer = null;
  let solarCurrentData = null;

  function updateSolarCountdownUI() {
    const badge = document.getElementById('solarCountdownBadge');
    if (!badge) return;
    if (isSolarLoading) {
      badge.textContent = '● SYNC...';
      badge.style.color = 'var(--c-gold)';
      badge.style.borderColor = 'var(--c-gold)';
      badge.style.background = 'rgba(237, 179, 120, 0.15)';
    } else {
      badge.textContent = `● REFRESH IN ${solarCountdownSeconds}S`;
      badge.style.color = 'var(--c-gold)';
      badge.style.borderColor = 'rgba(237, 179, 120, 0.4)';
      badge.style.background = 'rgba(237, 179, 120, 0.15)';
    }
  }

  async function loadSolarData(force = false) {
    if (isSolarLoading) return;
    isSolarLoading = true;
    updateSolarCountdownUI();

    try {
      const resp = await fetch('/api/solar/data');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      solarCurrentData = data;
      renderSolarUI(data);
    } catch(err) {
      console.warn('loadSolarData error:', err);
      const liveBadge = document.getElementById('solarLiveBadge');
      if (liveBadge) {
        liveBadge.textContent = 'OFFLINE';
        liveBadge.className = 'badge-status badge-offline';
      }
    } finally {
      isSolarLoading = false;
      solarCountdownSeconds = 15;
      updateSolarCountdownUI();
    }
  }

  function renderSolarUI(data) {
    if (!data || !data.success || !data.summary) return;
    const s = data.summary;

    // Badges oben
    const liveBadge = document.getElementById('solarLiveBadge');
    if (liveBadge) {
      liveBadge.textContent = 'ONLINE';
      liveBadge.className = 'badge-status badge-online';
    }

    const cloudBadge = document.getElementById('solarCloudBadge');
    if (cloudBadge) {
      const c1 = (s.cloud_state || 'online').toUpperCase();
      const c2 = (s.ecotracker_cloud || 'online').toUpperCase();
      cloudBadge.textContent = `ANKER CLOUD: ${c1} // ECOTRACKER: ${c2}`;
      cloudBadge.style.color = (c1 === 'ONLINE' && c2 === 'ONLINE') ? 'var(--c-gold)' : 'var(--c-red)';
    }

    // Top Right Badges (Header)
    const topBat = document.getElementById('topBatVal');
    if (topBat && s.battery_soc_str) topBat.textContent = s.battery_soc_str;
    const itemBat = document.getElementById('topVitalBat');
    if (itemBat && s.battery_soc !== null && s.battery_soc !== undefined) {
      itemBat.classList.toggle('vital-alert', s.battery_soc < 10);
    }

    const topHouse = document.getElementById('topHouseVal');
    if (topHouse && s.house_power_str) topHouse.textContent = s.house_power_str;

    // Power Flow Nodes
    const nSolarVal = document.getElementById('nodeSolarVal');
    if (nSolarVal) nSolarVal.textContent = s.solar_power_str || '0 W';

    const nBatVal = document.getElementById('nodeBatVal');
    if (nBatVal) nBatVal.textContent = s.battery_soc_str || '--%';

    const nBatStatus = document.getElementById('nodeBatStatus');
    if (nBatStatus) {
      nBatStatus.textContent = s.battery_status || 'STANDBY';
      nBatStatus.style.color = s.battery_status_color || 'var(--c-secondary)';
    }

    const nBatSub = document.getElementById('nodeBatSub');
    if (nBatSub) {
      nBatSub.textContent = `${s.battery_energy_wh || 0} Wh / ${s.battery_capacity_wh || 1600} Wh (${s.battery_temp_str || '--'})`;
    }

    const nHouseVal = document.getElementById('nodeHouseVal');
    if (nHouseVal) nHouseVal.textContent = s.house_power_str || '-- W';

    const nGridVal = document.getElementById('nodeGridVal');
    if (nGridVal) nGridVal.textContent = s.grid_power_str || '-- W';

    const nGridSub = document.getElementById('nodeGridSub');
    if (nGridSub) {
      if (s.grid_feed_in > 0) {
        nGridSub.textContent = `Einspeisung: ${s.grid_feed_in_str} // Bezug: 0 W`;
      } else {
        nGridSub.textContent = `Netzbezug // ${s.grid_feed_in_str || '0 W'} Einspeisung`;
      }
    }

    // Summary Tag
    const flowTag = document.getElementById('solarFlowSummaryTag');
    if (flowTag) {
      if (s.solar_power > (s.house_power || 0) && s.solar_power > 0) {
        flowTag.textContent = '⚡ SOLAR-ÜBERSCHUSS';
        flowTag.style.backgroundColor = '#44dd88';
      } else if (s.battery_discharge_power > 20) {
        flowTag.textContent = '🔋 AKKU-VERSORGUNG';
        flowTag.style.backgroundColor = 'var(--c-secondary)';
      } else if (s.grid_power > 0) {
        flowTag.textContent = '🌐 LIVE NETZBEZUG';
        flowTag.style.backgroundColor = 'var(--c-gold)';
      } else {
        flowTag.textContent = 'AUTARK';
        flowTag.style.backgroundColor = '#44dd88';
      }
    }

    // Card 1: Photovoltaik
    const cSolVal = document.getElementById('cardSolarVal');
    if (cSolVal) cSolVal.textContent = s.solar_power_str || '0 W';

    const setPvBar = (valId, barId, val) => {
      const elVal = document.getElementById(valId);
      const elBar = document.getElementById(barId);
      if (elVal) elVal.textContent = `${val} W`;
      if (elBar) elBar.style.width = Math.min(100, Math.round((val / 500) * 100)) + '%';
    };
    setPvBar('pv1Val', 'pv1Bar', s.pv1 || 0);
    setPvBar('pv2Val', 'pv2Bar', s.pv2 || 0);
    setPvBar('pv3Val', 'pv3Bar', s.pv3 || 0);
    setPvBar('pv4Val', 'pv4Bar', s.pv4 || 0);

    const elYield = document.getElementById('solYieldTotal');
    if (elYield) elYield.textContent = (s.yield_total !== null && s.yield_total !== undefined) ? `${s.yield_total} kWh` : '-- kWh';
    const elCo2 = document.getElementById('solCo2Saved');
    if (elCo2) elCo2.textContent = (s.co2_saved !== null && s.co2_saved !== undefined) ? `${s.co2_saved} kg` : '-- kg';
    const elCost = document.getElementById('solCostSaved');
    if (elCost) elCost.textContent = (s.cost_saved !== null && s.cost_saved !== undefined) ? `${Number(s.cost_saved).toFixed(2)} €` : '-- €';

    // Card 2: Akku
    const cBatVal = document.getElementById('cardBatVal');
    if (cBatVal) cBatVal.textContent = s.battery_soc_str || '--%';
    const cBatSub = document.getElementById('cardBatSub');
    if (cBatSub) {
      cBatSub.textContent = `Akkustand (${s.battery_status || 'STANDBY'})`;
      cBatSub.style.color = s.battery_status_color || 'var(--c-secondary)';
    }
    const cBatBar = document.getElementById('cardBatBar');
    if (cBatBar) {
      const pct = Math.min(100, Math.max(0, s.battery_soc || 0));
      cBatBar.style.width = pct + '%';
      if (pct < 10) cBatBar.style.backgroundColor = 'var(--c-red)';
      else if (pct < 30) cBatBar.style.backgroundColor = 'var(--c-gold)';
      else cBatBar.style.backgroundColor = 'var(--c-secondary)';
    }

    const elEnergyWh = document.getElementById('solEnergyWh');
    if (elEnergyWh) elEnergyWh.textContent = `${s.battery_energy_wh || 0} Wh / ${s.battery_capacity_wh || 1600} Wh`;
    const elBatCharge = document.getElementById('solBatCharge');
    if (elBatCharge) elBatCharge.textContent = `${s.battery_charge_power || 0} W`;
    const elBatDischarge = document.getElementById('solBatDischarge');
    if (elBatDischarge) elBatDischarge.textContent = `${s.battery_discharge_power || 0} W`;
    const elBatTemp = document.getElementById('solBatTemp');
    if (elBatTemp) elBatTemp.textContent = s.battery_temp_str || '--';
    const elSocLimits = document.getElementById('solSocLimits');
    if (elSocLimits) elSocLimits.textContent = `Min ${s.battery_soc_min || 5}% / Max ${s.battery_soc_max || 100}%`;
    const elBatHeating = document.getElementById('solBatHeating');
    if (elBatHeating) elBatHeating.textContent = s.battery_heating ? `Aktiv (${s.battery_heat_power || 0} W)` : 'Inaktiv (0 W)';
    const elOpState = document.getElementById('solOpState');
    if (elOpState) elOpState.textContent = s.operating_state || '--';

    // Card 3: Hausnetz & Zähler
    const cHouseVal = document.getElementById('cardHouseVal');
    if (cHouseVal) cHouseVal.textContent = s.house_power_str || '-- W';
    const elAcOut = document.getElementById('solAcOutput');
    if (elAcOut) elAcOut.textContent = `${s.inverter_ac_output || 0} W`;
    const elDcOut = document.getElementById('solDcOutput');
    if (elDcOut) elDcOut.textContent = `${s.inverter_dc_output || 0} W`;
    const elGridUsage = document.getElementById('solGridUsage');
    if (elGridUsage) elGridUsage.textContent = s.grid_power_str || '-- W';
    const elGridFeed = document.getElementById('solGridFeedIn');
    if (elGridFeed) elGridFeed.textContent = s.grid_feed_in_str || '0 W';
    const elAcSocket = document.getElementById('solAcSocket');
    if (elAcSocket) elAcSocket.textContent = `${s.inverter_socket || 0} W`;
    const elFeedTarget = document.getElementById('solFeedTarget');
    if (elFeedTarget) elFeedTarget.textContent = `${s.feed_target || 0} W`;
    const elFeedLimit = document.getElementById('solFeedLimit');
    if (elFeedLimit) elFeedLimit.textContent = `${s.feed_limit || 800} W`;

    // Card 4: Telemetrie & Schnellsteuerung
    const elMode = document.getElementById('solMode');
    if (elMode) elMode.textContent = s.mode || 'smartmeter';
    const elCloudState = document.getElementById('solCloudState');
    if (elCloudState) {
      elCloudState.textContent = (s.cloud_state || 'online').toUpperCase();
      elCloudState.style.color = (s.cloud_state === 'online') ? '#44dd88' : 'var(--c-red)';
    }
    const elEcoCloud = document.getElementById('solEcoCloudState');
    if (elEcoCloud) {
      elEcoCloud.textContent = (s.ecotracker_cloud || 'online').toUpperCase();
      elEcoCloud.style.color = (s.ecotracker_cloud === 'online') ? '#44dd88' : 'var(--c-red)';
    }
    const elWifiState = document.getElementById('solWifiState');
    if (elWifiState) {
      const w1 = s.wifi_storage ? 'Verbunden' : 'Getrennt';
      const w2 = s.wifi_tracker ? 'Verbunden' : 'Getrennt';
      elWifiState.textContent = `${w1} / ${w2}`;
    }
    const elMqttTime = document.getElementById('solMqttTime');
    if (elMqttTime) elMqttTime.textContent = s.mqtt_time || '--';

    // Buttons / Switch State
    const btnFeed = document.getElementById('btnToggleFeed');
    if (btnFeed) {
      btnFeed.textContent = s.grid_feed_allowed ? 'NETZEINSPEISUNG: AN' : 'NETZEINSPEISUNG: AUS';
      btnFeed.style.borderColor = s.grid_feed_allowed ? '#44dd88' : '#888';
      btnFeed.style.color = s.grid_feed_allowed ? '#44dd88' : '#888';
    }
    const btnLed = document.getElementById('btnToggleLed');
    if (btnLed) {
      btnLed.textContent = s.led_light ? 'LED LICHT: AN' : 'LED LICHT: AUS';
      btnLed.style.borderColor = s.led_light ? 'var(--c-gold)' : '#888';
      btnLed.style.color = s.led_light ? 'var(--c-gold)' : '#888';
    }

    // Entities Grid
    renderSolarEntitiesGrid(data.entities || []);
  }

  function renderSolarEntitiesGrid(entities) {
    const container = document.getElementById('solarEntitiesGrid');
    if (!container) return;

    if (!entities || entities.length === 0) {
      container.innerHTML = '<div style="padding:2rem; text-align:center; color:#888; font-family:var(--mono-family); grid-column:1/-1;">Keine Dachterassen-Objekte gefunden.</div>';
      return;
    }

    const domainFilter = document.getElementById('solarDomainFilter')?.value || 'all';
    const searchVal = (document.getElementById('solarSearchInput')?.value || '').toLowerCase().trim();

    const filtered = entities.filter(e => {
      if (domainFilter === 'controllable' && !e.controllable) return false;
      if (domainFilter !== 'all' && domainFilter !== 'controllable' && e.domain !== domainFilter) return false;
      if (searchVal) {
        const matchName = (e.friendly_name || '').toLowerCase().includes(searchVal);
        const matchId = (e.entity_id || '').toLowerCase().includes(searchVal);
        const matchState = (e.state || '').toLowerCase().includes(searchVal);
        if (!matchName && !matchId && !matchState) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      container.innerHTML = '<div style="padding:2rem; text-align:center; color:#888; font-family:var(--mono-family); grid-column:1/-1;">Keine Objekte entsprechen dem aktuellen Filter.</div>';
      return;
    }

    let html = '';
    filtered.forEach(e => {
      const isActive = ['on', 'open', 'playing', 'cleaning'].includes(e.state);
      const activeClass = isActive ? 'entity-active' : '';
      const unit = e.controls?.unit ? ` ${e.controls.unit}` : '';
      const stateDisplay = (e.state || 'unknown') + unit;

      html += `
        <div class="ha-entity-tile ${activeClass}" onclick="openHaControlModal('${e.entity_id}')" title="${e.entity_id}">
          <div class="ha-tile-top">
            <span class="ha-tile-icon">${e.icon || '⚙️'}</span>
            <span class="ha-tile-state" style="font-family:var(--mono-family); font-weight:700; font-size:0.85rem; color:${isActive ? 'var(--c-primary)' : '#ccc'};">${stateDisplay}</span>
          </div>
          <div class="ha-tile-name" style="margin-top:0.4rem; font-weight:600; font-size:0.82rem; color:#fff; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${e.friendly_name}</div>
          <div class="ha-tile-id" style="font-size:0.68rem; color:#777; font-family:var(--mono-family); text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${e.entity_id}</div>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  function filterSolarEntities() {
    if (solarCurrentData && solarCurrentData.entities) {
      renderSolarEntitiesGrid(solarCurrentData.entities);
    }
  }

  async function triggerSolarAction(entityId, label) {
    playLcarsBeep(980, 1400);
    try {
      const resp = await fetch('/api/homeassistant/service', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain: 'button',
          service: 'press',
          service_data: { entity_id: entityId }
        })
      });
      const res = await resp.json();
      if (res.success) {
        playLcarsBeep(1200, 1600);
        setTimeout(() => { loadSolarData(true); }, 400);
      } else {
        console.warn('triggerSolarAction error:', res.error);
        playLcarsBeep(440, 220);
      }
    } catch(e) {
      console.warn('triggerSolarAction exception:', e);
      playLcarsBeep(440, 220);
    }
  }

  async function toggleSolarFeedSwitch() {
    if (!solarCurrentData || !solarCurrentData.summary) return;
    const current = solarCurrentData.summary.grid_feed_allowed;
    const service = current ? 'turn_off' : 'turn_on';
    playLcarsBeep(880, 1760);
    try {
      await fetch('/api/homeassistant/service', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain: 'switch',
          service: service,
          service_data: { entity_id: 'switch.christophs_energiespeicher_erlaube_netzeinspeisung' }
        })
      });
      setTimeout(() => { loadSolarData(true); }, 400);
    } catch(e) {
      console.warn('toggleSolarFeedSwitch error:', e);
    }
  }

  async function toggleSolarLedSwitch() {
    if (!solarCurrentData || !solarCurrentData.summary) return;
    const current = solarCurrentData.summary.led_light;
    const service = current ? 'turn_off' : 'turn_on';
    playLcarsBeep(880, 1760);
    try {
      await fetch('/api/homeassistant/service', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain: 'switch',
          service: service,
          service_data: { entity_id: 'switch.christophs_energiespeicher_led_licht' }
        })
      });
      setTimeout(() => { loadSolarData(true); }, 400);
    } catch(e) {
      console.warn('toggleSolarLedSwitch error:', e);
    }
  }

  function startSolarAutoRefresh() {
    if (solarAutoRefreshTimer) clearInterval(solarAutoRefreshTimer);
    solarCountdownSeconds = 15;
    updateSolarCountdownUI();

    solarAutoRefreshTimer = setInterval(() => {
      const sec = document.getElementById('section-solar');
      const isVisible = sec && sec.classList.contains('active-section') && !document.hidden;

      if (!isVisible) {
        solarCountdownSeconds = 15;
        return;
      }

      solarCountdownSeconds--;
      if (solarCountdownSeconds <= 0) {
        solarCountdownSeconds = 15;
        updateSolarCountdownUI();
        loadSolarData(false);
      } else {
        updateSolarCountdownUI();
      }
    }, 1000);
  }

  function playRedAlertKlaxon() {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioContext();
      if (!ctx) return;
      const now = ctx.currentTime;
      for (let i = 0; i < 2; i++) {
        const start = now + (i * 0.65);
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(660, start);
        osc.frequency.exponentialRampToValueAtTime(440, start + 0.42);
        gain.gain.setValueAtTime(0.01, start);
        gain.gain.linearRampToValueAtTime(0.2, start + 0.05);
        gain.gain.exponentialRampToValueAtTime(0.001, start + 0.5);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(start + 0.55);
      }
    } catch(e) {
      console.warn("Red Alert Klaxon audio error:", e);
    }
  }

  // Theme Switcher (Farbmodi)
  let lastUserTheme = localStorage.getItem('lcars-theme') || 'classic';
  if (lastUserTheme === 'redalert') lastUserTheme = 'classic';
  let isCurrentlyRedAlert = false;

  function setLcarsTheme(themeName) {
    if (themeName === 'redalert') return; // Red Alert kann nicht manuell gewählt werden
    playLcarsBeep(1100, 1800);
    lastUserTheme = themeName;
    localStorage.setItem('lcars-theme', themeName);

    if (!isCurrentlyRedAlert) {
      document.documentElement.setAttribute('data-theme', themeName);
    }

    document.querySelectorAll('.theme-btn').forEach(btn => btn.classList.remove('active-theme'));
    const btn = document.getElementById('theme-btn-' + themeName);
    if (btn) btn.classList.add('active-theme');

    if (historyChart) historyChart.update();
  }

  setLcarsTheme(lastUserTheme);
  updateAudioUI();

  // Schwellwert-Initialisierung im Config-Bereich
  let thresholdsInitialized = false;
  function initThresholdsConfig(alerts) {
    if (!alerts || !alerts.thresholds) return;
    if (thresholdsInitialized) return;
    thresholdsInitialized = true;
    const th = alerts.thresholds;
    if (th.cpu_threshold !== undefined) {
      const el = document.getElementById('cfgCpuThresh');
      if (el) { el.value = th.cpu_threshold; document.getElementById('cfgCpuThreshVal').textContent = th.cpu_threshold + '%'; }
    }
    if (th.ram_threshold !== undefined) {
      const el = document.getElementById('cfgRamThresh');
      if (el) { el.value = th.ram_threshold; document.getElementById('cfgRamThreshVal').textContent = th.ram_threshold + '%'; }
    }
    if (th.disk_threshold !== undefined) {
      const el = document.getElementById('cfgDiskThresh');
      if (el) { el.value = th.disk_threshold; document.getElementById('cfgDiskThreshVal').textContent = th.disk_threshold + '%'; }
    }
    if (th.temp_threshold !== undefined) {
      const el = document.getElementById('cfgTempThresh');
      if (el) { el.value = th.temp_threshold; document.getElementById('cfgTempThreshVal').textContent = th.temp_threshold + '°C'; }
    }
    if (th.duration_seconds !== undefined) {
      const el = document.getElementById('cfgDuration');
      if (el) { el.value = th.duration_seconds; document.getElementById('cfgDurationVal').textContent = th.duration_seconds + ' Sek'; }
    }
    if (th.gateway_check_interval_seconds !== undefined) {
      const minVal = Math.round(th.gateway_check_interval_seconds / 60) || 10;
      const el = document.getElementById('cfgGwInterval');
      if (el) { el.value = minVal; document.getElementById('cfgGwIntervalVal').textContent = minVal + ' Min'; }
    }
    if (th.gateway_url) {
      const el = document.getElementById('cfgGwUrl');
      if (el) el.value = th.gateway_url;
    }
    if (th.antigravity_ide_url) {
      const elCfg = document.getElementById('cfgIdeUrl');
      if (elCfg) elCfg.value = th.antigravity_ide_url;
      const elIde = document.getElementById('ideUrlInput');
      if (elIde && (!elIde.value || elIde.value.indexOf('antigravity.google.com') !== -1)) {
        elIde.value = th.antigravity_ide_url;
      }
      const launchBtn = document.getElementById('ideLaunchBtn');
      if (launchBtn) launchBtn.href = th.antigravity_ide_url;
    }
  }

  // Red Alert Status Handler
  function updateAlertUI(alerts) {
    if (!alerts) return;

    initThresholdsConfig(alerts);

    const banner = document.getElementById('redAlertBanner');
    const reasonsEl = document.getElementById('redAlertReasons');

    if (alerts.active) {
      if (!isCurrentlyRedAlert) {
        isCurrentlyRedAlert = true;
        const cur = document.documentElement.getAttribute('data-theme');
        if (cur && cur !== 'redalert') {
          lastUserTheme = cur;
        }
        document.documentElement.setAttribute('data-theme', 'redalert');
        playRedAlertKlaxon();
      }
      if (banner) banner.style.display = 'block';
      if (reasonsEl && alerts.reasons) reasonsEl.textContent = alerts.reasons.join(' // ');
    } else {
      if (isCurrentlyRedAlert) {
        isCurrentlyRedAlert = false;
        document.documentElement.setAttribute('data-theme', lastUserTheme || 'classic');
      }
      if (banner) banner.style.display = 'none';
    }

    if (alerts.gateway) {
      const gwBadge = document.getElementById('cfgGwBadge');
      const gwTime = document.getElementById('cfgGwTime');
      const gwErr = document.getElementById('cfgGwErr');
      if (gwBadge) {
        if (alerts.gateway.status === 'ok') {
          gwBadge.className = 'badge-status badge-online';
          gwBadge.textContent = 'ONLINE';
          if (gwErr) gwErr.textContent = '';
        } else {
          gwBadge.className = 'badge-status badge-offline';
          gwBadge.textContent = 'OFFLINE';
          if (gwErr) gwErr.textContent = alerts.gateway.error ? `(${alerts.gateway.error})` : '';
        }
      }
      if (gwTime && alerts.gateway.last_check) {
        gwTime.textContent = `Letzter Test: ${alerts.gateway.last_check}`;
      }
    }
  }

  async function saveThresholds(e) {
    if (e) e.preventDefault();
    playLcarsBeep(1100, 1800);
    const cpu = parseFloat(document.getElementById('cfgCpuThresh').value);
    const ram = parseFloat(document.getElementById('cfgRamThresh').value);
    const disk = parseFloat(document.getElementById('cfgDiskThresh').value);
    const temp = parseFloat(document.getElementById('cfgTempThresh').value);
    const duration = parseInt(document.getElementById('cfgDuration').value);
    const gwIntervalMin = parseInt(document.getElementById('cfgGwInterval').value);
    const gwUrl = document.getElementById('cfgGwUrl').value.trim();
    const ideUrl = (document.getElementById('cfgIdeUrl')?.value || '').trim();

    const payload = {
      cpu_threshold: cpu,
      ram_threshold: ram,
      disk_threshold: disk,
      temp_threshold: temp,
      duration_seconds: duration,
      gateway_check_interval_seconds: gwIntervalMin * 60,
      gateway_url: gwUrl
    };
    if (ideUrl) payload.antigravity_ide_url = ideUrl;

    const msgEl = document.getElementById('cfgSaveMsg');
    try {
      const resp = await fetch('/api/config/thresholds', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await resp.json();
      if (msgEl) {
        msgEl.style.display = 'block';
        msgEl.style.color = 'var(--c-primary)';
        msgEl.textContent = '✓ SCHWELLWERTE ERFOLGREICH GESPEICHERT & AKTIVIERT';
        setTimeout(() => { msgEl.style.display = 'none'; }, 4000);
      }
      playLcarsBeep(1400, 2100);
      fetchLiveStats(true);
    } catch(err) {
      if (msgEl) {
        msgEl.style.display = 'block';
        msgEl.style.color = 'var(--c-red)';
        msgEl.textContent = '✗ FEHLER BEIM SPEICHERN: ' + err;
      }
      playLcarsBeep(440, 220);
    }
  }

  async function testGatewayNow() {
    playLcarsBeep(980, 1400);
    const btn = document.getElementById('btnTestGw');
    if (btn) btn.disabled = true;
    const badge = document.getElementById('cfgGwBadge');
    const timeEl = document.getElementById('cfgGwTime');
    const errEl = document.getElementById('cfgGwErr');
    if (badge) {
      badge.className = 'badge-status';
      badge.style.background = 'var(--c-gold)';
      badge.textContent = 'PRÜFE...';
    }

    try {
      const resp = await fetch('/api/gateway/test', { method: 'POST' });
      const data = await resp.json();
      if (badge) {
        if (data.status === 'ok') {
          badge.className = 'badge-status badge-online';
          badge.textContent = 'ONLINE';
          if (errEl) errEl.textContent = '';
          playLcarsBeep(1200, 2400);
        } else {
          badge.className = 'badge-status badge-offline';
          badge.textContent = 'OFFLINE';
          if (errEl) errEl.textContent = data.error ? `(${data.error})` : '(Fehler)';
          playLcarsBeep(440, 220);
        }
      }
      if (timeEl && data.time) {
        timeEl.textContent = `Letzter Test: ${data.time}`;
      }
    } catch(err) {
      if (badge) {
        badge.className = 'badge-status badge-offline';
        badge.textContent = 'FEHLER';
      }
      if (errEl) errEl.textContent = String(err);
      playLcarsBeep(440, 220);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function resetDefaultThresholds() {
    playLcarsBeep(880, 1320);
    document.getElementById('cfgCpuThresh').value = 90;
    document.getElementById('cfgCpuThreshVal').textContent = '90%';
    document.getElementById('cfgRamThresh').value = 90;
    document.getElementById('cfgRamThreshVal').textContent = '90%';
    document.getElementById('cfgDiskThresh').value = 90;
    document.getElementById('cfgDiskThreshVal').textContent = '90%';
    document.getElementById('cfgTempThresh').value = 80;
    document.getElementById('cfgTempThreshVal').textContent = '80°C';
    document.getElementById('cfgDuration').value = 60;
    document.getElementById('cfgDurationVal').textContent = '60 Sek';
    document.getElementById('cfgGwInterval').value = 10;
    document.getElementById('cfgGwIntervalVal').textContent = '10 Min';
    document.getElementById('cfgGwUrl').value = 'http://127.0.0.1:20128';
  }

  // Telemetrie Aktualisierung & Rendering
  function renderStats(data) {
    if (!data) return;

    // Schwellwerte für Live-Warnung ermitteln
    const cpuTh = (data.alerts?.thresholds?.cpu_threshold) ?? 90;
    const ramTh = (data.alerts?.thresholds?.ram_threshold) ?? 90;
    const diskTh = (data.alerts?.thresholds?.disk_threshold) ?? 90;
    const tempTh = (data.alerts?.thresholds?.temp_threshold) ?? 80;

    // CPU
    if (data.cpu) {
      document.getElementById('sysCpuVal').textContent = data.cpu.percent + '%';
      document.getElementById('sysCpuBar').style.width = data.cpu.percent + '%';
      document.getElementById('sysCpuCores').textContent = `Kerne: ${data.cpu.cores || 1}`;

      const topCpu = document.getElementById('topCpuVal');
      if (topCpu) topCpu.textContent = Math.round(data.cpu.percent) + '%';
      const itemCpu = document.getElementById('topVitalCpu');
      if (itemCpu) itemCpu.classList.toggle('vital-alert', data.cpu.percent >= cpuTh);
      const liveCpu = document.getElementById('curCpuLive');
      if (liveCpu) liveCpu.textContent = data.cpu.percent + '%';
    }

    // RAM
    if (data.ram) {
      document.getElementById('sysRamVal').textContent = data.ram.percent + '%';
      document.getElementById('sysRamBar').style.width = data.ram.percent + '%';
      document.getElementById('sysRamSub').textContent = `${data.ram.used_gb} GB / ${data.ram.total_gb} GB (${data.ram.available_gb} frei)`;

      const topRam = document.getElementById('topRamVal');
      if (topRam) topRam.textContent = Math.round(data.ram.percent) + '%';
      const itemRam = document.getElementById('topVitalRam');
      if (itemRam) itemRam.classList.toggle('vital-alert', data.ram.percent >= ramTh);
      const liveRam = document.getElementById('curRamLive');
      if (liveRam) liveRam.textContent = data.ram.percent + '%';
    }

    // Temp
    if (data.temperature) {
      document.getElementById('sysTempVal').textContent = data.temperature.display || '-- °C';
      if (data.temperature.value) {
        document.getElementById('sysTempBar').style.width = Math.min(100, (data.temperature.value / 85) * 100) + '%';
      }

      const topTemp = document.getElementById('topTempVal');
      if (topTemp) topTemp.textContent = Math.round(data.temperature.value || 0) + '°';
      const itemTemp = document.getElementById('topVitalTemp');
      if (itemTemp) itemTemp.classList.toggle('vital-alert', (data.temperature.value || 0) >= tempTh);
      const liveTemp = document.getElementById('curTempLive');
      if (liveTemp) liveTemp.textContent = data.temperature.display || '--';
    }

    // Throttled
    if (data.throttled) {
      const isThrottled = data.throttled.active;
      const el = document.getElementById('sysThrottledSub');
      if (el) {
        el.textContent = isThrottled ? 'WARNUNG: THROTTLED (0x1)' : `Normal (${data.throttled.raw || '0x0'})`;
        el.style.color = isThrottled ? 'var(--c-red)' : 'var(--c-gold)';
      }
    }

    // Disk
    if (data.disk) {
      document.getElementById('sysDiskVal').textContent = data.disk.percent + '%';
      document.getElementById('sysDiskBar').style.width = data.disk.percent + '%';
      document.getElementById('sysDiskSub').textContent = `${data.disk.used_gb} GB von ${data.disk.total_gb} GB`;

      const topDisk = document.getElementById('topDiskVal');
      if (topDisk) topDisk.textContent = Math.round(data.disk.percent) + '%';
      const itemDisk = document.getElementById('topVitalDisk');
      if (itemDisk) itemDisk.classList.toggle('vital-alert', data.disk.percent >= diskTh);
      const liveDisk = document.getElementById('curDiskLive');
      if (liveDisk) liveDisk.textContent = data.disk.percent + '%';
    }

    // Uptime
    if (data.uptime) {
      document.getElementById('sysUptimeVal').textContent = data.uptime.display || '--';
      if (data.uptime.boot_time) {
        document.getElementById('sysBootTimeSub').textContent = `Boot: ${data.uptime.boot_time}`;
      }
    }

    // Solar Balkonsolar (Akkustand & Hausverbrauch oben rechts)
    if (data.solar) {
      const topBat = document.getElementById('topBatVal');
      if (topBat && data.solar.battery_soc_str) {
        topBat.textContent = data.solar.battery_soc_str;
      }
      const itemBat = document.getElementById('topVitalBat');
      if (itemBat && data.solar.battery_soc !== null && data.solar.battery_soc !== undefined) {
        itemBat.classList.toggle('vital-alert', data.solar.battery_soc < 10);
      }

      const topHouse = document.getElementById('topHouseVal');
      if (topHouse && data.solar.house_power_str) {
        topHouse.textContent = data.solar.house_power_str;
      }
    }

    // Scanner Meta & Countdown
    if (data.scanner_meta) {
      document.getElementById('scanLastTime').textContent = data.scanner_meta.last_scan_time || '--';
      const sec = data.scanner_meta.next_scan_seconds || 0;
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      document.getElementById('scanCountdown').textContent = `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    }
    if (data.discovered_servers) {
      latestDiscoveredServers = data.discovered_servers;
      const countEl = document.getElementById('scanFoundCount');
      if (countEl) countEl.textContent = data.discovered_servers.length;
      if (currentCategory === 'services' || !lastServicesFingerprint) {
        updateServicesCards(data.discovered_servers);
      }
    }

    // LAN IP
    if (data.lan_ip) {
      const el = document.getElementById('sysLanIp');
      if (el) el.textContent = data.lan_ip;
    }

    // 9Router Telemetrie Sync
    if (data.nine_router) {
      renderNineRouterStats(data.nine_router);
    }

    // Red Alert & Schwellwerte
    if (data.alerts) {
      updateAlertUI(data.alerts);
    }

    // Timestamp
    if (data.timestamp) {
      document.getElementById('footerTimestamp').textContent = data.timestamp;
    }

    // System & Hardware Label Sync
    if (data.system_label) {
      const el = document.getElementById('sysElbowLabel');
      if (el) el.textContent = data.system_label;
    }
    if (data.system_model) {
      const el = document.getElementById('sysHardwareModel');
      if (el) el.textContent = data.system_model;
    }
    if (data.hostname) {
      const el = document.getElementById('topTerminalHost');
      if (el) el.textContent = String(data.hostname).toUpperCase();
    }
  }

  // Responsive Diagnostic Cards für Services & Scanner rendern
  function updateServicesCards(servers) {
    if (!servers) return;
    const fp = servers.map(s => `${s.port}:${s.pid}:${s.cloudflared_url || ''}:${s.http_status || ''}`).join('|');
    if (fp === lastServicesFingerprint) return;
    lastServicesFingerprint = fp;

    const grid = document.getElementById('servicesGrid');
    if (!grid) return;
    const lanIp = initialStats?.lan_ip || '192.168.31.210';
    let html = '';
    servers.forEach(s => {
      // Grundsatz: NIEMALS 127.0.0.1 anzeigen
      const cleanLanUrl = s.lan_url ? s.lan_url.replace("127.0.0.1", lanIp) : `http://${lanIp}:${s.port}`;
      const cleanTsUrl = s.tailscale_url ? s.tailscale_url.replace("127.0.0.1", lanIp) : '';

      const tailscaleBtn = cleanTsUrl
        ? `<a href="${cleanTsUrl}" target="_blank" class="url-chip-btn chip-ts"><span>🌐</span> <span>TS: ${cleanTsUrl}</span></a>`
        : '';

      const cfBtn = s.cloudflared_url
        ? `<a href="${s.cloudflared_url}" target="_blank" class="url-chip-btn chip-cf"><span>☁️</span> <span>CF: ${s.cloudflared_url}</span></a>`
        : `<span style="font-size:0.75rem; color:var(--c-gold); padding-left:0.2rem;">☁️ Tunnel wird initialisiert...</span>`;

      const stopActionHtml = (s.port === 5000)
        ? `<span class="system-kernel-badge">🔒 SYSTEM KERNEL</span>`
        : `<button class="btn-stop-service" onclick="stopService(${s.pid || 0}, ${s.port}, '${(s.title || 'Dienst').replace(/'/g, "\\'")}')">
             <span>🛑</span> <span>PROZESS BEENDEN</span>
           </button>`;

      html += `
        <div class="lcars-card">
          <div class="card-head">
            <span class="card-head-title">${s.title}</span>
            <span class="card-head-icon">${s.icon || '🌐'}</span>
          </div>
          <div>
            <span class="badge-status badge-online">
              PORT ${s.port} // ${s.http_status || 200} OK
            </span>
          </div>
          <div class="cmd-text-box" title="${s.cmdline || ''}">
            PID ${s.pid || 'N/A'} [${s.process_name || 'N/A'}] // ${s.cmdline || ''}
          </div>
          <div class="card-action-links">
            <a href="${cleanLanUrl}" target="_blank" class="url-chip-btn">
              <span>🏠</span> <span>LAN: ${cleanLanUrl}</span>
            </a>
            ${tailscaleBtn}
            ${cfBtn}
          </div>
          <div class="card-service-footer">
            ${stopActionHtml}
          </div>
        </div>
      `;
    });
    grid.innerHTML = html;
  }

  // Dienst über API beenden
  async function stopService(pid, port, name) {
    if (!confirm(`Möchtest du den Dienst "${name}" (Port ${port}, PID ${pid}) wirklich beenden?`)) {
      return;
    }
    playLcarsBeep(440, 220);
    try {
      const resp = await fetch('/api/services/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pid: pid, port: port })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        setTimeout(() => triggerWebserverScan(), 400);
      } else {
        alert("Fehler beim Beenden: " + (data.error || "Unbekannter Fehler"));
      }
    } catch (e) {
      alert("Netzwerkfehler beim Beenden: " + e.message);
    }
  }

  // Manueller Sofort-Scan Trigger
  async function triggerWebserverScan() {
    playLcarsBeep(1200, 2400);
    const btn = document.getElementById('btnScanTrigger');
    const label = document.getElementById('btnScanLabel');
    if (btn) btn.disabled = true;
    if (label) label.textContent = 'SCAN LÄUFT...';

    try {
      const resp = await fetch('/api/scan-webservers');
      if (resp.ok) {
        const data = await resp.json();
        if (data.discovered) updateServicesCards(data.discovered);
        fetchLiveStats(true);
      }
    } catch (e) {
      console.warn("Scan Fehler:", e);
    } finally {
      if (btn) btn.disabled = false;
      if (label) label.textContent = 'JETZT SCANNEN';
    }
  }

  // Live Stats Polling
  let refreshIntervalMs = 3000;
  let refreshTimer = null;
  let isFetchingStats = false;

  async function fetchLiveStats(playSound = false) {
    if (document.hidden) return;
    if (isFetchingStats) return;
    isFetchingStats = true;
    if (playSound) playLcarsBeep(1400, 900);
    try {
      const resp = await fetch('/api/stats');
      if (resp.ok) {
        const data = await resp.json();
        renderStats(data);
      }
    } catch (e) {
      console.warn("Telemetrie Fetch Fehler:", e);
    } finally {
      isFetchingStats = false;
    }
  }

  function setRefreshInterval(ms) {
    refreshIntervalMs = ms;
    const rateEl = document.getElementById('refreshRateDisplay');
    if (rateEl) rateEl.textContent = (ms / 1000) + ' SEKUNDEN';
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => fetchLiveStats(false), refreshIntervalMs);
  }
  refreshTimer = setInterval(() => fetchLiveStats(false), refreshIntervalMs);

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      fetchLiveStats(false);
      if (currentCategory === 'fantasy' && typeof loadFantasyData === 'function') loadFantasyData(false);
      if (currentCategory === 'homeassistant' && typeof loadHomeAssistantData === 'function') loadHomeAssistantData(false);
      if (currentCategory === 'solar' && typeof loadSolarData === 'function') loadSolarData(false);
    }
  });

  // ---------------------------------------------------------------------------
  // CHART ENGINE: DUAL CHART.JS + NATIVES LCARS CANVAS (100% GARANTIE)
  // ---------------------------------------------------------------------------
  function ensureChart(cb, attempts = 30) {
    if (typeof Chart !== 'undefined') {
      try { cb(); } catch (e) { console.error('Chart init exception:', e); }
      return;
    }
    if (attempts > 0) {
      setTimeout(() => ensureChart(cb, attempts - 1), 50);
    } else {
      console.warn('Chart.js nicht verfügbar - aktiviere nativen LCARS Canvas Renderer.');
      try { cb(); } catch (e) { console.error('Fallback exception:', e); }
    }
  }

  // 1. Systemverlauf (Line Chart)
  function initHistoryChart() {
    const canvas = document.getElementById('historyChart');
    if (!canvas) return;

    const samples = currentHistorySamples;
    const labels = samples.map(s => s.time || '');
    const temps = samples.map(s => s.temp);
    const cpus = samples.map(s => s.cpu);
    const throttles = samples.map(s => s.throttled ? s.temp : null);
    const rams = samples.map(s => s.ram);

    if (typeof Chart !== 'undefined') {
      try {
        if (historyChart) {
          historyChart.data.labels = labels;
          historyChart.data.datasets[0].data = temps;
          historyChart.data.datasets[1].data = cpus;
          historyChart.data.datasets[2].data = throttles;
          historyChart.data.datasets[3].data = rams;
          historyChart.update('none');
          return;
        }
        const existingChart = Chart.getChart(canvas);
        if (existingChart) {
          try { existingChart.destroy(); } catch (e) {}
        }
        historyChart = new Chart(canvas, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [
              {
                label: 'Temperatur (°C)',
                data: temps,
                borderColor: '#eb943a',
                backgroundColor: 'rgba(235, 148, 58, 0.12)',
                borderWidth: 2,
                tension: 0.25,
                pointRadius: labels.length > 40 ? 2 : 4,
                pointHoverRadius: 6,
                pointBackgroundColor: '#eb943a',
                fill: false,
                yAxisID: 'yTemp'
              },
              {
                label: 'CPU-Auslastung (%)',
                data: cpus,
                borderColor: '#8899ff',
                backgroundColor: 'rgba(136, 153, 255, 0.12)',
                borderWidth: 2,
                tension: 0.25,
                pointRadius: labels.length > 40 ? 2 : 4,
                pointHoverRadius: 6,
                pointBackgroundColor: '#8899ff',
                fill: false,
                yAxisID: 'yPercent'
              },
              {
                label: 'Throttling Aktiv',
                data: throttles,
                borderColor: '#cf4f4f',
                backgroundColor: '#cf4f4f',
                showLine: false,
                pointRadius: 6,
                yAxisID: 'yTemp'
              },
              {
                label: 'RAM (%)',
                data: rams,
                borderColor: '#baa4e5',
                backgroundColor: 'rgba(186, 164, 229, 0.12)',
                borderWidth: 2,
                tension: 0.25,
                pointRadius: labels.length > 40 ? 2 : 4,
                pointHoverRadius: 6,
                pointBackgroundColor: '#baa4e5',
                fill: false,
                hidden: !visibleDatasets[3],
                yAxisID: 'yPercent'
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 350 },
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: '#000',
                borderColor: '#eb943a',
                borderWidth: 1,
                titleFont: { family: 'Antonio', size: 14 },
                bodyFont: { family: 'Share Tech Mono', size: 13 }
              }
            },
            scales: {
              x: {
                grid: { color: 'rgba(255, 255, 255, 0.08)' },
                ticks: { color: '#aaa', font: { family: 'Share Tech Mono' }, maxRotation: 0 }
              },
              yPercent: {
                position: 'left',
                min: 0,
                max: 100,
                grid: { color: 'rgba(255, 255, 255, 0.08)' },
                ticks: { color: '#8899ff', font: { family: 'Share Tech Mono' }, callback: v => v + '%' }
              },
              yTemp: {
                position: 'right',
                min: 20,
                max: 85,
                grid: { drawOnChartArea: false },
                ticks: { color: '#eb943a', font: { family: 'Share Tech Mono' }, callback: v => v + '°C' }
              }
            }
          }
        });

        if (samples.length === 0) {
          fetchHistoryData(activeRange);
        }
        return;
      } catch (e) {
        console.error('Chart.js history init error:', e);
      }
    }

    // Nativer Canvas Fallback
    renderNativeHistoryChart(canvas, samples);
    if (samples.length === 0) {
      fetchHistoryData(activeRange);
    }
  }

  // Nativer HTML5 2D Canvas Fallback für History-Graph
  function renderNativeHistoryChart(canvas, samples) {
    if (!canvas) return;
    const parent = canvas.parentElement;
    const w = parent ? parent.clientWidth : 600;
    const h = parent ? parent.clientHeight : 320;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    if (!samples || samples.length === 0) {
      ctx.fillStyle = '#eb943a';
      ctx.font = '14px "Share Tech Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText('ODN DATENBANK: LADEN...', w / 2, h / 2);
      return;
    }

    const padLeft = 45;
    const padRight = 50;
    const padTop = 20;
    const padBottom = 28;
    const plotW = Math.max(10, w - padLeft - padRight);
    const plotH = Math.max(10, h - padTop - padBottom);

    // Gitterlinien
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padTop + (plotH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(padLeft + plotW, y);
      ctx.stroke();

      const pct = Math.round(100 - i * 25);
      ctx.fillStyle = '#8899ff';
      ctx.font = '11px "Share Tech Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText(pct + '%', padLeft - 6, y + 4);

      const tempVal = Math.round(85 - i * (65 / 4));
      ctx.fillStyle = '#eb943a';
      ctx.textAlign = 'left';
      ctx.fillText(tempVal + '°C', padLeft + plotW + 6, y + 4);
    }

    const n = samples.length;
    const getX = (idx) => padLeft + (n > 1 ? (idx / (n - 1)) * plotW : plotW / 2);
    const getYPercent = (val) => padTop + plotH - ((val || 0) / 100) * plotH;
    const getYTemp = (val) => padTop + plotH - (((val || 20) - 20) / 65) * plotH;

    // Zeitstempel unten
    ctx.fillStyle = '#888';
    ctx.font = '10px "Share Tech Mono", monospace';
    ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) {
      if (samples[i] && samples[i].time) {
        ctx.fillText(samples[i].time, getX(i), h - 8);
      }
    }

    // Dataset 0: Temperatur (Orange)
    if (visibleDatasets[0]) {
      ctx.beginPath();
      ctx.strokeStyle = '#eb943a';
      ctx.lineWidth = 2.5;
      samples.forEach((s, i) => {
        const x = getX(i);
        const y = getYTemp(s.temp);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = '#eb943a';
      samples.forEach((s, i) => {
        ctx.beginPath();
        ctx.arc(getX(i), getYTemp(s.temp), 2.5, 0, Math.PI * 2);
        ctx.fill();
      });
    }

    // Dataset 1: CPU (Blau)
    if (visibleDatasets[1]) {
      ctx.beginPath();
      ctx.strokeStyle = '#8899ff';
      ctx.lineWidth = 2;
      samples.forEach((s, i) => {
        const x = getX(i);
        const y = getYPercent(s.cpu);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = '#8899ff';
      samples.forEach((s, i) => {
        ctx.beginPath();
        ctx.arc(getX(i), getYPercent(s.cpu), 2.5, 0, Math.PI * 2);
        ctx.fill();
      });
    }

    // Dataset 2: Throttling (Rot)
    if (visibleDatasets[2]) {
      ctx.fillStyle = '#cf4f4f';
      samples.forEach((s, i) => {
        if (s.throttled) {
          ctx.beginPath();
          ctx.arc(getX(i), getYTemp(s.temp), 5, 0, Math.PI * 2);
          ctx.fill();
        }
      });
    }

    // Dataset 3: RAM (Lila)
    if (visibleDatasets[3]) {
      ctx.beginPath();
      ctx.strokeStyle = '#baa4e5';
      ctx.lineWidth = 2;
      samples.forEach((s, i) => {
        const x = getX(i);
        const y = getYPercent(s.ram);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
  }

  async function fetchHistoryData(range) {
    try {
      const resp = await fetch(`/api/history?range=${range}`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.samples) return;

      currentHistorySamples = data.samples;
      const labels = data.samples.map(s => s.time);
      const temps = data.samples.map(s => s.temp);
      const cpus = data.samples.map(s => s.cpu);
      const throttles = data.samples.map(s => s.throttled ? s.temp : null);
      const rams = data.samples.map(s => s.ram);

      if (historyChart) {
        historyChart.data.labels = labels;
        historyChart.data.datasets[0].data = temps;
        historyChart.data.datasets[1].data = cpus;
        historyChart.data.datasets[2].data = throttles;
        historyChart.data.datasets[3].data = rams;
        historyChart.update();
      } else {
        const canvas = document.getElementById('historyChart');
        if (canvas) renderNativeHistoryChart(canvas, data.samples);
      }
    } catch (e) {
      console.warn("History Fetch Fehler:", e);
    }
  }

  function setChartRange(range, btnElem) {
    playLcarsBeep(1100, 1600);
    activeRange = range;
    document.querySelectorAll('.range-btn').forEach(btn => btn.classList.remove('active-range'));
    if (btnElem) {
      btnElem.classList.add('active-range');
    }
    fetchHistoryData(range);
  }

  function toggleDataset(idx) {
    visibleDatasets[idx] = !visibleDatasets[idx];
    playLcarsBeep(880, 1320);

    const btn = document.getElementById('dsBtn' + idx);
    if (btn) {
      btn.style.opacity = visibleDatasets[idx] ? '1' : '0.35';
    }

    if (historyChart) {
      historyChart.setDatasetVisibility(idx, visibleDatasets[idx]);
      historyChart.update();
    } else {
      const canvas = document.getElementById('historyChart');
      if (canvas) renderNativeHistoryChart(canvas, currentHistorySamples);
    }
  }

  // ---------------------------------------------------------------------------
  // 9ROUTER TELEMETRIE, CHARTS & TABELLEN ENGINE
  // ---------------------------------------------------------------------------
  function renderNineRouterStats(nrData) {
    if (!nrData) return;
    currentNineRouterData = nrData;

    // Totals & Metriken
    const totals = nrData.totals || {};
    const reqEl = document.getElementById('nrTotalRequests');
    if (reqEl) reqEl.textContent = totals.requests || 0;

    const tokEl = document.getElementById('nrTotalTokens');
    if (tokEl) tokEl.textContent = totals.tokens_formatted || (totals.total_tokens ? Number(totals.total_tokens).toLocaleString() : '0');

    const promptComplEl = document.getElementById('nrPromptComplTokens');
    if (promptComplEl) {
      promptComplEl.textContent = `Prompt: ${totals.prompt_formatted || totals.prompt_tokens || '0'} | Compl: ${totals.completion_formatted || totals.completion_tokens || '0'}`;
    }

    const cachedEl = document.getElementById('nrCachedTokens');
    if (cachedEl) {
      const rateStr = totals.cache_hit_rate ? ` (${totals.cache_hit_rate}% Quote)` : '';
      cachedEl.textContent = `Cached: ${totals.cached_formatted || totals.cached_tokens || '0'}${rateStr}`;
    }

    const savingsRateEl = document.getElementById('nrSavingsRate');
    if (savingsRateEl) {
      savingsRateEl.textContent = totals.cache_hit_rate_formatted || (totals.cache_hit_rate ? `${totals.cache_hit_rate}%` : '0.0%');
    }

    const savedTokensEl = document.getElementById('nrSavedTokens');
    if (savedTokensEl) {
      savedTokensEl.textContent = `Tokens gespart: ${totals.saved_tokens_formatted || totals.saved_tokens || '0'}`;
    }

    const savedCostEl = document.getElementById('nrSavedCost');
    if (savedCostEl) {
      const pct = totals.saved_cost_pct ? ` (-${totals.saved_cost_pct}%)` : '';
      savedCostEl.textContent = `Kosten gespart: ~${totals.saved_cost_formatted || '$0.00'}${pct}`;
    }

    const costEl = document.getElementById('nrTotalCost');
    if (costEl) costEl.textContent = totals.cost_formatted || `$${Number(totals.cost || 0).toFixed(4)}`;

    const uncachedCostEl = document.getElementById('nrUncachedCost');
    if (uncachedCostEl) {
      uncachedCostEl.textContent = `Ohne Cache: ~${totals.uncached_cost_formatted || '$0.00'}`;
    }

    const costSavingsSubEl = document.getElementById('nrCostSavingsSub');
    if (costSavingsSubEl) {
      costSavingsSubEl.textContent = `Ersparnis: ~${totals.saved_cost_formatted || '$0.00'}`;
    }

    const connCountEl = document.getElementById('nrActiveConnCount');
    if (connCountEl) {
      connCountEl.textContent = `${(nrData.connections || []).length}`;
    }

    const provListEl = document.getElementById('nrConnProvidersList');
    if (provListEl && nrData.connections && nrData.connections.length > 0) {
      provListEl.textContent = nrData.connections.map(c => (c.provider ? c.provider.toUpperCase() : '')).join(' • ');
    }

    // Only do heavy DOM table/list updates and chart updates if 'ai-info', 'ai', '9router', 'hermes' or 'agents' is visible
    if (currentCategory !== 'ai-info' && currentCategory !== 'agents' && currentCategory !== '9router' && currentCategory !== 'hermes' && currentCategory !== 'ide' && currentCategory !== 'ai') {
      return;
    }

    // Instances / Verursacher Table Rendering with dirty-checking
    const instTbody = document.getElementById('nineRouterInstancesTableBody');
    if (instTbody && nrData.by_instance) {
      const instFp = nrData.by_instance.map(i => `${i.key_prefix}:${i.requests}:${i.total_tokens}:${i.cost}`).join('|');
      if (instFp !== lastNrInstancesFingerprint) {
        lastNrInstancesFingerprint = instFp;
        if (nrData.by_instance.length === 0) {
          instTbody.innerHTML = '<tr><td colspan="9" style="padding:1rem; text-align:center; color:#888;">Keine Instanzen-Daten erfasst.</td></tr>';
        } else {
          let iHtml = '';
          nrData.by_instance.forEach(inst => {
            const dotColor = inst.is_active ? '#00e676' : '#ff5252';
            iHtml += `
              <tr style="border-bottom:1px solid rgba(255,255,255,0.08); font-family:var(--mono-family);">
                <td style="padding:0.55rem 0.5rem; font-weight:700; color:var(--c-primary);">
                  <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${dotColor}; margin-right:6px;"></span>
                  ${escapeHtml(inst.name)}
                </td>
                <td style="padding:0.55rem 0.5rem; color:#aaa; font-size:0.8rem;" title="${escapeHtml(inst.key)}">${escapeHtml(inst.key_prefix)}</td>
                <td style="padding:0.55rem 0.5rem; text-align:right;">${Number(inst.requests || 0).toLocaleString()}</td>
                <td style="padding:0.55rem 0.5rem; text-align:right;">${escapeHtml(inst.prompt_formatted || '0')}</td>
                <td style="padding:0.55rem 0.5rem; text-align:right;">${escapeHtml(inst.completion_formatted || '0')}</td>
                <td style="padding:0.55rem 0.5rem; text-align:right; color:var(--c-blue);">${escapeHtml(inst.cached_formatted || '0')}</td>
                <td style="padding:0.55rem 0.5rem; text-align:right; font-weight:700; color:var(--c-primary);">${escapeHtml(inst.total_formatted || '0')}</td>
                <td style="padding:0.55rem 0.5rem; text-align:right; color:var(--c-gold);">${escapeHtml(inst.cost_formatted || '$0.00')}</td>
                <td style="padding:0.55rem 0.5rem; text-align:center;">
                  <div style="display:flex; align-items:center; gap:6px;">
                    <div style="flex:1; background:rgba(255,255,255,0.1); height:6px; border-radius:3px; overflow:hidden;">
                      <div style="width:${inst.percent || 0}%; background:var(--c-primary); height:100%;"></div>
                    </div>
                    <span style="font-size:0.75rem; min-width:38px; text-align:right;">${inst.percent_formatted || '0.0%'}</span>
                  </div>
                </td>
              </tr>
            `;
          });
          instTbody.innerHTML = iHtml;
        }
      }
    }

    // Provider Connections List Rendering with dirty-checking
    const connList = document.getElementById('nineRouterConnectionsList');
    if (connList && nrData.connections) {
      const connFp = nrData.connections.map(c => `${c.provider}:${c.is_active}:${c.priority}`).join('|');
      if (connFp !== lastNrConnFingerprint) {
        lastNrConnFingerprint = connFp;
        if (nrData.connections.length === 0) {
          connList.innerHTML = '<div style="color:#888; font-size:0.85rem; padding:0.5rem;">Keine Provider-Verbindungen konfiguriert.</div>';
        } else {
          let cHtml = '';
          nrData.connections.forEach(conn => {
            const badgeCls = conn.is_active ? 'badge-online' : 'badge-offline';
            const badgeTxt = conn.is_active ? 'AKTIV // VERBUNDEN' : 'INAKTIV';
            cHtml += `
              <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.5); padding:0.6rem 0.8rem; border-left:4px solid var(--c-primary); border-radius:6px; flex-wrap:wrap; gap:0.5rem;">
                <div>
                  <div style="font-weight:700; color:var(--c-primary); font-size:0.95rem;">${escapeHtml(conn.provider.toUpperCase())} // ${escapeHtml(conn.name || conn.provider)}</div>
                  <div style="font-family:var(--mono-family); font-size:0.78rem; color:#888;">Auth: ${escapeHtml((conn.auth_type || '').toUpperCase())} | Prio: ${conn.priority} ${conn.email ? '| ' + escapeHtml(conn.email) : ''}</div>
                </div>
                <div>
                  <span class="badge-status ${badgeCls}">${badgeTxt}</span>
                </div>
              </div>
            `;
          });
          connList.innerHTML = cHtml;
        }
      }
    }

    // Recent History Table Rendering with dirty-checking
    const tbody = document.getElementById('nineRouterHistoryTableBody');
    if (tbody && nrData.recent_history) {
      const histFp = (nrData.recent_history || []).map(r => `${r.id}:${r.status}:${r.total_tokens}`).join('|');
      if (histFp !== lastNrHistoryFingerprint) {
        lastNrHistoryFingerprint = histFp;
        if (nrData.recent_history.length === 0) {
          tbody.innerHTML = '<tr><td colspan="10" style="padding:0.8rem; text-align:center; color:#888;">Keine Transaktionen aufgezeichnet.</td></tr>';
        } else {
          let hHtml = '';
          nrData.recent_history.forEach(r => {
            const badgeClass = (r.status === 'ok') ? 'badge-online' : 'badge-offline';
            hHtml += `
              <tr style="border-bottom:1px solid rgba(255,255,255,0.08); font-family:var(--mono-family);">
                <td style="padding:0.45rem 0.4rem; color:var(--c-gold);">#${r.id}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:#ccc;">${escapeHtml(r.time_display)}</td>
                <td style="padding:0.45rem 0.4rem;"><span style="color:var(--c-blue); font-weight:700;">${escapeHtml((r.provider || '').toUpperCase())}</span></td>
                <td style="padding:0.45rem 0.4rem; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(r.model)}">${escapeHtml(r.model)}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap;">${Number(r.prompt_tokens || 0).toLocaleString()}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap;">${Number(r.completion_tokens || 0).toLocaleString()}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:var(--c-blue);">${Number(r.cached_tokens || 0).toLocaleString()}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap; font-weight:700; color:var(--c-primary);">${Number(r.total_tokens || 0).toLocaleString()}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:var(--c-gold);">${escapeHtml(r.cost_formatted || ('$' + Number(r.cost || 0).toFixed(4)))}</td>
                <td style="padding:0.45rem 0.4rem;">
                  <span class="badge-status ${badgeClass}" style="padding:0.15rem 0.4rem; font-size:0.75rem;">
                    ${escapeHtml((r.status || 'OK').toUpperCase())}
                  </span>
                </td>
              </tr>
            `;
          });
          tbody.innerHTML = hHtml;
        }
      }
    }

    // Charts nur im aktiven Tab 'ai-info' aktualisieren
    if (currentCategory === 'ai-info' && (nineRouterModelChart || nineRouterTimelineChart)) {
      initNineRouterCharts(nrData);
    }
  }

  async function fetchNineRouterStats(playSound = false) {
    if (playSound) playLcarsBeep(1400, 900);
    try {
      const resp = await fetch('/api/9router/stats');
      if (resp.ok) {
        const data = await resp.json();
        renderNineRouterStats(data);
        initNineRouterCharts(data);
      }
    } catch (e) {
      console.warn("9Router Fetch Fehler:", e);
    }
  }

  function initNineRouterCharts(nrData) {
    if (nrData) currentNineRouterData = nrData;
    const data = currentNineRouterData || initialStats?.nine_router;
    if (!data) return;

    initNineRouterModelChart(data);
    initNineRouterTimelineChart(data);
  }

  function initNineRouterModelChart(data) {
    const canvas = document.getElementById('nineRouterModelChart');
    if (!canvas) return;

    const modelsObj = data?.by_model || {};
    let labels = Object.keys(modelsObj);
    let values = labels.map(k => (modelsObj[k].promptTokens || 0) + (modelsObj[k].completionTokens || 0));
    const palette = ['#eb943a', '#baa4e5', '#8899ff', '#faad44', '#10b981', '#cf4f4f', '#06b6d4'];

    if (!labels.length || values.every(v => v === 0)) {
      labels = ['Keine Daten'];
      values = [1];
    }

    if (typeof Chart !== 'undefined') {
      try {
        if (nineRouterModelChart) {
          nineRouterModelChart.data.labels = labels;
          nineRouterModelChart.data.datasets[0].data = values;
          nineRouterModelChart.data.datasets[0].backgroundColor = palette.slice(0, labels.length);
          nineRouterModelChart.update('none');
          return;
        }
        const existing = Chart.getChart(canvas);
        if (existing) {
          try { existing.destroy(); } catch (e) {}
        }
        nineRouterModelChart = new Chart(canvas, {
          type: 'doughnut',
          data: {
            labels: labels,
            datasets: [{
              data: values,
              backgroundColor: palette.slice(0, labels.length),
              borderColor: '#000000',
              borderWidth: 2
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '68%',
            animation: { duration: 350 },
            plugins: {
              legend: {
                display: true,
                position: 'bottom',
                labels: {
                  color: '#ccc',
                  font: { family: 'Share Tech Mono', size: 11 },
                  boxWidth: 12
                }
              },
              tooltip: {
                backgroundColor: '#000',
                borderColor: '#eb943a',
                borderWidth: 1,
                titleFont: { family: 'Antonio', size: 14 },
                bodyFont: { family: 'Share Tech Mono', size: 12 },
                callbacks: {
                  label: function(ctx) {
                    const val = ctx.raw || 0;
                    return ` ${ctx.label}: ${val.toLocaleString()} Tokens`;
                  }
                }
              }
            }
          }
        });
        return;
      } catch (e) {
        console.error('9Router Model Chart error:', e);
      }
    }
  }

  function initNineRouterTimelineChart(data) {
    const canvas = document.getElementById('nineRouterTimelineChart');
    if (!canvas) return;

    const history = (data?.recent_history || []).slice().reverse();
    const labels = history.map(r => {
      if (r.time_display) {
        const parts = r.time_display.split(' ');
        return parts.length > 1 ? parts[1] : parts[0];
      }
      return '#' + r.id;
    });

    const promptToks = history.map(r => r.prompt_tokens || 0);
    const cachedToks = history.map(r => r.cached_tokens || 0);
    const complToks = history.map(r => r.completion_tokens || 0);
    const costs = history.map(r => r.cost || 0);

    if (typeof Chart !== 'undefined') {
      try {
        if (nineRouterTimelineChart) {
          nineRouterTimelineChart.data.labels = labels.length ? labels : ['Keine Daten'];
          nineRouterTimelineChart.data.datasets[0].data = promptToks.length ? promptToks : [0];
          nineRouterTimelineChart.data.datasets[1].data = cachedToks.length ? cachedToks : [0];
          nineRouterTimelineChart.data.datasets[2].data = complToks.length ? complToks : [0];
          nineRouterTimelineChart.data.datasets[3].data = costs.length ? costs : [0];
          nineRouterTimelineChart.update('none');
          return;
        }
        const existing = Chart.getChart(canvas);
        if (existing) {
          try { existing.destroy(); } catch (e) {}
        }
        nineRouterTimelineChart = new Chart(canvas, {
          type: 'bar',
          data: {
            labels: labels.length ? labels : ['Keine Daten'],
            datasets: [
              {
                label: 'Prompt Tokens',
                data: promptToks.length ? promptToks : [0],
                backgroundColor: 'rgba(235, 148, 58, 0.8)',
                borderColor: '#eb943a',
                borderWidth: 1,
                stack: 'tokens',
                yAxisID: 'y'
              },
              {
                label: 'Cached Tokens',
                data: cachedToks.length ? cachedToks : [0],
                backgroundColor: 'rgba(16, 185, 129, 0.8)',
                borderColor: '#10b981',
                borderWidth: 1,
                stack: 'tokens',
                yAxisID: 'y'
              },
              {
                label: 'Completion Tokens',
                data: complToks.length ? complToks : [0],
                backgroundColor: 'rgba(136, 153, 255, 0.8)',
                borderColor: '#8899ff',
                borderWidth: 1,
                stack: 'tokens',
                yAxisID: 'y'
              },
              {
                label: 'Kosten ($)',
                type: 'line',
                data: costs.length ? costs : [0],
                borderColor: '#faad44',
                backgroundColor: '#faad44',
                borderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 6,
                tension: 0.2,
                yAxisID: 'yCost'
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 350 },
            plugins: {
              legend: {
                display: true,
                position: 'top',
                labels: {
                  color: '#ccc',
                  font: { family: 'Share Tech Mono', size: 11 },
                  boxWidth: 12
                }
              },
              tooltip: {
                backgroundColor: '#000',
                borderColor: '#eb943a',
                borderWidth: 1,
                titleFont: { family: 'Antonio', size: 14 },
                bodyFont: { family: 'Share Tech Mono', size: 12 },
                callbacks: {
                  label: function(ctx) {
                    if (ctx.dataset.yAxisID === 'yCost') {
                      return ` Kosten: $${Number(ctx.raw || 0).toFixed(4)}`;
                    }
                    return ` ${ctx.dataset.label}: ${Number(ctx.raw || 0).toLocaleString()} Tokens`;
                  }
                }
              }
            },
            scales: {
              x: {
                stacked: true,
                grid: { color: 'rgba(255, 255, 255, 0.07)' },
                ticks: { color: '#aaa', font: { family: 'Share Tech Mono', size: 11 } }
              },
              y: {
                stacked: true,
                grid: { color: 'rgba(255, 255, 255, 0.07)' },
                ticks: {
                  color: '#aaa',
                  font: { family: 'Share Tech Mono', size: 11 },
                  callback: function(val) {
                    if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
                    if (val >= 1000) return (val / 1000).toFixed(0) + 'k';
                    return val;
                  }
                }
              },
              yCost: {
                position: 'right',
                grid: { drawOnChartArea: false },
                ticks: {
                  color: '#faad44',
                  font: { family: 'Share Tech Mono', size: 11 },
                  callback: function(val) {
                    return '$' + Number(val).toFixed(4);
                  }
                }
              }
            }
          }
        });
        return;
      } catch (e) {
        console.error('9Router Timeline Chart error:', e);
      }
    }
  }

  // 2. Hermes Donut Chart (Modell Token-Verteilung)
  function initHermesChart() {
    const canvas = document.getElementById('hermesChart');
    if (!canvas) return;

    const models = initialStats?.hermes?.models || [];
    let labels = models.map(m => m.model);
    let data = models.map(m => m.total_tokens);
    const palette = ['#eb943a', '#baa4e5', '#8899ff', '#ea9c72', '#edb378', '#cf4f4f', '#10b981'];

    if (!data.length || data.every(v => v === 0)) {
      labels = ['Keine Daten'];
      data = [1];
    }

    if (typeof Chart !== 'undefined') {
      try {
        if (hermesChart) {
          hermesChart.data.labels = labels;
          hermesChart.data.datasets[0].data = data;
          hermesChart.data.datasets[0].backgroundColor = palette.slice(0, labels.length);
          hermesChart.update('none');
          return;
        }
        const existingChart = Chart.getChart(canvas);
        if (existingChart) {
          try { existingChart.destroy(); } catch (e) {}
        }
        hermesChart = new Chart(canvas, {
          type: 'doughnut',
          data: {
            labels: labels,
            datasets: [{
              data: data,
              backgroundColor: palette.slice(0, labels.length),
              borderColor: '#000000',
              borderWidth: 2
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '68%',
            animation: { duration: 350 },
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: '#000',
                borderColor: '#baa4e5',
                borderWidth: 1,
                titleFont: { family: 'Antonio', size: 14 },
                bodyFont: { family: 'Share Tech Mono', size: 12 }
              }
            }
          }
        });
        return;
      } catch (e) {
        console.error('Hermes Chart.js error:', e);
      }
    }

    // Nativer Canvas Fallback
    renderNativeDonutChart(canvas, models);
  }

  function renderNativeDonutChart(canvas, models) {
    if (!canvas) return;
    const parent = canvas.parentElement;
    const size = Math.min(parent ? parent.clientWidth : 260, parent ? parent.clientHeight : 260) || 260;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, size, size);

    const cx = size / 2;
    const cy = size / 2;
    const outerR = size * 0.46;
    const innerR = size * 0.32;

    const palette = ['#eb943a', '#baa4e5', '#8899ff', '#ea9c72', '#edb378', '#cf4f4f', '#10b981'];
    const total = (models || []).reduce((acc, m) => acc + (m.total_tokens || 0), 0) || 1;

    let startAngle = -Math.PI / 2;
    if (models && models.length > 0) {
      models.forEach((m, idx) => {
        const slice = ((m.total_tokens || 0) / total) * Math.PI * 2;
        if (slice <= 0) return;
        const endAngle = startAngle + slice;
        ctx.beginPath();
        ctx.arc(cx, cy, outerR, startAngle, endAngle);
        ctx.arc(cx, cy, innerR, endAngle, startAngle, true);
        ctx.closePath();
        ctx.fillStyle = palette[idx % palette.length];
        ctx.fill();
        ctx.strokeStyle = '#000000';
        ctx.lineWidth = 2;
        ctx.stroke();
        startAngle = endAngle;
      });
    } else {
      ctx.beginPath();
      ctx.arc(cx, cy, outerR, 0, Math.PI * 2);
      ctx.arc(cx, cy, innerR, Math.PI * 2, 0, true);
      ctx.closePath();
      ctx.fillStyle = '#333333';
      ctx.fill();
    }

    // Zentrierte Beschriftung
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 15px "Antonio", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('TOKENS', cx, cy - 8);

    ctx.fillStyle = '#eb943a';
    ctx.font = '12px "Share Tech Mono", monospace';
    const totalStr = total > 1000000 ? (total / 1000000).toFixed(1) + 'M' : (total / 1000).toFixed(0) + 'k';
    ctx.fillText(totalStr, cx, cy + 10);
  }

  // ---------------------------------------------------------------------------
  // LCARS KI-AGENTEN CHAT ENGINE
  // ---------------------------------------------------------------------------
  let chatHistory = [];
  let chatModels = ['ag/gemini-3-flash', 'openrouter/openrouter/free'];
  let currentChatModel = localStorage.getItem('lcars-chat-model') || 'ag/gemini-3-flash';
  let isChatGenerating = false;

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatTimeNow() {
    const d = new Date();
    return String(d.getHours()).padStart(2, '0') + ':' +
           String(d.getMinutes()).padStart(2, '0') + ':' +
           String(d.getSeconds()).padStart(2, '0');
  }

  function formatMessageText(text) {
    if (!text) return '';
    const codeBlocks = [];
    let processed = text.replace(/```([a-zA-Z0-9_-]*)\\n?([\\s\\S]*?)```/g, (match, lang, code) => {
      const idx = codeBlocks.length;
      codeBlocks.push(`<pre class="lcars-code-block"><code>${escapeHtml(code.trim())}</code></pre>`);
      return `###CODEBLOCK_${idx}###`;
    });

    processed = escapeHtml(processed);

    processed = processed.replace(/`([^`]+)`/g, (match, code) => {
      return `<code class="lcars-inline-code">${code}</code>`;
    });

    processed = processed.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');

    codeBlocks.forEach((block, idx) => {
      processed = processed.replace(`###CODEBLOCK_${idx}###`, block);
    });

    return processed;
  }

  function appendChatMessage(role, content, modelName = null, isError = false) {
    const log = document.getElementById('lcarsChatLog');
    if (!log) return;

    const msgDiv = document.createElement('div');
    const timeStr = formatTimeNow();

    let cssClass = 'lcars-msg ';
    let senderLabel = '';

    if (isError) {
      cssClass += 'lcars-msg-error';
      senderLabel = '⚠️ SYSTEM // WARNUNG';
    } else if (role === 'user') {
      cssClass += 'lcars-msg-user';
      senderLabel = '▶ USER // ODN-TERMINAL';
    } else {
      cssClass += 'lcars-msg-agent';
      senderLabel = `▶ AGENT // ${escapeHtml(modelName || currentChatModel)}`;
    }

    msgDiv.className = cssClass;
    msgDiv.innerHTML = `
      <div class="lcars-msg-header">
        <span class="lcars-msg-sender">${senderLabel}</span>
        <span class="lcars-msg-time">${timeStr}</span>
      </div>
      <div class="lcars-msg-body">${formatMessageText(content)}</div>
    `;

    log.appendChild(msgDiv);
    log.scrollTop = log.scrollHeight;
  }

  async function loadChatModels(playSound = false) {
    if (playSound) playLcarsBeep(1200, 1800);
    try {
      const resp = await fetch('/api/chat/models');
      if (resp.ok) {
        const data = await resp.json();
        let models = [];
        if (Array.isArray(data)) {
          models = data;
        } else if (Array.isArray(data.models)) {
          models = data.models;
        } else if (Array.isArray(data.data)) {
          models = data.data.map(m => typeof m === 'string' ? m : (m.id || m.name));
        }

        if (models && models.length > 0) {
          chatModels = models;
          const select = document.getElementById('chatModelSelect');
          if (select) {
            const saved = localStorage.getItem('lcars-chat-model') || currentChatModel;
            select.innerHTML = '';
            models.forEach(m => {
              const opt = document.createElement('option');
              opt.value = m;
              opt.textContent = m;
              if (m === saved) opt.selected = true;
              select.appendChild(opt);
            });
            if (select.value) {
              currentChatModel = select.value;
            }
          }
        }

        const badge = document.getElementById('chatProxyStatusBadge');
        const text = document.getElementById('chatProxyStatusText');
        if (badge && text) {
          if (data.status === 'online' || !data.status) {
            badge.className = 'badge-status badge-online';
            text.textContent = 'PROXY BEREIT';
          } else {
            badge.className = 'badge-status badge-warn';
            text.textContent = 'FALLBACK MODUS';
          }
        }
      }
    } catch (e) {
      console.warn("Konnte KI-Modelle nicht abrufen:", e);
      const badge = document.getElementById('chatProxyStatusBadge');
      const text = document.getElementById('chatProxyStatusText');
      if (badge && text) {
        badge.className = 'badge-status badge-offline';
        text.textContent = 'OFFLINE';
      }
    }
  }

  function onChatModelChange() {
    playLcarsBeep(980, 1400);
    const select = document.getElementById('chatModelSelect');
    if (select) {
      currentChatModel = select.value;
      localStorage.setItem('lcars-chat-model', currentChatModel);
      const meta = document.getElementById('chatMetaStatus');
      if (meta) meta.textContent = `MODELL GEWÄHLT: ${currentChatModel}`;
    }
  }

  function clearChatHistory() {
    playLcarsBeep(440, 220);
    chatHistory = [];
    const log = document.getElementById('lcarsChatLog');
    if (log) {
      const timeStr = formatTimeNow();
      log.innerHTML = `
        <div class="lcars-msg lcars-msg-agent">
          <div class="lcars-msg-header">
            <span class="lcars-msg-sender">▶ AGENT // SUBRAUM-COMM</span>
            <span class="lcars-msg-time">${timeStr}</span>
          </div>
          <div class="lcars-msg-body">Kanal zurückgesetzt. Neuer Dialog initialisiert. Bereit für neue Befehle.</div>
        </div>
      `;
    }
    const meta = document.getElementById('chatMetaStatus');
    if (meta) meta.textContent = 'DIALOG ZURÜCKGESETZT // BEREIT';
  }

  function sendQuickPrompt(promptText) {
    const input = document.getElementById('lcarsChatInput');
    if (input) {
      input.value = promptText;
      handleChatSubmit(null);
    }
  }

  async function handleChatSubmit(event, fromVoice = false) {
    if (event) event.preventDefault();
    if (isChatGenerating) return;

    const input = document.getElementById('lcarsChatInput');
    const sendBtn = document.getElementById('lcarsChatSendBtn');
    const sendLabel = document.getElementById('lcarsChatSendLabel');
    const loading = document.getElementById('lcarsChatLoading');
    const meta = document.getElementById('chatMetaStatus');

    if (!input) return;
    const messageText = input.value.trim();
    if (!messageText) return;

    playLcarsBeep(1200, 1600);

    chatHistory.push({ role: 'user', content: messageText });
    appendChatMessage('user', messageText);
    input.value = '';

    isChatGenerating = true;
    input.disabled = true;
    if (sendBtn) sendBtn.disabled = true;
    if (sendLabel) sendLabel.textContent = 'TRANSMITTING...';
    if (loading) loading.style.display = 'flex';
    if (meta) meta.textContent = `TRANSMISSION IN BEARBEITUNG (${currentChatModel})...`;
    if (fromVoice) setCommBadgeState('computing');

    let messagesToSend = [...chatHistory];
    if (fromVoice || isVoiceLastInput) {
      messagesToSend = [
        {
          role: 'system',
          content: 'Du bist der LCARS Hauptcomputer eines Sternenflotten-Raumschiffs. Antworte auf Deutsch, präzise, sachlich und ruhig im Star Trek Computer-Stil. Halte deine Antwort auf maximal 2 bis 3 Sätze beschränkt, ohne Markdown-Formatierungen, da deine Antwort direkt über die Sprachausgabe vorgelesen wird.'
        },
        ...chatHistory
      ];
    }

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: currentChatModel,
          messages: messagesToSend
        })
      });

      const data = await resp.json();

      if (resp.ok) {
        const assistantText = data.content ||
                              (data.message && data.message.content) ||
                              (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) ||
                              'Keine Antwort vom Agenten erhalten.';

        chatHistory.push({ role: 'assistant', content: assistantText });
        appendChatMessage('assistant', assistantText, data.model || currentChatModel);
        playLcarsAcknowledge();

        if (meta) {
          const toks = data.usage ? ` [Tokens: ${data.usage.total_tokens || 0}]` : '';
          meta.textContent = `TRANSMISSION EMPFANGEN // MODELL: ${data.model || currentChatModel}${toks}`;
        }

        if (fromVoice || (voiceOutputEnabled && isVoiceLastInput)) {
          speakLcarsText(assistantText);
        } else {
          setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
        }
      } else {
        const errorMsg = data.error || (data.message && data.message.content) || 'Unbekannter Fehler bei Kommunikation mit KI-Proxy.';
        appendChatMessage('assistant', errorMsg, currentChatModel, true);
        playLcarsError();
        if (meta) meta.textContent = `TRANSMISSIONSFEHLER (${resp.status})`;
        if (fromVoice) speakLcarsText('Fehler bei Übertragung an Subraum-Relay.');
        setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
      }
    } catch (e) {
      appendChatMessage('assistant', `Netzwerkfehler: Verbindung zum Backend fehlgeschlagen (${e.message})`, currentChatModel, true);
      playLcarsError();
      if (meta) meta.textContent = 'NETZWERKFEHLER BEI TRANSMISSION';
      if (fromVoice) speakLcarsText('Netzwerkfehler bei Subraum-Transceiver.');
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
    } finally {
      isChatGenerating = false;
      input.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
      if (sendLabel) sendLabel.textContent = 'TRANSMIT';
      if (loading) loading.style.display = 'none';
      input.focus();
    }
  }

  // ==========================================================================
  // LCARS STAR TREK VOICE COMM-LINK ENGINE (STT, TTS, COMMANDS & VISUALIZER)
  // ==========================================================================
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let voiceRecognition = null;
  let isListening = false;
  let isVoiceLastInput = false;
  let voiceVizInterval = null;

  let voiceInputEnabled = (localStorage.getItem('lcars-voice-in') !== 'false');
  let voiceOutputEnabled = (localStorage.getItem('lcars-voice-out') !== 'false');
  let wakeWordActive = (localStorage.getItem('lcars-wakeword') === 'true');

  function initLcarsVoiceComm() {
    updateVoiceUI();
    populateVoices();
    if (wakeWordActive && voiceInputEnabled && SpeechRecognition) {
      setTimeout(() => {
        startContinuousWakeWord();
      }, 1200);
    }
  }

  function setCommBadgeState(state) {
    // state: 'idle' | 'listening' | 'speaking' | 'computing' | 'passive-listen'
    const topBadge = document.getElementById('topCommBadge');
    const topText = document.getElementById('topCommText');
    const topIcon = document.getElementById('topCommIcon');
    const chatBtn = document.getElementById('lcarsChatMicBtn');
    const chatLabel = document.getElementById('lcarsChatMicLabel');
    const hermesBtn = document.getElementById('hermesChatMicBtn');

    [topBadge, chatBtn, hermesBtn].forEach(el => {
      if (el) {
        el.classList.remove('listening', 'speaking', 'computing', 'passive-listen');
        if (state !== 'idle') el.classList.add(state);
      }
    });

    if (topText && topIcon) {
      if (state === 'listening') {
        topIcon.textContent = '🔴';
        topText.textContent = 'HÖRE ZU...';
      } else if (state === 'speaking') {
        topIcon.textContent = '🔊';
        topText.textContent = 'TRANSMITTING';
      } else if (state === 'computing') {
        topIcon.textContent = '⚙️';
        topText.textContent = 'COMPUTING...';
      } else if (state === 'passive-listen') {
        topIcon.textContent = '👂';
        topText.textContent = "WAKE: 'COMPUTER'";
      } else {
        topIcon.textContent = '🎙️';
        topText.textContent = 'COMM: BEREIT';
      }
    }

    if (chatLabel) {
      if (state === 'listening') chatLabel.textContent = 'HÖRE...';
      else if (state === 'speaking') chatLabel.textContent = 'AUDIO';
      else if (state === 'computing') chatLabel.textContent = 'WAIT...';
      else chatLabel.textContent = 'COMM';
    }
  }

  function startVoiceVisualizer(mode = 'listening') {
    stopVoiceVisualizer();
    const hud = document.getElementById('lcarsVoiceHud');
    if (hud) hud.style.display = 'flex';
    const bars = document.querySelectorAll('.lcars-voice-bar-col');
    if (!bars.length) return;

    voiceVizInterval = setInterval(() => {
      bars.forEach(bar => {
        const factor = mode === 'listening' ? 0.75 : 0.9;
        const randomH = Math.floor(Math.random() * 16 * factor) + 4;
        bar.style.height = randomH + 'px';
        if (mode === 'speaking') {
          bar.style.backgroundColor = 'var(--c-blue)';
        } else if (mode === 'listening') {
          bar.style.backgroundColor = '#ff5577';
        } else {
          bar.style.backgroundColor = 'var(--c-gold)';
        }
      });
    }, 85);
  }

  function stopVoiceVisualizer() {
    if (voiceVizInterval) {
      clearInterval(voiceVizInterval);
      voiceVizInterval = null;
    }
    const hud = document.getElementById('lcarsVoiceHud');
    if (hud && !isListening) hud.style.display = 'none';
    const bars = document.querySelectorAll('.lcars-voice-bar-col');
    bars.forEach(bar => {
      bar.style.height = '4px';
      bar.style.backgroundColor = 'var(--c-gold)';
    });
  }

  function populateVoices() {
    if (!('speechSynthesis' in window)) return;
    const voices = window.speechSynthesis.getVoices();
    const select = document.getElementById('cfgVoiceSelect');
    if (!select || !voices.length) return;

    select.innerHTML = '';
    const saved = localStorage.getItem('lcars-voice-name') || '';

    const sorted = [...voices].sort((a, b) => {
      const aDe = a.lang.startsWith('de') ? 0 : 1;
      const bDe = b.lang.startsWith('de') ? 0 : 1;
      if (aDe !== bDe) return aDe - bDe;
      return a.name.localeCompare(b.name);
    });

    sorted.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.name;
      opt.textContent = `${v.name} [${v.lang}]`;
      if (v.name === saved) opt.selected = true;
      select.appendChild(opt);
    });
  }

  if ('speechSynthesis' in window) {
    speechSynthesis.onvoiceschanged = populateVoices;
  }

  function getPreferredGermanVoice() {
    if (!('speechSynthesis' in window)) return null;
    const voices = window.speechSynthesis.getVoices();
    const saved = localStorage.getItem('lcars-voice-name');
    if (saved) {
      const match = voices.find(v => v.name === saved);
      if (match) return match;
    }
    const deVoices = voices.filter(v => v.lang.startsWith('de'));
    return deVoices.find(v => v.name.includes('Google') || v.name.includes('Katja') || v.name.includes('Hedda') || v.name.includes('Natural') || v.name.includes('Neural') || v.name.includes('Amira') || v.name.includes('Marlena')) ||
           deVoices.find(v => v.name.toLowerCase().includes('female') || v.name.toLowerCase().includes('weiblich')) ||
           deVoices[0] ||
           voices[0] || null;
  }

  function speakLcarsText(rawText, onComplete = null) {
    if (!voiceOutputEnabled) {
      if (onComplete) onComplete();
      return;
    }
    if (!('speechSynthesis' in window)) {
      if (onComplete) onComplete();
      return;
    }

    try {
      window.speechSynthesis.cancel();

      let clean = rawText.replace(/```[\s\S]*?```/g, 'Codeblock im Hauptfenster.');
      clean = clean.replace(/[*_`#]/g, '');
      clean = clean.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
      clean = clean.replace(/https?:\/\/\S+/g, 'Link');
      clean = clean.replace(/%/g, ' Prozent');
      clean = clean.replace(/°C?/g, ' Grad');
      clean = clean.replace(/\b([0-9]+)\s*W\b/g, '$1 Watt');
      clean = clean.replace(/\b([0-9]+)\s*kW\b/g, '$1 Kilowatt');
      clean = clean.replace(/\b([0-9]+)\s*Wh\b/g, '$1 Wattstunden');
      clean = clean.replace(/\b([0-9]+)\s*kWh\b/g, '$1 Kilowattstunden');
      clean = clean.replace(/\s+/g, ' ').trim();

      if (!clean) {
        if (onComplete) onComplete();
        return;
      }

      const sentences = clean.match(/[^.!?]+[.!?]+/g) || [clean];
      let spokenText = clean;
      if (sentences.length > 3) {
        spokenText = sentences.slice(0, 3).join(' ') + ' Weitere Telemetrie auf dem Hauptschirm dargestellt.';
      }

      const utterance = new SpeechSynthesisUtterance(spokenText);
      utterance.lang = 'de-DE';
      utterance.rate = 0.98;
      utterance.pitch = 1.05;

      const voice = getPreferredGermanVoice();
      if (voice) utterance.voice = voice;

      utterance.onstart = () => {
        setCommBadgeState('speaking');
        startVoiceVisualizer('speaking');
      };

      utterance.onend = () => {
        setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
        stopVoiceVisualizer();
        if (onComplete) onComplete();
      };

      utterance.onerror = () => {
        setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
        stopVoiceVisualizer();
        if (onComplete) onComplete();
      };

      playLcarsAcknowledge();
      setTimeout(() => {
        window.speechSynthesis.speak(utterance);
      }, 150);
    } catch (e) {
      console.warn("TTS Fehler:", e);
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
      stopVoiceVisualizer();
      if (onComplete) onComplete();
    }
  }

  function initSpeechRecognition() {
    if (!SpeechRecognition) return null;
    const rec = new SpeechRecognition();
    rec.lang = 'de-DE';
    rec.continuous = wakeWordActive;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onstart = () => {
      isListening = true;
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'listening');
    };

    rec.onresult = (event) => {
      let finalTranscript = '';
      let interimTranscript = '';
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        if (event.results[i].isFinal) {
          finalTranscript += event.results[i][0].transcript;
        } else {
          interimTranscript += event.results[i][0].transcript;
        }
      }

      const hudTranscript = document.getElementById('lcarsVoiceHudTranscript');
      if (hudTranscript) {
        hudTranscript.textContent = (finalTranscript || interimTranscript).trim();
      }

      if (wakeWordActive) {
        const lower = (finalTranscript || interimTranscript).toLowerCase();
        const match = lower.match(/\\b(computer|lcars)\\b(.*)/i);
        if (match && finalTranscript) {
          const command = match[2].trim();
          playLcarsChirp();
          if (command.length > 1) {
            handleVoiceCommand(command);
          } else {
            speakLcarsText("Bereit für Befehle.");
          }
        }
      } else {
        if (finalTranscript && finalTranscript.trim().length > 0) {
          handleVoiceCommand(finalTranscript.trim());
        }
      }
    };

    rec.onerror = (event) => {
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        console.warn("SpeechRecognition Fehler:", event.error);
        playLcarsError();
      }
      if (!wakeWordActive) {
        stopVoiceComm();
      }
    };

    rec.onend = () => {
      isListening = false;
      if (wakeWordActive && voiceInputEnabled) {
        try { rec.start(); } catch(e) {}
      } else {
        stopVoiceComm();
      }
    };

    return rec;
  }

  function startContinuousWakeWord() {
    if (!SpeechRecognition || !voiceInputEnabled || !wakeWordActive) return;
    try {
      if (voiceRecognition) {
        try { voiceRecognition.stop(); } catch(e) {}
      }
      voiceRecognition = initSpeechRecognition();
      if (voiceRecognition) {
        voiceRecognition.start();
        setCommBadgeState('passive-listen');
      }
    } catch(e) {
      console.warn("Konnte kontinuierliches Wake-Word nicht starten:", e);
    }
  }

  async function requestMicPermission() {
    playLcarsBeep(980, 1400);
    if (!window.isSecureContext) {
      playLcarsError();
      const origin = window.location.origin;
      alert("LCARS SICHERHEITSHINWEIS // KEIN HTTPS / UNSICHERER URSPRUNG:\\n\\nDer Browser sperrt das Mikrofon auf unverschlüsselten IP-Adressen (" + origin + ").\\n\\nSO GIBST DU ES FREI:\\n1) In Chrome/Edge eine neue Registerkarte öffnen:\\n   chrome://flags/#unsafely-treat-insecure-origin-as-secure\\n2) Dort genau diese Adresse eintragen:\\n   " + origin + "\\n3) Auf 'Enabled' stellen und Browser neu starten (Relaunch).\\n\\nAlternativ: Rufe das Dashboard über 'http://localhost:5000' (am Host) oder über HTTPS auf.");
      return false;
    }
    try {
      if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach(t => t.stop());
        playLcarsAcknowledge();
        alert("LCARS COMM-LINK // MIKROFON FREIGEGEBEN!\\n\\nDas Mikrofon ist jetzt autorisiert und betriebsbereit.");
        updateVoiceUI();
        return true;
      }
    } catch (e) {
      playLcarsError();
      alert("LCARS BERECHTIGUNG GEBLOCKT:\\n\\n" + e.message + "\\n\\nKlicke links neben der Webadresse auf das Schloss- oder Schieberegler-Icon und aktiviere 'Mikrofon: Zulassen'.");
      return false;
    }
  }

  function toggleVoiceListening(targetSubgroup = null) {
    if (!window.isSecureContext) {
      playLcarsError();
      const origin = window.location.origin;
      alert("LCARS SICHERHEITSHINWEIS // KEIN HTTPS / UNSICHERER URSPRUNG:\\n\\nDer Browser sperrt das Mikrofon auf unverschlüsselten IP-Adressen (" + origin + ").\\n\\nSO GIBST DU ES FREI:\\n1) In Chrome/Edge eine neue Registerkarte öffnen:\\n   chrome://flags/#unsafely-treat-insecure-origin-as-secure\\n2) Dort genau diese Adresse eintragen:\\n   " + origin + "\\n3) Auf 'Enabled' stellen und Browser neu starten (Relaunch).\\n\\nAlternativ: Rufe das Dashboard über 'http://localhost:5000' (am Host) oder über HTTPS auf.");
      return;
    }

    if (!SpeechRecognition) {
      playLcarsError();
      alert("LCARS HINWEIS: Die Web Speech API ist in diesem Browser oder unter dieser URL nicht aktiv.\\nBitte verwende Google Chrome, Edge oder Safari und stelle sicher, dass die Seite über localhost, HTTPS oder mit der Chrome-Flag aufgerufen wird.");
      return;
    }

    // Barge-in: Falls Computer gerade spricht, sofort stummschalten
    if ('speechSynthesis' in window && window.speechSynthesis.speaking) {
      window.speechSynthesis.cancel();
      setCommBadgeState('idle');
      stopVoiceVisualizer();
      playLcarsBeep(440, 220);
      return;
    }

    if (isListening && !wakeWordActive) {
      stopVoiceComm();
      playLcarsBeep(600, 300);
      return;
    }

    playLcarsChirp();
    if (targetSubgroup) {
      activeAgentSubgroup = targetSubgroup;
    }

    if (wakeWordActive) {
      try {
        if (voiceRecognition) voiceRecognition.stop();
      } catch(e) {}
    }

    voiceRecognition = initSpeechRecognition();

    const hud = document.getElementById('lcarsVoiceHud');
    const hudStatus = document.getElementById('lcarsVoiceHudStatus');
    const hudTranscript = document.getElementById('lcarsVoiceHudTranscript');
    if (hud) hud.style.display = 'flex';
    if (hudStatus) hudStatus.textContent = '● ODN SUBRAUM-COMM // HÖRE ZU...';
    if (hudTranscript) hudTranscript.textContent = 'Befehl sprechen...';

    setCommBadgeState('listening');
    startVoiceVisualizer('listening');

    try {
      voiceRecognition.start();
    } catch (e) {
      console.warn("Fehler beim Starten der Spracherkennung:", e);
    }
  }

  function stopVoiceComm(userAborted = false) {
    if (voiceRecognition && isListening) {
      try { voiceRecognition.stop(); } catch(e) {}
    }
    isListening = false;
    setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
    stopVoiceVisualizer();
    if (userAborted) {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      playLcarsBeep(440, 220);
    }
  }

  async function handleVoiceCommand(rawText) {
    stopVoiceComm();
    playLcarsAcknowledge();
    setCommBadgeState('computing');
    isVoiceLastInput = true;

    const cleanText = rawText.trim();
    const lower = cleanText.toLowerCase();

    // 1. Stopp / Abbruch (Barge-In)
    if (/^(stopp|halt|abbrechen|ruhe|computer ende|stille|stop|abbruch)$/i.test(lower)) {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
      playLcarsBeep(440, 220);
      return;
    }

    // 2. Status / Systemstatus / Diagnose
    if (/(status|statusbericht|systemstatus|diagnose|vitals|systemzustand)/i.test(lower)) {
      switchCategory('system');
      const cpu = document.getElementById('topCpuVal')?.textContent || 'nominal';
      const ram = document.getElementById('topRamVal')?.textContent || 'nominal';
      const temp = document.getElementById('topTempVal')?.textContent || 'optimal';
      const disk = document.getElementById('topDiskVal')?.textContent || 'nominal';
      const resp = `Statusbericht für Terminal 47: Prozessor bei ${cpu}, Arbeitsspeicher bei ${ram}, Festplatte ${disk}, Kerntemperatur ${temp}. Alle primären Systeme arbeiten nominal.`;
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS BORDCOMPUTER');
      speakLcarsText(resp);
      return;
    }

    // 3. Roter Alarm
    if (/(roter? alarm|red alert|alarmstufe rot|gefechtsstationen|alarm auslösen)/i.test(lower)) {
      isCurrentlyRedAlert = true;
      document.documentElement.setAttribute('data-theme', 'redalert');
      playRedAlertKlaxon();
      const banner = document.getElementById('redAlertBanner');
      if (banner) banner.style.display = 'block';
      const reasonsEl = document.getElementById('redAlertReasons');
      if (reasonsEl) reasonsEl.textContent = 'MANUELL AUTORISIERT VIA LCARS SPRACHSTEUERUNG';
      const resp = "Roter Alarm autorisiert. Schutzschilde und Verteidigungsgitter aktiviert. Alle Stationen auf Gefechtsstationen.";
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS SICHERHEIT');
      speakLcarsText(resp);
      return;
    }

    // 4. Alarm aufheben
    if (/(alarm aufheben|alarm beenden|entwarnung|normaler status|gelber alarm|alarm abbrechen)/i.test(lower)) {
      isCurrentlyRedAlert = false;
      document.documentElement.setAttribute('data-theme', lastUserTheme || 'classic');
      const banner = document.getElementById('redAlertBanner');
      if (banner) banner.style.display = 'none';
      const resp = "Alarmstufe aufgehoben. Normalbetrieb wiederhergestellt.";
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS SICHERHEIT');
      speakLcarsText(resp);
      return;
    }

    // 5. Services / Webdienste
    if (/(services|dienste|webdienste|server|laufende dienste)/i.test(lower)) {
      switchCategory('services');
      const count = document.getElementById('scanFoundCount')?.textContent || 'mehrere';
      const resp = `Subraum-Verbindungen analysiert. Aktuell sind ${count} aktive Server auf den Frequenzen registriert.`;
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS SUBRAUM-COMM');
      speakLcarsText(resp);
      return;
    }

    // 6. Solar / Balkonkraftwerk / Energie
    if (/(solar|akku|batterie|energie|strom|balkonkraftwerk|hausverbrauch)/i.test(lower)) {
      switchCategory('solar');
      const bat = document.getElementById('topBatVal')?.textContent || 'nicht erfasst';
      const house = document.getElementById('topHouseVal')?.textContent || 'nicht erfasst';
      const resp = `Energie-Status: Balkonkraftwerk-Speicher liegt bei ${bat}. Aktueller Hausverbrauch beträgt ${house}.`;
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS ENERGIE-MANAGEMENT');
      speakLcarsText(resp);
      return;
    }

    // 7. Netzwerk Scan
    if (/(scan|scannen|suchlauf|netzwerk scannen|portscan)/i.test(lower)) {
      switchCategory('services');
      if (typeof triggerWebserverScan === 'function') {
        triggerWebserverScan();
      }
      const resp = "ODN-Netzwerk-Scan nach aktiven Servern und Diensten initiiert.";
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS SCANNER');
      speakLcarsText(resp);
      return;
    }

    // 8. Farbschema Wechsel
    const themeMatch = lower.match(/(farbschema|design|theme)\\s+(picard|nemesis|classic|lower decks|voyager)/i);
    if (themeMatch) {
      let th = themeMatch[2].toLowerCase().replace(/\\s+/g, '');
      setLcarsTheme(th);
      const resp = `LCARS Farbschema ${themeMatch[2].toUpperCase()} erfolgreich rekonfiguriert.`;
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS INTERFACE');
      speakLcarsText(resp);
      return;
    }

    // 9. Weiterleitung an KI-Agenten (Hermes oder 9Router)
    if (activeAgentSubgroup === 'hermes') {
      switchCategory('hermes');
      const inp = document.getElementById('hermesChatInput');
      if (inp) {
        inp.value = cleanText;
        await handleHermesChatSubmit(null, true);
      }
    } else {
      switchCategory('9router');
      const inp = document.getElementById('lcarsChatInput');
      if (inp) {
        inp.value = cleanText;
        await handleChatSubmit(null, true);
      }
    }
  }

  function toggleVoiceInputSetting() {
    voiceInputEnabled = !voiceInputEnabled;
    localStorage.setItem('lcars-voice-in', voiceInputEnabled);
    if (!voiceInputEnabled) {
      stopVoiceComm();
    } else if (wakeWordActive) {
      startContinuousWakeWord();
    }
    updateVoiceUI();
    playLcarsBeep(880, 1760);
  }

  function toggleVoiceOutputSetting() {
    voiceOutputEnabled = !voiceOutputEnabled;
    localStorage.setItem('lcars-voice-out', voiceOutputEnabled);
    if (!voiceOutputEnabled && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    updateVoiceUI();
    playLcarsBeep(880, 1760);
  }

  function toggleWakeWordSetting() {
    wakeWordActive = !wakeWordActive;
    localStorage.setItem('lcars-wakeword', wakeWordActive);
    if (wakeWordActive && voiceInputEnabled) {
      startContinuousWakeWord();
    } else {
      stopVoiceComm();
    }
    updateVoiceUI();
    playLcarsBeep(980, 1400);
  }

  function onVoiceSelectChange(val) {
    localStorage.setItem('lcars-voice-name', val);
    playLcarsBeep(1100, 1600);
  }

  function testLcarsVoice() {
    speakLcarsText("LCARS Audio-Transceiver online. Subraum-Kommunikation und Sprachausgabe nominal.");
  }

  function updateVoiceUI() {
    const inLabel = document.getElementById('cfgVoiceInLabel');
    const outLabel = document.getElementById('cfgVoiceOutLabel');
    const wakeLabel = document.getElementById('cfgWakeWordLabel');
    if (inLabel) inLabel.textContent = voiceInputEnabled ? 'SPRACHEINGABE: AKTIV' : 'SPRACHEINGABE: AUS';
    if (outLabel) outLabel.textContent = voiceOutputEnabled ? 'SPRACHAUSGABE: AKTIV' : 'SPRACHAUSGABE: AUS';
    if (wakeLabel) wakeLabel.textContent = wakeWordActive ? "WAKE-WORD 'COMPUTER': AKTIV" : "WAKE-WORD 'COMPUTER': AUS";
    setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
  }

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (isListening || ('speechSynthesis' in window && window.speechSynthesis.speaking)) {
        stopVoiceComm(true);
      }
    }
  });

  // ==========================================================================
  // KI-AGENTEN SUBGRUPPEN & HERMES & ANTIGRAVITY IDE INTERFACE
  // ==========================================================================
  let activeAgentSubgroup = '9router';
  let hermesProfilesList = [];
  let currentHermesProfile = 'default';
  let isHermesGenerating = false;

  function switchAgentSubgroup(subgroup) {
    playLcarsBeep(1100, 1600);
    activeAgentSubgroup = subgroup;
    switchCategory(subgroup);
  }

  // --- HERMES AGENTEN VERWALTUNG & CHAT ---
  async function loadHermesProfiles(playSound = false) {
    if (playSound) playLcarsBeep(1200, 1800);
    try {
      const resp = await fetch('/api/hermes/profiles');
      if (resp.ok) {
        const data = await resp.json();
        hermesProfilesList = data.profiles || [];
        renderHermesProfiles(hermesProfilesList);
      }
    } catch (e) {
      console.warn("Fehler beim Laden der Hermes Profile:", e);
      const grid = document.getElementById('hermesProfilesGrid');
      if (grid) grid.innerHTML = `<div style="color:var(--c-red); font-family:var(--mono-family); padding:0.5rem;">Fehler beim Laden der Profile: ${e.message}</div>`;
    }
  }

  function renderHermesProfiles(profiles) {
    const grid = document.getElementById('hermesProfilesGrid');
    const select = document.getElementById('hermesProfileSelect');
    const cloneSelect = document.getElementById('newAgentCloneFrom');

    if (select) {
      const cur = select.value || currentHermesProfile;
      select.innerHTML = '';
      profiles.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = `${p.name} (${p.display_name})`;
        if (p.name === cur) opt.selected = true;
        select.appendChild(opt);
      });
      if (select.value) currentHermesProfile = select.value;
    }

    if (cloneSelect) {
      cloneSelect.innerHTML = '';
      profiles.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.name;
        cloneSelect.appendChild(opt);
      });
    }

    if (!grid) return;
    if (profiles.length === 0) {
      grid.innerHTML = '<div style="color:#888; font-family:var(--mono-family); padding:1rem;">Keine Profile in ~/.hermes gefunden.</div>';
      return;
    }

    grid.innerHTML = profiles.map(p => {
      const isGw = p.gateway_running;
      const statusBadge = isGw
        ? '<span class="badge-status badge-online" style="font-size:0.75rem;"><span class="lcars-status-dot"></span> GATEWAY AKTIV</span>'
        : '<span class="badge-status badge-offline" style="font-size:0.75rem;">BEREIT / STANDBY</span>';

      const isActive = p.name === currentHermesProfile;

      return `
        <div class="hermes-card ${isActive ? 'active-card' : ''}" id="hermes-card-${p.name}">
          <div>
            <div class="hermes-card-head">
              <div class="hermes-card-name">
                <span>🤖</span> <span>${p.name.toUpperCase()}</span>
                ${p.is_default ? '<span style="font-size:0.7rem; background:rgba(235,148,58,0.25); color:var(--c-primary); padding:1px 5px; border-radius:3px;">CORE</span>' : ''}
              </div>
              ${statusBadge}
            </div>
            <div style="font-size:0.84rem; color:var(--c-gold); margin:0.4rem 0;">${p.description || p.personality || 'Autonomer System-Agent'}</div>
            <div class="hermes-card-meta">
              <div class="hermes-meta-row">
                <span class="hermes-meta-lbl">MODELL:</span>
                <span class="hermes-meta-val">${p.model || 'ag/gemini-3-flash'}</span>
              </div>
              <div class="hermes-meta-row">
                <span class="hermes-meta-lbl">PROVIDER:</span>
                <span class="hermes-meta-val">${p.provider || 'custom'}</span>
              </div>
              <div class="hermes-meta-row">
                <span class="hermes-meta-lbl">PFAD:</span>
                <span class="hermes-meta-val" style="font-size:0.72rem; opacity:0.8;">${p.path}</span>
              </div>
            </div>
          </div>
          <div style="display:flex; justify-content:flex-end; gap:0.4rem; margin-top:0.4rem;">
            <button type="button" class="left-action-btn" onclick="selectHermesProfile('${p.name}')" style="padding:0.35rem 0.8rem; font-size:0.8rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
              💬 MIT AGENT SPRECHEN
            </button>
          </div>
        </div>
      `;
    }).join('');
  }

  function selectHermesProfile(name) {
    playLcarsBeep(980, 1400);
    currentHermesProfile = name;
    const select = document.getElementById('hermesProfileSelect');
    if (select) select.value = name;
    const header = document.getElementById('hermesActiveProfileHeader');
    if (header) header.textContent = name.toUpperCase();

    document.querySelectorAll('.hermes-card').forEach(c => c.classList.remove('active-card'));
    const activeC = document.getElementById('hermes-card-' + name);
    if (activeC) activeC.classList.add('active-card');

    const inp = document.getElementById('hermesChatInput');
    if (inp) {
      inp.placeholder = `BEFEHL AN AGENT '${name.toUpperCase()}' SENDEN...`;
      inp.focus();
    }
  }

  function onHermesProfileChange() {
    const select = document.getElementById('hermesProfileSelect');
    if (select) {
      selectHermesProfile(select.value);
    }
  }

  function toggleHermesCreateForm(show) {
    playLcarsBeep(880, 1320);
    const card = document.getElementById('hermesCreateAgentCard');
    if (!card) return;
    if (show === undefined) {
      card.style.display = (card.style.display === 'none' || !card.style.display) ? 'block' : 'none';
    } else {
      card.style.display = show ? 'block' : 'none';
    }
    if (card.style.display === 'block') {
      const inp = document.getElementById('newAgentName');
      if (inp) inp.focus();
    }
  }

  async function handleCreateHermesAgent(e) {
    e.preventDefault();
    const btn = document.getElementById('btnSubmitCreateAgent');
    const status = document.getElementById('hermesCreateStatus');
    const nameInp = document.getElementById('newAgentName');
    const cloneInp = document.getElementById('newAgentCloneFrom');
    const modelInp = document.getElementById('newAgentModel');
    const descInp = document.getElementById('newAgentDesc');

    const name = nameInp ? nameInp.value.trim().toLowerCase() : '';
    if (!name) return;

    if (btn) btn.disabled = true;
    if (status) {
      status.style.display = 'block';
      status.style.color = 'var(--c-primary)';
      status.textContent = `INITIALISIERE HERMES PROFIL '${name}' (bitte kurz warten)...`;
    }

    try {
      const resp = await fetch('/api/hermes/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          clone_from: cloneInp ? cloneInp.value : 'default',
          model: modelInp ? modelInp.value.trim() : 'ag/gemini-3-flash',
          description: descInp ? descInp.value.trim() : ''
        })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        if (status) {
          status.style.color = '#10b981';
          status.textContent = `✓ Profil '${name}' erfolgreich angelegt!`;
        }
        playLcarsBeep(1200, 2400);
        await loadHermesProfiles();
        selectHermesProfile(name);
        setTimeout(() => {
          toggleHermesCreateForm(false);
          if (status) status.style.display = 'none';
        }, 1200);
      } else {
        if (status) {
          status.style.color = 'var(--c-red)';
          status.textContent = `Fehler: ${data.error || 'Profil konnte nicht erstellt werden'}`;
        }
        playLcarsBeep(440, 220);
      }
    } catch (err) {
      if (status) {
        status.style.color = 'var(--c-red)';
        status.textContent = `Netzwerkfehler: ${err.message}`;
      }
      playLcarsBeep(440, 220);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function appendHermesMessage(role, content, senderName, isError = false) {
    const log = document.getElementById('hermesChatLog');
    if (!log) return;

    const timeStr = formatTimeNow();
    const msgDiv = document.createElement('div');
    const cssClass = role === 'user'
      ? 'lcars-msg lcars-msg-user'
      : (isError ? 'lcars-msg lcars-msg-error' : 'lcars-msg lcars-msg-agent');

    const senderLabel = role === 'user' ? 'USER // ODN TERMINAL' : `HERMES // ${senderName.toUpperCase()}`;

    msgDiv.className = cssClass;
    if (role !== 'user' && !isError) {
      msgDiv.style.borderLeftColor = 'var(--c-secondary)';
      msgDiv.style.background = 'rgba(186, 164, 229, 0.08)';
    }

    msgDiv.innerHTML = `
      <div class="lcars-msg-header">
        <span class="lcars-msg-sender" style="${role !== 'user' ? 'color:var(--c-secondary);' : ''}">${senderLabel}</span>
        <span class="lcars-msg-time">${timeStr}</span>
      </div>
      <div class="lcars-msg-body">${formatMessageText(content)}</div>
    `;

    log.appendChild(msgDiv);
    log.scrollTop = log.scrollHeight;
  }

  async function handleHermesChatSubmit(e, fromVoice = false) {
    if (e) e.preventDefault();
    if (isHermesGenerating) return;

    const input = document.getElementById('hermesChatInput');
    const sendBtn = document.getElementById('hermesChatSendBtn');
    const sendLabel = document.getElementById('hermesChatSendLabel');
    const loading = document.getElementById('hermesChatLoading');
    const meta = document.getElementById('hermesMetaStatus');

    if (!input) return;
    const message = input.value.trim();
    if (!message) return;

    input.value = '';
    appendHermesMessage('user', message, currentHermesProfile);
    playLcarsBeep(880, 1320);

    isHermesGenerating = true;
    input.disabled = true;
    if (sendBtn) sendBtn.disabled = true;
    if (sendLabel) sendLabel.textContent = 'WAIT...';
    if (loading) loading.style.display = 'flex';
    if (meta) meta.textContent = `HERMES VERARBEITET ANFRAGE AN '${currentHermesProfile.toUpperCase()}'...`;
    if (fromVoice) setCommBadgeState('computing');

    try {
      const resp = await fetch('/api/hermes/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile: currentHermesProfile,
          message: message
        })
      });

      const data = await resp.json();

      if (resp.ok && data.success) {
        appendHermesMessage('assistant', data.reply || 'Keine Antwort erhalten.', currentHermesProfile);
        playLcarsAcknowledge();
        if (meta) meta.textContent = `ANTWORT EMPFANGEN // AGENT: ${currentHermesProfile.toUpperCase()}`;
        if (fromVoice || (voiceOutputEnabled && isVoiceLastInput)) {
          speakLcarsText(data.reply || 'Keine Antwort erhalten.');
        } else {
          setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
        }
      } else {
        const err = data.error || data.reply || 'Fehler bei Hermes Ausführung.';
        appendHermesMessage('assistant', err, currentHermesProfile, true);
        playLcarsError();
        if (meta) meta.textContent = 'HERMES AUSFÜHRUNGSFEHLER';
        if (fromVoice) speakLcarsText('Fehler bei Ausführung des Hermes Agenten.');
        setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
      }
    } catch (err) {
      appendHermesMessage('assistant', `Verbindungsfehler zu Hermes: ${err.message}`, currentHermesProfile, true);
      playLcarsError();
      if (meta) meta.textContent = 'NETZWERKFEHLER';
      if (fromVoice) speakLcarsText('Netzwerkfehler bei Verbindung zu Hermes.');
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
    } finally {
      isHermesGenerating = false;
      input.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
      if (sendLabel) sendLabel.textContent = 'SENDEN';
      if (loading) loading.style.display = 'none';
      input.focus();
    }
  }

  function sendHermesQuickPrompt(text) {
    const inp = document.getElementById('hermesChatInput');
    if (inp) {
      inp.value = text;
      handleHermesChatSubmit(null);
    }
  }

  function clearHermesChatHistory() {
    playLcarsBeep(600, 300);
    const log = document.getElementById('hermesChatLog');
    if (log) {
      log.innerHTML = `
        <div class="lcars-msg lcars-msg-agent" style="border-left-color: var(--c-secondary); background: rgba(186, 164, 229, 0.08);">
          <div class="lcars-msg-header">
            <span class="lcars-msg-sender" style="color: var(--c-secondary);">▶ HERMES // SYSTEM COMM-LINK</span>
            <span class="lcars-msg-time">${formatTimeNow()}</span>
          </div>
          <div class="lcars-msg-body">Chat-Verlauf zurückgesetzt. Neuer Dialog mit Agent '${currentHermesProfile.toUpperCase()}'.</div>
        </div>
      `;
    }
  }

  // --- ANTIGRAVITY IDE URL & KONFIGURATION ---
  const DEFAULT_ANTIGRAVITY_IDE_URL = 'https://antigravity.google.com/r/f9e18040-ab9a-4f0c-9d4a-2b4f5281af22-v2?p=c%2F3a633d37-def3-419b-ab1e-8498973ae694%3Fsection%3Dc15db4e2-c36e-442e-850b-0d28e944e2c6';

  function parseIdeUrlParams(url) {
    try {
      const u = new URL(url);
      const runnerMatch = u.pathname.match(new RegExp('/r/([^/?#]+)'));
      const runnerId = runnerMatch ? runnerMatch[1] : '--';

      const p = u.searchParams.get('p') || '';
      let convId = '--';
      let sectionId = '--';
      if (p) {
        const decodedP = decodeURIComponent(p);
        const convMatch = decodedP.match(new RegExp('c/([^/?#]+)'));
        if (convMatch) convId = convMatch[1];
        const secMatch = decodedP.match(new RegExp('section=([^/?#&]+)'));
        if (secMatch) sectionId = secMatch[1];
      }
      return { runnerId, convId, sectionId };
    } catch (e) {
      return { runnerId: '--', convId: '--', sectionId: '--' };
    }
  }

  function loadIdeConfig() {
    const inp = document.getElementById('ideUrlInput');
    const launchBtn = document.getElementById('ideLaunchBtn');
    const cfgInp = document.getElementById('cfgIdeUrl');

    let curUrl = DEFAULT_ANTIGRAVITY_IDE_URL;
    if (inp && inp.value) curUrl = inp.value;

    if (launchBtn) launchBtn.href = curUrl;
    if (cfgInp && !cfgInp.value) cfgInp.value = curUrl;

    const parsed = parseIdeUrlParams(curUrl);
    const rEl = document.getElementById('ideRunnerId');
    if (rEl) rEl.textContent = parsed.runnerId;
    const cEl = document.getElementById('ideConvId');
    if (cEl) cEl.textContent = parsed.convId;
    const sEl = document.getElementById('ideSectionId');
    if (sEl) sEl.textContent = parsed.sectionId;
  }

  async function saveIdeUrl() {
    playLcarsBeep(980, 1400);
    const inp = document.getElementById('ideUrlInput');
    const status = document.getElementById('ideSaveStatus');
    const cfgInp = document.getElementById('cfgIdeUrl');
    const launchBtn = document.getElementById('ideLaunchBtn');

    if (!inp) return;
    const url = inp.value.trim();
    if (!url) return;

    try {
      const resp = await fetch('/api/config/ide-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        if (launchBtn) launchBtn.href = url;
        if (cfgInp) cfgInp.value = url;
        loadIdeConfig();
        if (status) {
          status.style.display = 'block';
          status.style.color = '#10b981';
          status.textContent = '✓ IDE-URL erfolgreich persistent gespeichert!';
          setTimeout(() => { status.style.display = 'none'; }, 3000);
        }
        playLcarsBeep(1200, 2400);
      } else {
        if (status) {
          status.style.display = 'block';
          status.style.color = 'var(--c-red)';
          status.textContent = 'Fehler beim Speichern der URL.';
        }
      }
    } catch (e) {
      if (status) {
        status.style.display = 'block';
        status.style.color = 'var(--c-red)';
        status.textContent = `Netzwerkfehler: ${e.message}`;
      }
    }
  }

  function copyIdeUrl() {
    playLcarsBeep(880, 1320);
    const inp = document.getElementById('ideUrlInput');
    const status = document.getElementById('ideSaveStatus');
    if (inp) {
      navigator.clipboard.writeText(inp.value).then(() => {
        if (status) {
          status.style.display = 'block';
          status.style.color = 'var(--c-gold)';
          status.textContent = '📋 URL in Zwischenablage kopiert!';
          setTimeout(() => { status.style.display = 'none'; }, 2500);
        }
      }).catch(err => {
        alert('Konnte Zwischenablage nicht öffnen: ' + err);
      });
    }
  }

  function resetIdeUrl() {
    playLcarsBeep(600, 400);
    const inp = document.getElementById('ideUrlInput');
    if (inp) {
      inp.value = DEFAULT_ANTIGRAVITY_IDE_URL;
      saveIdeUrl();
    }
  }

  // ==========================================================================
  // LCARS PERMISSIONS & COMMAND CODE CONTROLLER
  // ==========================================================================
  var currentLockedSections = ['cycle'];
  var pendingUnlockCategory = null;
  var currentAuthCode = sessionStorage.getItem('lcars_auth_code') || '';

  function isCategoryLocked(catId) {
    if (typeof isUserAllowedSection === 'function' && isUserAllowedSection(catId)) {
      return false;
    }
    const isUnlocked = (sessionStorage.getItem('lcars_auth_unlocked') === 'true');
    return currentLockedSections.includes(catId) && !isUnlocked;
  }

  async function fetchPermissionsStatus() {
    try {
      const resp = await fetch('/api/permissions/status');
      if (resp.ok) {
        const data = await resp.json();
        if (Array.isArray(data.locked_sections)) {
          currentLockedSections = data.locked_sections;
        }
      }
    } catch (e) {
      console.warn('Fehler beim Laden der Rechtekonfiguration:', e);
    }
    applyPermissionsVisibility();
  }

  function applyPermissionsVisibility() {
    const isUnlocked = (sessionStorage.getItem('lcars_auth_unlocked') === 'true');
    const isSuperAdmin = Boolean(currentLcarsUser && (currentLcarsUser.is_super_admin || currentLcarsUser.username === 'cb' || (currentLcarsUser.username && currentLcarsUser.username.toLowerCase() === 'cb')));

    function canAccess(secId) {
      if (typeof isUserAllowedSection === 'function') {
        return isUserAllowedSection(secId);
      }
      return !currentLockedSections.includes(secId) || isUnlocked;
    }

    // Top-level main sections
    const sysBtn = document.getElementById('btn-cat-system');
    if (sysBtn) sysBtn.style.display = canAccess('system') ? '' : 'none';

    const srvBtn = document.getElementById('btn-cat-services');
    if (srvBtn) srvBtn.style.display = canAccess('services') ? '' : 'none';

    const cfgBtn = document.getElementById('btn-cat-config');
    if (cfgBtn) cfgBtn.style.display = canAccess('config') ? '' : 'none';

    const devteamBtn = document.getElementById('btn-cat-devteam');
    if (devteamBtn) devteamBtn.style.display = canAccess('devteam') ? '' : 'none';

    const knowledgeBtn = document.getElementById('btn-cat-knowledge');
    if (knowledgeBtn) knowledgeBtn.style.display = canAccess('knowledge') ? '' : 'none';

    const researchBtn = document.getElementById('btn-cat-research');
    if (researchBtn) researchBtn.style.display = canAccess('research') ? '' : 'none';

    // AI sub-sections
    const aiSubSections = ['9router', 'hermes', 'ide', 'ai-info', 'gemini_live', 'agents'];
    let anyAiAllowed = canAccess('ai');
    aiSubSections.forEach(secId => {
      const btn = document.getElementById('btn-cat-' + secId);
      if (!btn) return;
      if (secId === 'agents') {
        btn.style.display = 'none';
        return;
      }
      const allowed = canAccess(secId);
      if (allowed) anyAiAllowed = true;
      btn.style.display = allowed ? '' : 'none';
    });
    const aiGroup = document.getElementById('nav-ai-group');
    if (aiGroup) aiGroup.style.display = anyAiAllowed ? '' : 'none';

    // Personal sub-sections
    const personalSubSections = ['fantasy', 'solar', 'homeassistant', 'cycle', 'pulsecast'];
    let anyPersonalAllowed = canAccess('personal');
    personalSubSections.forEach(secId => {
      const btn = document.getElementById('btn-cat-' + secId);
      if (!btn) return;
      const allowed = canAccess(secId);
      if (allowed) anyPersonalAllowed = true;
      if (secId === 'homeassistant' && allowed) {
        checkHomeAssistantConfig();
      } else {
        btn.style.display = allowed ? '' : 'none';
      }
    });
    const personalGroup = document.getElementById('nav-personal-group');
    if (personalGroup) personalGroup.style.display = anyPersonalAllowed ? '' : 'none';



    // Update Config Rechteverwaltung Card
    updateConfigPermUI(isUnlocked || isSuperAdmin);

    // If current category is not allowed, switch to first allowed category
    if (!canAccess(currentCategory)) {
      const priorityOrder = [
        'system', 'services', 'ai', '9router', 'hermes', 'ide', 'ai-info', 'gemini_live',
        'config', 'personal', 'fantasy', 'solar', 'homeassistant', 'cycle', 'pulsecast', 'devteam'
      ];
      const fallback = priorityOrder.find(cat => canAccess(cat));
      if (fallback) {
        switchCategory(fallback, true);
      }
    }
  }

  function updateConfigPermUI(isUnlocked) {
    const unlockedView = document.getElementById('permUnlockedView');
    const badge = document.getElementById('permStatusBadge');
    const icon = document.getElementById('permHeadIcon');
    const isSuperAdmin = Boolean(currentLcarsUser && (currentLcarsUser.is_super_admin || currentLcarsUser.username === 'cb' || (currentLcarsUser.username && currentLcarsUser.username.toLowerCase() === 'cb')));

    if (unlockedView) unlockedView.style.display = 'block';
    if (badge) {
      badge.textContent = 'AKTIV // ZENTRALE AUTH';
      badge.style.backgroundColor = '#44dd88';
      badge.style.color = '#000000';
    }
    if (icon) icon.textContent = '🛡️';

    // Check the checkboxes for currentLockedSections
    const allSections = ['system', 'services', 'ai', '9router', 'hermes', 'ide', 'agents', 'ai-info', 'config', 'fantasy', 'personal', 'solar', 'homeassistant', 'cycle', 'pulsecast', 'gemini_live', 'devteam', 'knowledge', 'research'];
    allSections.forEach(secId => {
      const cb = document.getElementById('permLock_' + secId);
      if (cb) {
        cb.checked = currentLockedSections.includes(secId);
      }
    });
    if (isSuperAdmin) {
      loadLcarsUsers();
      loadLcarsAuditLog();
    }

    // Nur für Admins (cb / Super Admin) sichtbar
    const mgmtSec = document.getElementById('lcarsUserManagementSection');
    const mgmtNotice = document.getElementById('lcarsUserMgmtRestrictedNotice');
    if (mgmtSec) {
      if (isSuperAdmin) {
        mgmtSec.style.display = 'block';
        if (mgmtNotice) mgmtNotice.style.display = 'none';
      } else {
        mgmtSec.style.display = 'none';
        if (mgmtNotice) mgmtNotice.style.display = 'block';
      }
    }
  }

  function openAuthModal() {
    playLcarsBeep(880, 1400);
    const modal = document.getElementById('lcarsAuthModal');
    if (modal) {
      modal.style.display = 'flex';
      const inp = document.getElementById('modalPinInput');
      if (inp) {
        inp.value = '';
        inp.focus();
      }
      const err = document.getElementById('modalPinError');
      if (err) err.style.display = 'none';
    }
  }

  function closeAuthModal() {
    playLcarsBeep(440, 220);
    const modal = document.getElementById('lcarsAuthModal');
    if (modal) modal.style.display = 'none';
    pendingUnlockCategory = null;
  }

  function handleAuthModalBackdropClick(e) {
    if (e.target && e.target.id === 'lcarsAuthModal') {
      closeAuthModal();
    }
  }

  function toggleAuthModal() {
    const isUnlocked = (sessionStorage.getItem('lcars_auth_unlocked') === 'true');
    if (isUnlocked) {
      lockPermissionsSession();
    } else {
      openAuthModal();
    }
  }

  function appendModalPin(digit) {
    playLcarsBeep(1200, 1600);
    const inp = document.getElementById('modalPinInput');
    if (inp && inp.value.length < 10) {
      inp.value += digit;
    }
  }

  function clearModalPin() {
    playLcarsBeep(500, 300);
    const inp = document.getElementById('modalPinInput');
    if (inp) inp.value = '';
    const err = document.getElementById('modalPinError');
    if (err) err.style.display = 'none';
  }

  async function verifyModalPin() {
    const inp = document.getElementById('modalPinInput');
    const code = inp ? inp.value.trim() : '';
    if (!code) return;

    try {
      const resp = await fetch('/api/permissions/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code })
      });
      const res = await resp.json();
      if (res.valid) {
        sessionStorage.setItem('lcars_auth_unlocked', 'true');
        sessionStorage.setItem('lcars_auth_code', code);
        currentAuthCode = code;
        playLcarsAcknowledge();
        closeAuthModal();
        applyPermissionsVisibility();
        if (pendingUnlockCategory) {
          const target = pendingUnlockCategory;
          pendingUnlockCategory = null;
          switchCategory(target);
        }
      } else {
        playLcarsBeep(300, 150);
        const err = document.getElementById('modalPinError');
        if (err) err.style.display = 'block';
        if (inp) {
          inp.value = '';
          inp.focus();
        }
      }
    } catch (e) {
      alert('Fehler bei der Authentifizierung: ' + e);
    }
  }



  function lockPermissionsSession() {
    playLcarsBeep(440, 220);
    sessionStorage.removeItem('lcars_auth_unlocked');
    applyPermissionsVisibility();
  }

  async function savePermissionsConfig(e) {
    e.preventDefault();
    const btn = document.getElementById('btnSavePerm');
    const feedback = document.getElementById('permSaveFeedback');
    if (btn) btn.disabled = true;

    const checkedBoxes = document.querySelectorAll('.perm-lock-cb:checked');
    const lockedSections = Array.from(checkedBoxes).map(cb => cb.value);

    const newCodeInp = document.getElementById('permNewCode');
    const newCodeConfirmInp = document.getElementById('permNewCodeConfirm');
    const newCode = newCodeInp ? newCodeInp.value.trim() : '';
    const newCodeConfirm = newCodeConfirmInp ? newCodeConfirmInp.value.trim() : '';

    if (newCode) {
      if (newCode.length < 3) {
        alert('Der neue Command Code muss mindestens 3 Zeichen lang sein.');
        if (btn) btn.disabled = false;
        return;
      }
      if (newCode !== newCodeConfirm) {
        alert('Die eingegebenen Command Codes stimmen nicht überein.');
        if (btn) btn.disabled = false;
        return;
      }
    }

    const payload = {
      code: sessionStorage.getItem('lcars_auth_code') || currentAuthCode || '',
      locked_sections: lockedSections
    };
    if (newCode) {
      payload.new_code = newCode;
    }

    try {
      const resp = await fetch('/api/permissions/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const res = await resp.json();
      if (res.success) {
        playLcarsAcknowledge();
        if (newCode) {
          sessionStorage.setItem('lcars_auth_code', newCode);
          currentAuthCode = newCode;
          if (newCodeInp) newCodeInp.value = '';
          if (newCodeConfirmInp) newCodeConfirmInp.value = '';
        }
        currentLockedSections = res.locked_sections || lockedSections;
        applyPermissionsVisibility();

        if (feedback) {
          feedback.textContent = 'RECHTEKONFIGURATION ERFOLGREICH GESPEICHERT';
          feedback.style.display = 'block';
          setTimeout(() => { feedback.style.display = 'none'; }, 4000);
        }
      } else {
        alert('Fehler beim Speichern: ' + (res.error || 'Unbekannter Fehler'));
      }
    } catch (err) {
      alert('Netzwerkfehler beim Speichern: ' + err);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ==========================================================================
  // LCARS CENTRAL USER & ACCESS MANAGEMENT CONTROLLER (*.PIMMEL.SITE)
  // ==========================================================================
  var lcarsUsersList = [];
  var lcarsAvailableServices = [
    { key: 'telemetryvault', name: 'TelemetryVault', subdomain: 'tele' },
    { key: 'matter', name: 'Matter Server', subdomain: 'mat' },
    { key: 'headroom', name: 'Headroom AI', subdomain: 'head' },
    { key: 'cups', name: 'CUPS Drucker', subdomain: 'port' }
  ];
  var lcarsCategoriesList = [
    { key: 'system', name: 'System Status', group: 'main' },
    { key: 'services', name: 'Services Übersicht', group: 'main' },
    { key: 'ai', name: 'KI (Hauptbereich)', group: 'ai' },
    { key: '9router', name: '9Router', group: 'ai' },
    { key: 'hermes', name: 'Hermes Agent', group: 'ai' },
    { key: 'ide', name: 'Antigravity IDE', group: 'ai' },
    { key: 'ai-info', name: 'KI-Info', group: 'ai' },
    { key: 'gemini_live', name: 'Subraum Comm', group: 'ai' },
    { key: 'config', name: 'Config & Benutzer', group: 'main' },
    { key: 'personal', name: 'Persönlich (Alle)', group: 'personal' },
    { key: 'fantasy', name: 'Fantasy Bundesliga', group: 'personal' },
    { key: 'solar', name: 'Solar Ertrag', group: 'personal' },
    { key: 'homeassistant', name: 'Home Assistant', group: 'personal' },
    { key: 'cycle', name: 'Zyklus-Tracker', group: 'personal' },
    { key: 'pulsecast', name: 'PulseCast Hub', group: 'personal' },
    { key: 'devteam', name: 'Dev-Team Kanban', group: 'main' },
    { key: 'knowledge', name: 'Wissensdatenbank', group: 'main' },
    { key: 'research', name: 'Research-Rubrik', group: 'main' }
  ];

  function renderUserServicesCheckboxes(services, categories) {
    const grid = document.getElementById('userServicesGrid');
    if (!grid) return;
    if (Array.isArray(services) && services.length > 0) {
      lcarsAvailableServices = services;
    }
    if (Array.isArray(categories) && categories.length > 0) {
      lcarsCategoriesList = categories;
    }

    let html = '<div style="grid-column: 1 / -1; margin-bottom: 0.4rem;">' +
      '<label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">' +
      '<input type="checkbox" id="userSvc_all" value="*" class="user-svc-cb" onchange="handleUserSvcAllToggle()">' +
      '<span style="font-family:var(--mono-family); font-size:0.85rem; color:var(--c-primary); font-weight:700;">★ ALLE KATEGORIEN & WEBSERVICES (*) [VOLLZUGRIFF]</span>' +
      '</label></div>';

    html += '<div style="grid-column: 1 / -1; font-size:0.75rem; font-weight:700; color:var(--c-secondary); margin-top:0.4rem; margin-bottom:0.25rem; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:2px;">' +
      'DASHBOARD KATEGORIEN & SEKTIONEN</div>';

    lcarsCategoriesList.forEach(c => {
      html += '<label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">' +
        '<input type="checkbox" id="userSvc_' + escapeHtml(c.key) + '" value="' + escapeHtml(c.key) + '" class="user-svc-cb">' +
        '<span style="font-family:var(--mono-family); font-size:0.82rem;">' + escapeHtml(c.name || c.key) + '</span>' +
        '</label>';
    });

    html += '<div style="grid-column: 1 / -1; font-size:0.75rem; font-weight:700; color:var(--c-gold); margin-top:0.6rem; margin-bottom:0.25rem; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:2px;">' +
      'EXTERNE WEBSERVICES (*.PIMMEL.SITE)</div>';

    lcarsAvailableServices.forEach(s => {
      const subLabel = s.subdomain ? ' (' + escapeHtml(s.subdomain) + ')' : '';
      html += '<label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">' +
        '<input type="checkbox" id="userSvc_' + escapeHtml(s.key) + '" value="' + escapeHtml(s.key) + '" class="user-svc-cb">' +
        '<span style="font-family:var(--mono-family); font-size:0.82rem;">' + escapeHtml(s.name) + subLabel + '</span>' +
        '</label>';
    });

    grid.innerHTML = html;
  }

  function getLcarsAuthHeader() {
    return {
      'Content-Type': 'application/json'
    };
  }

  async function loadLcarsUsers() {
    const tbody = document.getElementById('lcarsUsersTableBody');
    if (!tbody) return;
    try {
      const resp = await fetch('/api/users', { headers: getLcarsAuthHeader() });
      if (resp.ok) {
        const data = await resp.json();
        lcarsUsersList = data.users || [];
        if (data.services || data.categories) {
          renderUserServicesCheckboxes(data.services, data.categories);
        }
        renderLcarsUsersTable(lcarsUsersList);
      } else if (resp.status === 403) {
        tbody.innerHTML = '<tr><td colspan="6" style="padding:1.5rem; text-align:center; color:var(--c-red); font-family:var(--mono-family);">AUTORISIERUNG FEHLGESCHLAGEN // KEINE ADMIN-BERECHTIGUNG</td></tr>';
      } else {
        tbody.innerHTML = '<tr><td colspan="6" style="padding:1.5rem; text-align:center; color:var(--c-red); font-family:var(--mono-family);">FEHLER BEIM LADEN DER BENUTZERDATEN</td></tr>';
      }
    } catch (e) {
      console.warn('loadLcarsUsers error:', e);
      tbody.innerHTML = '<tr><td colspan="6" style="padding:1.5rem; text-align:center; color:var(--c-red); font-family:var(--mono-family);">NETZWERKFEHLER BEIM LADEN</td></tr>';
    }
  }

  function copyLcarsApiKey(key) {
    if (!key) return;
    playLcarsBeep(880, 1400);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(key).then(() => {
        alert('Access-Key in Zwischenablage kopiert:\\n' + key);
      }).catch(() => {
        prompt('Access-Key:', key);
      });
    } else {
      prompt('Access-Key:', key);
    }
  }

  function generateFormUserApiKey() {
    playLcarsBeep(900, 1200);
    const key = 'lcars_' + Array.from(crypto.getRandomValues(new Uint8Array(16))).map(b => b.toString(16).padStart(2, '0')).join('');
    const inp = document.getElementById('formUserApiKey');
    if (inp) inp.value = key;
  }

  function clearFormUserApiKey() {
    playLcarsBeep(440, 220);
    const inp = document.getElementById('formUserApiKey');
    if (inp) inp.value = '';
  }

  function renderLcarsUsersTable(users) {
    const tbody = document.getElementById('lcarsUsersTableBody');
    if (!tbody) return;
    if (!users || users.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="padding:2rem; text-align:center; color:#888; font-family:var(--mono-family);">KEINE BENUTZER ANGELEGT. KLICKEN SIE AUF "+ NEUER BENUTZER" UM DEN ERSTEN ZUGANG ZU ERSTELLEN.</td></tr>';
      return;
    }

    const serviceLabels = {
      '*': 'VOLLZUGRIFF (*)',
      'all': 'VOLLZUGRIFF (*)',
      'system': 'SYSTEM',
      'services': 'SERVICES',
      'ai': 'KI (ALLE)',
      '9router': '9ROUTER',
      'hermes': 'HERMES',
      'ide': 'IDE',
      'ai-info': 'KI-INFO',
      'gemini_live': 'SUBRAUM',
      'config': 'CONFIG',
      'personal': 'PERSÖNLICH (ALLE)',
      'fantasy': 'FANTASY',
      'solar': 'SOLAR',
      'homeassistant': 'ASSISTANT',
      'cycle': 'ZYKLUS',
      'pulsecast': 'PULSECAST',
      'devteam': 'DEV-TEAM',
      'telemetryvault': 'TELEMETRY',
      'matter': 'MATTER',
      'headroom': 'HEADROOM',
      'cups': 'CUPS'
    };
    (lcarsAvailableServices || []).forEach(s => {
      serviceLabels[s.key] = (s.name || s.key).toUpperCase();
    });
    (lcarsCategoriesList || []).forEach(c => {
      serviceLabels[c.key] = (c.name || c.key).toUpperCase();
    });

    let html = '';
    users.forEach(u => {
      const isActive = u.is_active;
      const statusBadge = isActive
        ? '<span style="background:#44dd88; color:#000; padding:2px 8px; border-radius:4px; font-weight:700; font-family:var(--mono-family); font-size:0.75rem;">AKTIV</span>'
        : '<span style="background:var(--c-red); color:#fff; padding:2px 8px; border-radius:4px; font-weight:700; font-family:var(--mono-family); font-size:0.75rem;">GESPERRT</span>';

      let permsHtml = '';
      const sList = u.allowed_services || [];
      if (sList.includes('*') || sList.includes('all')) {
        permsHtml = '<span style="background:rgba(235,148,58,0.2); border:1px solid var(--c-primary); color:var(--c-primary); padding:1px 6px; border-radius:3px; font-family:var(--mono-family); font-size:0.75rem; font-weight:700;">★ ALLE KATEGORIEN & DIENSTE (*)</span>';
      } else if (sList.length === 0) {
        permsHtml = '<span style="color:#666; font-size:0.75rem; font-family:var(--mono-family);">KEINE</span>';
      } else {
        permsHtml = sList.map(s => {
          const lbl = serviceLabels[s] || s.toUpperCase();
          return '<span style="background:rgba(186,164,229,0.15); border:1px solid var(--c-secondary); color:var(--c-secondary); padding:1px 6px; border-radius:3px; font-family:var(--mono-family); font-size:0.72rem; margin-right:4px;">' + escapeHtml(lbl) + '</span>';
        }).join(' ');
      }

      const lastLogin = u.last_login_at ? escapeHtml(u.last_login_at) : '<span style="color:#666;">Noch nie</span>';
      const displayName = u.display_name ? '<strong>' + escapeHtml(u.display_name) + '</strong>' : '';
      const notes = u.notes ? '<div style="font-size:0.75rem; color:#888;">' + escapeHtml(u.notes) + '</div>' : '';

      const keySnippet = u.api_key
        ? '<div style="font-family:var(--mono-family); font-size:0.72rem; color:var(--c-gold); margin-top:2px;">' +
            '<span>🔑 </span><span style="cursor:pointer; text-decoration:underline;" data-key="' + escapeHtml(u.api_key) + '" onclick="copyLcarsApiKey(this.dataset.key)" title="Kopieren">' + escapeHtml(u.api_key.substring(0, 10)) + '... 📋</span>' +
          '</div>'
        : '<div style="font-family:var(--mono-family); font-size:0.7rem; color:#666; margin-top:2px;">KEIN KEY</div>';

      html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.06);">' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle;">' + statusBadge + '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle;">' +
          '<div style="font-family:var(--mono-family); font-size:0.95rem; font-weight:700; color:var(--c-gold);">' + escapeHtml(u.username) + '</div>' +
          keySnippet +
        '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle;">' + displayName + ' ' + notes + '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle;">' + permsHtml + '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle; font-family:var(--mono-family); font-size:0.8rem; color:#bbb;">' + lastLogin + '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle; text-align:right; white-space:nowrap;">' +
          '<button type="button" class="left-action-btn" onclick="openEditUserModal(' + u.id + ')" style="padding:0.25rem 0.5rem; font-size:0.75rem; border-color:var(--c-primary); color:var(--c-primary);" title="Bearbeiten"><span>✏️</span></button> ' +
          '<button type="button" class="left-action-btn" onclick="openChangePasswordModal(' + u.id + ')" style="padding:0.25rem 0.5rem; font-size:0.75rem; border-color:var(--c-gold); color:var(--c-gold);" title="Passwort & Key verwalten"><span>🔑</span></button> ' +
          '<button type="button" class="left-action-btn" onclick="toggleUserActive(' + u.id + ', ' + (isActive ? 'true' : 'false') + ')" style="padding:0.25rem 0.5rem; font-size:0.75rem; border-color:' + (isActive ? 'var(--c-almond)' : '#44dd88') + '; color:' + (isActive ? 'var(--c-almond)' : '#44dd88') + ';" title="' + (isActive ? 'Sperren' : 'Aktivieren') + '"><span>' + (isActive ? '⛔' : '✓') + '</span></button> ' +
          '<button type="button" class="left-action-btn" onclick="deleteLcarsUser(' + u.id + ')" style="padding:0.25rem 0.5rem; font-size:0.75rem; border-color:var(--c-red); color:var(--c-red);" title="Löschen"><span>🗑️</span></button>' +
        '</td>' +
      '</tr>';
    });
    tbody.innerHTML = html;
  }

  function openNewUserModal() {
    playLcarsBeep(700, 900);
    const titleEl = document.getElementById('userFormModalTitle');
    if (titleEl) titleEl.textContent = 'NEUER LCARS BENUTZER ANLEGEN';
    document.getElementById('formUserId').value = '';
    const uInput = document.getElementById('formUserUsername');
    if (uInput) {
      uInput.value = '';
      uInput.disabled = false;
    }
    const pGroup = document.getElementById('formUserPasswordGroup');
    if (pGroup) pGroup.style.display = 'block';
    const pInput = document.getElementById('formUserPassword');
    if (pInput) {
      pInput.value = '';
      pInput.required = true;
    }
    const dInput = document.getElementById('formUserDisplayName');
    if (dInput) dInput.value = '';
    const kInput = document.getElementById('formUserApiKey');
    if (kInput) kInput.value = '';
    const nInput = document.getElementById('formUserNotes');
    if (nInput) nInput.value = '';
    const aInput = document.getElementById('formUserIsActive');
    if (aInput) aInput.checked = true;

    renderUserServicesCheckboxes(lcarsAvailableServices, lcarsCategoriesList);
    document.querySelectorAll('.user-svc-cb').forEach(cb => { cb.checked = false; });
    const allCb = document.getElementById('userSvc_all');
    if (allCb) allCb.checked = true;

    const modal = document.getElementById('userFormModal');
    if (modal) modal.style.display = 'flex';
    if (uInput) uInput.focus();
  }

  function openEditUserModal(userId) {
    const user = lcarsUsersList.find(u => u.id === userId);
    if (!user) return;
    playLcarsBeep(700, 900);
    const titleEl = document.getElementById('userFormModalTitle');
    if (titleEl) titleEl.textContent = 'BENUTZER BEARBEITEN: ' + user.username.toUpperCase();
    document.getElementById('formUserId').value = user.id;
    const uInput = document.getElementById('formUserUsername');
    if (uInput) {
      uInput.value = user.username;
      uInput.disabled = true;
    }

    const pGroup = document.getElementById('formUserPasswordGroup');
    if (pGroup) pGroup.style.display = 'none';
    const pInput = document.getElementById('formUserPassword');
    if (pInput) pInput.required = false;

    const dInput = document.getElementById('formUserDisplayName');
    if (dInput) dInput.value = user.display_name || '';
    const kInput = document.getElementById('formUserApiKey');
    if (kInput) kInput.value = user.api_key || '';
    const nInput = document.getElementById('formUserNotes');
    if (nInput) nInput.value = user.notes || '';
    const aInput = document.getElementById('formUserIsActive');
    if (aInput) aInput.checked = user.is_active;

    renderUserServicesCheckboxes(lcarsAvailableServices, lcarsCategoriesList);
    const svcs = user.allowed_services || [];
    const isAll = svcs.includes('*') || svcs.includes('all');
    const allCb = document.getElementById('userSvc_all');
    if (allCb) allCb.checked = isAll;
    document.querySelectorAll('.user-svc-cb').forEach(cb => {
      if (cb.id !== 'userSvc_all') {
        cb.checked = isAll || svcs.includes(cb.value);
      }
    });

    const modal = document.getElementById('userFormModal');
    if (modal) modal.style.display = 'flex';
  }

  function closeUserFormModal() {
    playLcarsBeep(440, 220);
    const modal = document.getElementById('userFormModal');
    if (modal) modal.style.display = 'none';
  }

  function handleUserSvcAllToggle() {
    const allCb = document.getElementById('userSvc_all');
    const shouldCheck = allCb && allCb.checked;
    document.querySelectorAll('.user-svc-cb').forEach(cb => {
      if (cb.id !== 'userSvc_all') cb.checked = shouldCheck;
    });
  }

  async function submitUserForm(event) {
    if (event) event.preventDefault();
    const userId = document.getElementById('formUserId').value;
    const isEdit = Boolean(userId);

    const username = document.getElementById('formUserUsername').value.trim();
    const displayName = document.getElementById('formUserDisplayName').value.trim();
    const apiKey = (document.getElementById('formUserApiKey')?.value || '').trim();
    const notes = document.getElementById('formUserNotes').value.trim();
    const isActive = document.getElementById('formUserIsActive').checked;

    let services = [];
    const allCb = document.getElementById('userSvc_all');
    if (allCb && allCb.checked) {
      services = ['*'];
    } else {
      document.querySelectorAll('.user-svc-cb').forEach(cb => {
        if (cb.id !== 'userSvc_all' && cb.checked) {
          services.push(cb.value);
        }
      });
    }

    try {
      if (isEdit) {
        const resp = await fetch('/api/users/' + userId, {
          method: 'PUT',
          headers: getLcarsAuthHeader(),
          body: JSON.stringify({
            display_name: displayName,
            is_active: isActive,
            allowed_services: services,
            api_key: apiKey,
            notes: notes
          })
        });
        const res = await resp.json();
        if (resp.ok && res.success) {
          playLcarsAcknowledge();
          closeUserFormModal();
          loadLcarsUsers();
          loadLcarsAuditLog();
        } else {
          alert('Fehler beim Aktualisieren: ' + (res.error || 'Unbekannt'));
        }
      } else {
        const password = document.getElementById('formUserPassword').value;
        const resp = await fetch('/api/users', {
          method: 'POST',
          headers: getLcarsAuthHeader(),
          body: JSON.stringify({
            username: username,
            password: password,
            display_name: displayName,
            allowed_services: services,
            api_key: apiKey,
            notes: notes
          })
        });
        const res = await resp.json();
        if (resp.ok && res.success) {
          playLcarsAcknowledge();
          closeUserFormModal();
          loadLcarsUsers();
          loadLcarsAuditLog();
        } else {
          alert('Fehler beim Erstellen: ' + (res.error || 'Unbekannt'));
        }
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function toggleUserActive(userId, currentActive) {
    playLcarsBeep(600, 800);
    try {
      const resp = await fetch('/api/users/' + userId, {
        method: 'PUT',
        headers: getLcarsAuthHeader(),
        body: JSON.stringify({ is_active: !currentActive })
      });
      const res = await resp.json();
      if (resp.ok && res.success) {
        playLcarsAcknowledge();
        loadLcarsUsers();
        loadLcarsAuditLog();
      } else {
        alert('Fehler beim Ändern des Status: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  function openChangePasswordModal(userId) {
    const user = (typeof lcarsUsersList !== 'undefined' ? lcarsUsersList : []).find(u => u.id === userId);
    const username = user ? user.username : ('ID ' + userId);
    playLcarsBeep(700, 900);
    document.getElementById('pwdModalUserId').value = userId;
    const nameEl = document.getElementById('pwdModalUsername');
    if (nameEl) nameEl.textContent = username;
    const pInput = document.getElementById('pwdModalNewPassword');
    if (pInput) pInput.value = '';
    const kInput = document.getElementById('pwdModalApiKey');
    if (kInput) kInput.value = (user && user.api_key) ? user.api_key : '';
    const statEl = document.getElementById('pwdModalStatus');
    if (statEl) statEl.style.display = 'none';
    const modal = document.getElementById('userPasswordModal');
    if (modal) modal.style.display = 'flex';
    if (pInput) pInput.focus();
  }

  function closeChangePasswordModal() {
    playLcarsBeep(440, 220);
    const modal = document.getElementById('userPasswordModal');
    if (modal) modal.style.display = 'none';
  }

  function generatePwdModalKey() {
    playLcarsBeep(900, 1200);
    const key = 'lcars_' + Array.from(crypto.getRandomValues(new Uint8Array(16))).map(b => b.toString(16).padStart(2, '0')).join('');
    const inp = document.getElementById('pwdModalApiKey');
    if (inp) inp.value = key;
  }

  async function saveUserApiKey() {
    const userId = document.getElementById('pwdModalUserId').value;
    const key = (document.getElementById('pwdModalApiKey')?.value || '').trim();
    const statEl = document.getElementById('pwdModalStatus');
    try {
      const resp = await fetch('/api/users/' + userId + '/key', {
        method: 'POST',
        headers: getLcarsAuthHeader(),
        body: JSON.stringify({ api_key: key })
      });
      const res = await resp.json();
      if (resp.ok && res.success) {
        playLcarsAcknowledge();
        if (statEl) {
          statEl.style.display = 'block';
          statEl.style.background = 'rgba(68,221,136,0.15)';
          statEl.style.border = '1px solid #44dd88';
          statEl.style.color = '#44dd88';
          statEl.textContent = '✓ ACCESS-KEY ERFOLGREICH GESPEICHERT';
        }
        loadLcarsUsers();
        loadLcarsAuditLog();
      } else {
        alert('Fehler beim Speichern des Keys: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function deleteUserApiKey() {
    const userId = document.getElementById('pwdModalUserId').value;
    const statEl = document.getElementById('pwdModalStatus');
    playLcarsBeep(300, 150);
    if (!confirm('Soll der Access-Key für diesen Benutzer wirklich gelöscht werden?')) return;
    try {
      const resp = await fetch('/api/users/' + userId + '/key', {
        method: 'DELETE',
        headers: getLcarsAuthHeader()
      });
      const res = await resp.json();
      if (resp.ok && res.success) {
        playLcarsAcknowledge();
        const inp = document.getElementById('pwdModalApiKey');
        if (inp) inp.value = '';
        if (statEl) {
          statEl.style.display = 'block';
          statEl.style.background = 'rgba(235,148,58,0.15)';
          statEl.style.border = '1px solid var(--c-primary)';
          statEl.style.color = 'var(--c-primary)';
          statEl.textContent = '✓ ACCESS-KEY GELÖSCHT';
        }
        loadLcarsUsers();
        loadLcarsAuditLog();
      } else {
        alert('Fehler beim Löschen des Keys: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function submitChangeUserPassword(event) {
    if (event) event.preventDefault();
    const userId = document.getElementById('pwdModalUserId').value;
    const newPwd = document.getElementById('pwdModalNewPassword').value;
    if (!newPwd || newPwd.length < 6) {
      alert('Passwort muss mindestens 6 Zeichen lang sein.');
      return;
    }
    try {
      const resp = await fetch('/api/users/' + userId + '/password', {
        method: 'POST',
        headers: getLcarsAuthHeader(),
        body: JSON.stringify({ new_password: newPwd })
      });
      const res = await resp.json();
      if (resp.ok && res.success) {
        playLcarsAcknowledge();
        const statEl = document.getElementById('pwdModalStatus');
        if (statEl) {
          statEl.style.display = 'block';
          statEl.style.background = 'rgba(68,221,136,0.15)';
          statEl.style.border = '1px solid #44dd88';
          statEl.style.color = '#44dd88';
          statEl.textContent = '✓ PASSWORT ERFOLGREICH AKTUALISIERT';
        }
        loadLcarsAuditLog();
        alert('Passwort für Benutzer erfolgreich aktualisiert!');
      } else {
        alert('Fehler beim Ändern des Passworts: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function deleteLcarsUser(userId) {
    const user = (typeof lcarsUsersList !== 'undefined' ? lcarsUsersList : []).find(u => u.id === userId);
    const username = user ? user.username : ('ID ' + userId);
    playLcarsBeep(300, 150);
    if (!confirm('LCARS SICHERHEITSABFRAGE:\\n\\nSoll der Benutzer "' + username + '" wirklich gelöscht werden?')) {
      return;
    }
    try {
      const resp = await fetch('/api/users/' + userId, {
        method: 'DELETE',
        headers: getLcarsAuthHeader()
      });
      const res = await resp.json();
      if (resp.ok && res.success) {
        playLcarsAcknowledge();
        loadLcarsUsers();
        loadLcarsAuditLog();
      } else {
        alert('Fehler beim Löschen: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  // ==========================================================================
  // LCARS IN-DASHBOARD LOGIN OVERLAY CONTROLLER
  // ==========================================================================
  let overlayAuthMode = 'credentials';

  function openLcarsLoginOverlay() {
    playLcarsBeep(700, 900);
    const modal = document.getElementById('lcarsLoginOverlay');
    const stat = document.getElementById('overlayStatusBox');
    if (stat) stat.style.display = 'none';
    const form = document.getElementById('lcarsOverlayLoginForm');
    if (form) form.reset();
    switchOverlayLoginMode('credentials');
    if (modal) modal.style.display = 'flex';
    const u = document.getElementById('overlayUsername');
    if (u) u.focus();
  }

  function closeLcarsLoginOverlay() {
    playLcarsBeep(440, 220);
    const modal = document.getElementById('lcarsLoginOverlay');
    if (modal) modal.style.display = 'none';
  }

  function switchOverlayLoginMode(mode) {
    overlayAuthMode = mode;
    playLcarsBeep(700, 900);
    const tabCred = document.getElementById('overlayTabCredentials');
    const tabKey = document.getElementById('overlayTabKey');
    const grpCred = document.getElementById('overlayCredentialsGroup');
    const grpKey = document.getElementById('overlayKeyGroup');
    const stat = document.getElementById('overlayStatusBox');
    if (stat) stat.style.display = 'none';

    if (mode === 'credentials') {
      if (tabCred) {
        tabCred.style.background = 'var(--c-primary)';
        tabCred.style.color = '#000';
        tabCred.style.borderColor = 'var(--c-primary)';
      }
      if (tabKey) {
        tabKey.style.background = 'transparent';
        tabKey.style.color = 'var(--c-gold)';
        tabKey.style.borderColor = 'var(--c-gold)';
      }
      if (grpCred) grpCred.style.display = 'block';
      if (grpKey) grpKey.style.display = 'none';
      const u = document.getElementById('overlayUsername');
      if (u) u.focus();
    } else {
      if (tabKey) {
        tabKey.style.background = 'var(--c-gold)';
        tabKey.style.color = '#000';
        tabKey.style.borderColor = 'var(--c-gold)';
      }
      if (tabCred) {
        tabCred.style.background = 'transparent';
        tabCred.style.color = 'var(--c-primary)';
        tabCred.style.borderColor = 'var(--c-primary)';
      }
      if (grpCred) grpCred.style.display = 'none';
      if (grpKey) grpKey.style.display = 'block';
      const k = document.getElementById('overlayApiKey');
      if (k) k.focus();
    }
  }

  async function submitLcarsLoginOverlay(event) {
    if (event) event.preventDefault();
    playLcarsBeep(880, 1100);

    const btn = document.getElementById('overlaySubmitBtn');
    const stat = document.getElementById('overlayStatusBox');
    const remember = document.getElementById('overlayRemember')?.checked || false;

    let payload = { remember: remember, return_to: '/' };

    if (overlayAuthMode === 'credentials') {
      const username = (document.getElementById('overlayUsername')?.value || '').trim();
      const password = document.getElementById('overlayPassword')?.value || '';
      if (!username || !password) {
        if (stat) {
          stat.style.display = 'block';
          stat.style.background = 'rgba(207,79,79,0.2)';
          stat.style.border = '1px solid var(--c-red)';
          stat.style.color = '#ff8888';
          stat.textContent = 'BENUTZERNAME UND PASSWORT ERFORDERLICH';
        }
        playLcarsBeep(300, 150);
        return;
      }
      payload.username = username;
      payload.password = password;
    } else {
      const apiKey = (document.getElementById('overlayApiKey')?.value || '').trim();
      if (!apiKey) {
        if (stat) {
          stat.style.display = 'block';
          stat.style.background = 'rgba(207,79,79,0.2)';
          stat.style.border = '1px solid var(--c-red)';
          stat.style.color = '#ff8888';
          stat.textContent = 'ACCESS-KEY ERFORDERLICH';
        }
        playLcarsBeep(300, 150);
        return;
      }
      payload.api_key = apiKey;
    }

    if (btn) btn.disabled = true;
    if (stat) {
      stat.style.display = 'block';
      stat.style.background = 'rgba(136,153,255,0.15)';
      stat.style.border = '1px solid var(--c-blue)';
      stat.style.color = 'var(--c-blue)';
      stat.textContent = 'AUTORISIERUNG WIRD GEPRÜFT...';
    }

    try {
      const resp = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await resp.json();

      if (resp.ok && data.success) {
        playLcarsAcknowledge();
        if (stat) {
          stat.style.display = 'block';
          stat.style.background = 'rgba(68,221,136,0.2)';
          stat.style.border = '1px solid #44dd88';
          stat.style.color = '#44dd88';
          stat.textContent = 'AUTORISIERUNG ERFOLGREICH // ANMELDUNG AKTIV';
        }
        setTimeout(() => {
          closeLcarsLoginOverlay();
          checkLcarsAuthUser();
          fetchPermissionsStatus();
          if (currentCategory === 'config') {
            loadLcarsUsers();
            loadLcarsAuditLog();
          }
        }, 500);
      } else {
        playLcarsBeep(300, 150);
        if (stat) {
          stat.style.display = 'block';
          stat.style.background = 'rgba(207,79,79,0.2)';
          stat.style.border = '1px solid var(--c-red)';
          stat.style.color = '#ff8888';
          stat.textContent = data.error || 'ZUGRIFF VERWEIGERT // UNGÜLTIGE ANMELDEDATEN';
        }
        if (btn) btn.disabled = false;
      }
    } catch (err) {
      playLcarsBeep(300, 150);
      if (stat) {
        stat.style.display = 'block';
        stat.style.background = 'rgba(207,79,79,0.2)';
        stat.style.border = '1px solid var(--c-red)';
        stat.style.color = '#ff8888';
        stat.textContent = 'NETZWERKFEHLER: ' + err;
      }
      if (btn) btn.disabled = false;
    }
  }

  async function loadLcarsAuditLog() {
    const box = document.getElementById('lcarsAuditLogList');
    if (!box) return;
    try {
      const resp = await fetch('/api/users/audit-log?limit=25', { headers: getLcarsAuthHeader() });
      if (resp.ok) {
        const data = await resp.json();
        const logs = data.audit_log || [];
        if (logs.length === 0) {
          box.innerHTML = '<div style="color:#666;">Keine Authentifizierungsereignisse protokolliert.</div>';
          return;
        }
        let html = '';
        logs.forEach(l => {
          let color = '#aaa';
          if (l.event.includes('SUCCESS') || l.event.includes('CREATED')) color = '#44dd88';
          else if (l.event.includes('FAILED') || l.event.includes('DENIED')) color = 'var(--c-red)';
          else if (l.event.includes('DELETED') || l.event.includes('UPDATED')) color = 'var(--c-primary)';

          const ipStr = l.ip ? ' [' + escapeHtml(l.ip) + ']' : '';
          const svcStr = l.target_service ? ' (' + escapeHtml(l.target_service) + ')' : '';
          const detailsStr = l.details ? ' - ' + escapeHtml(l.details) : '';

          html += '<div style="margin-bottom:2px;">' +
            '<span style="color:#666;">' + escapeHtml(l.timestamp) + '</span> ' +
            '<span style="color:' + color + '; font-weight:700;">' + escapeHtml(l.event) + '</span> ' +
            '<span style="color:var(--c-gold);">' + escapeHtml(l.username) + '</span>' + svcStr + ipStr +
            '<span style="color:#888;">' + detailsStr + '</span>' +
          '</div>';
        });
        box.innerHTML = html;
      }
    } catch (e) {
      console.warn('loadLcarsAuditLog error:', e);
    }
  }


  // ==========================================================================
  // LCARS PARTNERINNEN-ZYKLUS TRACKER CONTROLLER
  // ==========================================================================
  var cycleChart = null;
  var cyclePartners = [];
  var activePartnerId = null;

  const PARTNER_PALETTE = [
    { border: '#eb943a', bg: 'rgba(235, 148, 58, 0.12)', fill: 'rgba(235, 148, 58, 0.07)', name: 'LCARS Amber' },
    { border: '#baa4e5', bg: 'rgba(186, 164, 229, 0.12)', fill: 'rgba(186, 164, 229, 0.07)', name: 'LCARS Lilac' },
    { border: '#4cd964', bg: 'rgba(76, 217, 100, 0.12)', fill: 'rgba(76, 217, 100, 0.07)', name: 'LCARS Green' },
    { border: '#5ac8fa', bg: 'rgba(90, 200, 250, 0.12)', fill: 'rgba(90, 200, 250, 0.07)', name: 'LCARS Cyan' },
    { border: '#ff2d55', bg: 'rgba(255, 45, 85, 0.12)', fill: 'rgba(255, 45, 85, 0.07)', name: 'LCARS Rose' },
    { border: '#ffcc00', bg: 'rgba(255, 204, 0, 0.12)', fill: 'rgba(255, 204, 0, 0.07)', name: 'LCARS Gold' },
    { border: '#ff9500', bg: 'rgba(255, 149, 0, 0.12)', fill: 'rgba(255, 149, 0, 0.07)', name: 'LCARS Orange' },
    { border: '#af52de', bg: 'rgba(175, 82, 222, 0.12)', fill: 'rgba(175, 82, 222, 0.07)', name: 'LCARS Purple' }
  ];

  async function loadCycleData() {
    try {
      const resp = await fetch('/api/cycle/partners');
      if (resp.ok) {
        const data = await resp.json();
        cyclePartners = data.partners || [];
        renderCycleUI();
      }
    } catch (e) {
      console.warn('Fehler beim Laden der Zyklusdaten:', e);
    }
  }

  function renderCycleUI() {
    const emptyState = document.getElementById('cycleEmptyState');
    const activeContent = document.getElementById('cycleActiveContent');
    const pillsContainer = document.getElementById('cyclePartnerPills');
    const countBadge = document.getElementById('multiCycleCountBadge');

    if (!cyclePartners || cyclePartners.length === 0) {
      if (emptyState) emptyState.style.display = 'block';
      if (activeContent) activeContent.style.display = 'none';
      if (pillsContainer) pillsContainer.innerHTML = '';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';
    if (activeContent) activeContent.style.display = 'block';

    if (countBadge) {
      countBadge.textContent = `${cyclePartners.length} PARTNERIN${cyclePartners.length > 1 ? 'NEN' : ''} ERFASST // VERGLEICHSGRAPH`;
    }

    // Determine active partner for detail inspect
    let activeP = cyclePartners.find(p => p.id === activePartnerId);
    if (!activeP) {
      activeP = cyclePartners[0];
      activePartnerId = activeP.id;
    }

    // 1. Render Multi-Partner Badges at the top
    renderMultiPartnerBadges();

    // 2. Render Combined Multi-Partner Chart (1-30 Days) directly at top
    renderCombinedCycleChart();

    // 3. Render Profile Switcher Pills
    if (pillsContainer) {
      pillsContainer.innerHTML = '';
      cyclePartners.forEach((p, idx) => {
        const pal = PARTNER_PALETTE[idx % PARTNER_PALETTE.length];
        const isActive = (p.id === activePartnerId);
        const pill = document.createElement('button');
        pill.className = 'left-action-btn' + (isActive ? ' active-range' : '');
        pill.style.padding = '0.35rem 0.85rem';
        pill.style.fontSize = '0.82rem';
        pill.style.borderColor = isActive ? pal.border : '#666';
        pill.style.color = isActive ? '#ffffff' : '#bbb';
        pill.innerHTML = `<span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${pal.border}; margin-right:4px;"></span> <strong>${escapeHtml(p.name)}</strong> <span style="font-size:0.75rem; color:${isActive ? 'var(--c-gold)' : '#888'};">★ Tag ${p.current_day}/${p.cycle_duration}</span>`;
        pill.onclick = () => {
          playLcarsBeep(880, 1320);
          activePartnerId = p.id;
          renderCycleUI();
        };
        pillsContainer.appendChild(pill);
      });
    }

    // 4. Render Active Partner Details (vitals, tape)
    renderActivePartner(activeP);

    // 5. Render Partners Overview Grid
    renderPartnersOverviewGrid();
  }

  function renderMultiPartnerBadges() {
    const container = document.getElementById('cycleMultiPartnerBadges');
    if (!container) return;
    container.innerHTML = '';

    cyclePartners.forEach((p, idx) => {
      const pal = PARTNER_PALETTE[idx % PARTNER_PALETTE.length];
      const isSelected = (p.id === activePartnerId);
      const badge = document.createElement('div');
      badge.style.background = isSelected ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.55)';
      badge.style.border = `1px solid ${pal.border}`;
      badge.style.borderLeft = `5px solid ${pal.border}`;
      badge.style.borderRadius = '5px';
      badge.style.padding = '0.35rem 0.75rem';
      badge.style.display = 'inline-flex';
      badge.style.alignItems = 'center';
      badge.style.gap = '0.5rem';
      badge.style.cursor = 'pointer';
      badge.style.transition = 'all 0.2s ease';
      badge.style.fontFamily = 'var(--mono-family)';
      badge.style.fontSize = '0.82rem';

      const phaseName = p.current_phase ? p.current_phase.name : '';
      badge.innerHTML = `
        <span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:${pal.border}; box-shadow:0 0 6px ${pal.border};"></span>
        <strong style="color:#ffffff; font-family:var(--font-family); font-size:0.95rem;">${escapeHtml(p.name)}</strong>
        <span style="background:${pal.border}; color:#000000; font-weight:800; padding:0.12rem 0.45rem; border-radius:3px; font-size:0.75rem;">★ HEUTE: TAG ${p.current_day}/${p.cycle_duration}</span>
        <span style="color:#ddd; font-size:0.75rem;">${escapeHtml(phaseName)}</span>
      `;
      badge.onclick = () => {
        playLcarsBeep(880, 1320);
        activePartnerId = p.id;
        renderCycleUI();
        const activeSection = document.getElementById('activePartnerName');
        if (activeSection) {
          activeSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      };
      container.appendChild(badge);
    });
  }

  function renderActivePartner(p) {
    const nameEl = document.getElementById('activePartnerName');
    const subEl = document.getElementById('activePartnerSub');
    const tapePartnerEl = document.getElementById('tapePartnerName');
    const curDayEl = document.getElementById('dispCurrentDay');
    const cycleDurEl = document.getElementById('dispCycleDuration');
    const progressEl = document.getElementById('dispCycleProgress');
    const phaseBadgeEl = document.getElementById('dispPhaseBadge');
    const phaseDescEl = document.getElementById('dispPhaseDesc');
    const fertilityEl = document.getElementById('dispFertilityStatus');
    const daysUntilEl = document.getElementById('dispDaysUntilNext');
    const nextDateEl = document.getElementById('dispNextDate');

    if (nameEl) nameEl.textContent = p.name;
    if (subEl) subEl.textContent = `ZYKLUS: ${p.cycle_duration} TAGE // BLUTUNG: ${p.period_duration} TAGE // START: ${p.start_date_formatted || p.start_date}`;
    if (tapePartnerEl) tapePartnerEl.textContent = p.name.toUpperCase();
    if (curDayEl) curDayEl.textContent = `TAG ${p.current_day}`;
    if (cycleDurEl) cycleDurEl.textContent = `/ ${p.cycle_duration} TAGE`;
    if (progressEl) progressEl.style.width = `${p.progress_percent || 50}%`;

    if (phaseBadgeEl && p.current_phase) {
      phaseBadgeEl.textContent = p.current_phase.name || 'UNBEKANNT';
      phaseBadgeEl.style.backgroundColor = p.current_phase.color || '#baa4e5';
      phaseBadgeEl.style.color = (p.current_phase.key === 'menstruation' ? '#ffffff' : '#000000');
    }
    if (phaseDescEl && p.current_phase) {
      phaseDescEl.textContent = p.current_phase.desc || '';
    }

    if (fertilityEl && p.current_phase) {
      fertilityEl.textContent = (p.current_phase.fertility || 'Normal').toUpperCase();
    }
    if (daysUntilEl) daysUntilEl.textContent = p.days_until_next_period;
    if (nextDateEl) nextDateEl.textContent = p.next_period_date;

    // Render 30-Day Tape for Active Partner
    renderCycleTape(p.days_graph || [], p.current_day);
  }

  function renderCombinedCycleChart() {
    const canvas = document.getElementById('cycleChart');
    if (!canvas) return;

    const existing = Chart.getChart(canvas);
    if (existing) {
      try { existing.destroy(); } catch (e) {}
    }

    if (!cyclePartners || cyclePartners.length === 0) return;

    const labels = Array.from({ length: 30 }, (_, i) => 'Tag ' + (i + 1));
    const datasets = [];

    cyclePartners.forEach((p, idx) => {
      const pal = PARTNER_PALETTE[idx % PARTNER_PALETTE.length];
      const isSelected = (p.id === activePartnerId);
      const curDay = p.current_day;
      const days = p.days_graph || [];

      const values = [];
      const pointRadii = [];
      const pointHoverRadii = [];
      const pointBgColors = [];
      const pointBorderColors = [];
      const pointBorderWidths = [];

      for (let d = 1; d <= 30; d++) {
        const dayObj = days.find(x => x.day === d);
        values.push(dayObj ? dayObj.curve_value : 20);

        if (d === curDay) {
          pointRadii.push(isSelected ? 11 : 9);
          pointHoverRadii.push(isSelected ? 15 : 13);
          pointBgColors.push('#ffffff');
          pointBorderColors.push(pal.border);
          pointBorderWidths.push(3.5);
        } else {
          pointRadii.push(3);
          pointHoverRadii.push(6);
          pointBgColors.push(pal.border);
          pointBorderColors.push('#000000');
          pointBorderWidths.push(1);
        }
      }

      datasets.push({
        type: 'line',
        label: `${p.name} (★ Tag ${curDay}/${p.cycle_duration})`,
        data: values,
        borderColor: pal.border,
        backgroundColor: pal.fill,
        borderWidth: isSelected ? 3.5 : 2.2,
        tension: 0.35,
        fill: true,
        pointRadius: pointRadii,
        pointHoverRadius: pointHoverRadii,
        pointBackgroundColor: pointBgColors,
        pointBorderColor: pointBorderColors,
        pointBorderWidth: pointBorderWidths,
        order: isSelected ? 1 : 2
      });
    });

    // Custom Chart.js plugin to draw vertical highlight bands, indicator lines and name tags
    const multiHighlightPlugin = {
      id: 'multiHighlightPlugin',
      beforeDatasetsDraw(chart) {
        const { ctx, chartArea, scales: { x } } = chart;
        if (!chartArea || !x || !cyclePartners || cyclePartners.length === 0) return;

        cyclePartners.forEach((p, idx) => {
          const day = p.current_day;
          if (day >= 1 && day <= 30) {
            const xPos = x.getPixelForValue(day - 1);
            const pal = PARTNER_PALETTE[idx % PARTNER_PALETTE.length];
            const isSelected = (p.id === activePartnerId);

            ctx.save();
            // Vertical glow band around current day
            const bandWidth = Math.max(16, (chartArea.width / 30) * 0.75);
            ctx.fillStyle = isSelected ? pal.bg.replace('0.12', '0.24') : pal.bg.replace('0.12', '0.12');
            ctx.fillRect(xPos - bandWidth / 2, chartArea.top, bandWidth, chartArea.height);

            // Vertical dashed indicator line
            ctx.strokeStyle = pal.border;
            ctx.lineWidth = isSelected ? 2.2 : 1.2;
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(xPos, chartArea.top);
            ctx.lineTo(xPos, chartArea.bottom);
            ctx.stroke();

            // Marker badge tag at top
            ctx.setLineDash([]);
            const tagText = `★ ${p.name} (T${day})`;
            ctx.font = `bold ${isSelected ? '11px' : '10px'} "Share Tech Mono", monospace`;
            const textMetrics = ctx.measureText(tagText);
            const tagW = textMetrics.width + 10;
            const tagH = 15;
            const tagY = chartArea.top - tagH - 4;

            ctx.fillStyle = pal.border;
            ctx.fillRect(xPos - tagW / 2, tagY, tagW, tagH);
            ctx.fillStyle = '#000000';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(tagText, xPos, tagY + tagH / 2 + 0.5);

            ctx.restore();
          }
        });
      }
    };

    cycleChart = new Chart(canvas, {
      data: {
        labels: labels,
        datasets: datasets
      },
      plugins: [multiHighlightPlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: {
          padding: {
            top: 24,
            right: 15,
            bottom: 5,
            left: 10
          }
        },
        animation: { duration: 350 },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: {
              color: '#ffffff',
              font: { family: 'Share Tech Mono', size: 12 },
              boxWidth: 14,
              padding: 14,
              usePointStyle: true,
              pointStyle: 'circle'
            }
          },
          tooltip: {
            backgroundColor: 'rgba(10, 10, 18, 0.95)',
            titleColor: '#eb943a',
            titleFont: { family: 'Antonio', size: 14 },
            bodyColor: '#ffffff',
            bodyFont: { family: 'Share Tech Mono', size: 12 },
            borderColor: 'rgba(235, 148, 58, 0.6)',
            borderWidth: 1,
            padding: 10,
            callbacks: {
              title: function(items) {
                if (!items || items.length === 0) return '';
                const dayNum = items[0].dataIndex + 1;
                const todayPartners = cyclePartners.filter(p => p.current_day === dayNum);
                let t = `TAG ${dayNum} // BIO-STATUS`;
                if (todayPartners.length > 0) {
                  t += ` ★ HEUTE: ` + todayPartners.map(p => p.name).join(', ');
                }
                return t;
              },
              label: function(item) {
                const pIdx = item.datasetIndex;
                const partner = cyclePartners[pIdx];
                if (!partner) return '';
                const dayNum = item.dataIndex + 1;
                const d = (partner.days_graph || []).find(x => x.day === dayNum);
                const isCur = (partner.current_day === dayNum);
                const phaseStr = d ? `${d.phase_name} (${d.fertility})` : '';
                return ` ${partner.name}: ${item.raw}% - ${phaseStr}${isCur ? ' ★ HEUTE!' : ''}`;
              }
            }
          }
        },
        scales: {
          x: {
            grid: {
              color: function(ctx) {
                const day = ctx.index + 1;
                const isToday = cyclePartners.some(p => p.current_day === day);
                return isToday ? 'rgba(255, 255, 255, 0.25)' : 'rgba(255, 255, 255, 0.05)';
              },
              lineWidth: function(ctx) {
                const day = ctx.index + 1;
                return cyclePartners.some(p => p.current_day === day) ? 2 : 1;
              }
            },
            ticks: {
              color: function(ctx) {
                const day = ctx.index + 1;
                const matching = cyclePartners.find(p => p.current_day === day);
                if (matching) {
                  const idx = cyclePartners.indexOf(matching);
                  return PARTNER_PALETTE[idx % PARTNER_PALETTE.length].border;
                }
                return '#888888';
              },
              font: function(ctx) {
                const day = ctx.index + 1;
                const isToday = cyclePartners.some(p => p.current_day === day);
                return {
                  family: 'Share Tech Mono',
                  size: isToday ? 12 : 10,
                  weight: isToday ? 'bold' : 'normal'
                };
              }
            }
          },
          y: {
            min: 0,
            max: 115,
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: {
              color: '#777777',
              font: { family: 'Share Tech Mono', size: 10 },
              callback: function(v) {
                if (v === 20) return 'Ruhe / Regel';
                if (v === 55) return 'Aktiv';
                if (v === 100) return 'Peak (Eisprung)';
                return '';
              }
            }
          }
        }
      }
    });
  }

  function renderCycleTape(daysGraph, currentDay) {
    const container = document.getElementById('cycleTapeContainer');
    if (!container) return;
    container.innerHTML = '';

    daysGraph.forEach(d => {
      const isCur = (d.day === currentDay);
      const cell = document.createElement('div');
      cell.className = 'cycle-tape-cell' + (isCur ? ' current-day' : '');
      cell.style.backgroundColor = isCur ? '#ffffff' : d.color;
      cell.style.color = (d.phase_key === 'menstruation' && !isCur) ? '#ffffff' : '#000000';
      cell.innerHTML = `<span>${isCur ? '★' : ''}${d.day}</span>`;
      cell.title = `Tag ${d.day}: ${d.phase_name} (${d.fertility})`;
      cell.onclick = () => {
        playLcarsBeep(700, 1050);
        inspectDay(d.day, d.phase_name, d.fertility, d.desc, isCur);
      };
      container.appendChild(cell);
    });

    // Default inspection: current day
    const curObj = daysGraph.find(d => d.day === currentDay) || daysGraph[0];
    if (curObj) {
      inspectDay(curObj.day, curObj.phase_name, curObj.fertility, curObj.desc, true);
    }
  }

  function inspectDay(day, phaseName, fertility, desc, isCur) {
    const dayLabel = document.getElementById('inspectDayLabel');
    const phaseLabel = document.getElementById('inspectPhaseLabel');
    const todayBadge = document.getElementById('inspectTodayBadge');
    const infoText = document.getElementById('inspectInfoText');

    if (dayLabel) dayLabel.textContent = `TAG ${day}`;
    if (phaseLabel) phaseLabel.textContent = `${phaseName.toUpperCase()} // FRUCHTBARKEIT: ${fertility.toUpperCase()}`;
    if (todayBadge) todayBadge.style.display = isCur ? 'inline' : 'none';
    if (infoText) infoText.textContent = desc || '';
  }

  function renderPartnersOverviewGrid() {
    const grid = document.getElementById('partnersGrid');
    if (!grid) return;
    grid.innerHTML = '';

    cyclePartners.forEach(p => {
      const isSelected = (p.id === activePartnerId);
      const card = document.createElement('div');
      card.style.background = isSelected ? 'rgba(186, 164, 229, 0.15)' : 'rgba(0,0,0,0.4)';
      card.style.border = `1px solid ${isSelected ? 'var(--c-secondary)' : 'rgba(255,255,255,0.1)'}`;
      card.style.borderRadius = '6px';
      card.style.padding = '0.85rem';
      card.style.cursor = 'pointer';
      card.style.transition = 'all 0.2s';
      card.onclick = () => {
        playLcarsBeep(880, 1320);
        activePartnerId = p.id;
        renderCycleUI();
      };

      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.4rem;">
          <strong style="font-size:1.1rem; color:#fff; font-family:var(--font-family);">${escapeHtml(p.name)}</strong>
          <span style="font-family:var(--mono-family); font-size:0.8rem; color:${p.current_phase ? p.current_phase.color : 'var(--c-secondary)'}; font-weight:700;">
            TAG ${p.current_day}/${p.cycle_duration}
          </span>
        </div>
        <div style="font-size:0.8rem; color:#bbb; font-family:var(--mono-family); margin-bottom:0.3rem;">
          Phase: <span style="color:#fff;">${p.current_phase ? p.current_phase.name : '--'}</span>
        </div>
        <div style="font-size:0.75rem; color:#888; font-family:var(--mono-family);">
          Nächste Periode in: <strong style="color:var(--c-gold);">${p.days_until_next_period} Tagen</strong> (${p.next_period_date})
        </div>
      `;
      grid.appendChild(card);
    });
  }

  function openAddPartnerModal() {
    playLcarsBeep(880, 1320);
    const modal = document.getElementById('partnerFormModal');
    const title = document.getElementById('partnerModalTitle');
    const idInp = document.getElementById('formPartnerId');
    const nameInp = document.getElementById('formPartnerName');
    const cycleInp = document.getElementById('formCycleDuration');
    const periodInp = document.getElementById('formPeriodDuration');
    const dateInp = document.getElementById('formStartDate');
    const notesInp = document.getElementById('formNotes');

    if (title) title.textContent = 'PARTNERIN ANLEGEN';
    if (idInp) idInp.value = '';
    if (nameInp) nameInp.value = '';
    if (cycleInp) cycleInp.value = '28';
    if (periodInp) periodInp.value = '5';
    if (dateInp) dateInp.value = new Date().toISOString().split('T')[0];
    if (notesInp) notesInp.value = '';

    if (modal) {
      modal.style.display = 'flex';
      if (nameInp) nameInp.focus();
    }
  }

  function openEditPartnerModal() {
    const p = cyclePartners.find(x => x.id === activePartnerId);
    if (!p) return;

    playLcarsBeep(880, 1320);
    const modal = document.getElementById('partnerFormModal');
    const title = document.getElementById('partnerModalTitle');
    const idInp = document.getElementById('formPartnerId');
    const nameInp = document.getElementById('formPartnerName');
    const cycleInp = document.getElementById('formCycleDuration');
    const periodInp = document.getElementById('formPeriodDuration');
    const dateInp = document.getElementById('formStartDate');
    const notesInp = document.getElementById('formNotes');

    if (title) title.textContent = `PARTNERIN BEARBEITEN // ${p.name.toUpperCase()}`;
    if (idInp) idInp.value = p.id;
    if (nameInp) nameInp.value = p.name;
    if (cycleInp) cycleInp.value = p.cycle_duration || 28;
    if (periodInp) periodInp.value = p.period_duration || 5;
    if (dateInp) dateInp.value = p.start_date || new Date().toISOString().split('T')[0];
    if (notesInp) notesInp.value = p.notes || '';

    if (modal) {
      modal.style.display = 'flex';
      if (nameInp) nameInp.focus();
    }
  }

  function closePartnerModal() {
    playLcarsBeep(440, 220);
    const modal = document.getElementById('partnerFormModal');
    if (modal) modal.style.display = 'none';
  }

  function handlePartnerModalBackdropClick(e) {
    if (e.target && e.target.id === 'partnerFormModal') {
      closePartnerModal();
    }
  }

  async function savePartnerData(e) {
    e.preventDefault();
    const idInp = document.getElementById('formPartnerId');
    const nameInp = document.getElementById('formPartnerName');
    const cycleInp = document.getElementById('formCycleDuration');
    const periodInp = document.getElementById('formPeriodDuration');
    const dateInp = document.getElementById('formStartDate');
    const notesInp = document.getElementById('formNotes');

    const partnerId = idInp ? idInp.value.trim() : '';
    const payload = {
      name: nameInp ? nameInp.value.trim() : '',
      cycle_duration: cycleInp ? parseInt(cycleInp.value) || 28 : 28,
      period_duration: periodInp ? parseInt(periodInp.value) || 5 : 5,
      start_date: dateInp ? dateInp.value : '',
      notes: notesInp ? notesInp.value.trim() : ''
    };

    try {
      let resp;
      if (partnerId) {
        resp = await fetch(`/api/cycle/partners/${partnerId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      } else {
        resp = await fetch('/api/cycle/partners', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      }
      const res = await resp.json();
      if (res.success) {
        playLcarsAcknowledge();
        closePartnerModal();
        if (res.partner && res.partner.id) {
          activePartnerId = res.partner.id;
        }
        await loadCycleData();
      } else {
        alert('Fehler beim Speichern: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function confirmDeletePartner() {
    const p = cyclePartners.find(x => x.id === activePartnerId);
    if (!p) return;

    if (!confirm(`Möchten Sie den Zyklus-Eintrag für "${p.name}" wirklich löschen?`)) {
      return;
    }

    try {
      const resp = await fetch(`/api/cycle/partners/${p.id}`, { method: 'DELETE' });
      const res = await resp.json();
      if (res.success) {
        playLcarsBeep(600, 300);
        activePartnerId = null;
        await loadCycleData();
      } else {
        alert('Fehler beim Löschen: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function triggerNewCycleToday() {
    const p = cyclePartners.find(x => x.id === activePartnerId);
    if (!p) return;

    if (!confirm(`Neuen Zyklus für "${p.name}" mit heutigem Datum als Tag 1 starten?`)) {
      return;
    }

    try {
      const todayStr = new Date().toISOString().split('T')[0];
      const resp = await fetch(`/api/cycle/partners/${p.id}/start-cycle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start_date: todayStr })
      });
      const res = await resp.json();
      if (res.success) {
        playLcarsAcknowledge();
        await loadCycleData();
      } else {
        alert('Fehler beim Aktualisieren: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  // Window Resize Listener
  window.addEventListener('resize', () => {
    if (historyChart) historyChart.resize();
    else {
      const c = document.getElementById('historyChart');
      if (c) renderNativeHistoryChart(c, currentHistorySamples);
    }
    if (hermesChart) hermesChart.resize();
    else {
      const c = document.getElementById('hermesChart');
      if (c) renderNativeDonutChart(c, initialStats?.hermes?.models || []);
    }
    if (nineRouterTimelineChart) nineRouterTimelineChart.resize();
    if (nineRouterModelChart) nineRouterModelChart.resize();
    if (cycleChart) cycleChart.resize();
  });

  // ==========================================================================
  // LCARS PULSECAST CONTROLLER (MEDIA & DOWNLOAD HUB)
  // ==========================================================================
  let pulsecastIsOnline = false;
  let pulsecastPollTimer = null;
  let pulsecastActiveSubtab = 'downloads';
  let pulsecastCatalogCategory = 'Filme';
  let pulsecastCatalogSubcategory = 'all';
  let pulsecastCatalogSearchQuery = '';
  let pulsecastCatalogPage = 1;
  let pulsecastCatalogTotalPages = 1;
  let pulsecastCurrentSeriesEpisodes = [];
  let pulsecastActiveSeries = null;
  let pulsecastDownloadsCache = [];
  let pulsecastXdccSource = 'xdcc';
  let currentPulsecastStreamUrl = '';
  let currentPulsecastFilename = '';
  let currentPulsecastDisplayTitle = '';
  let currentPulsecastAudioTranscode = false;
  let currentPulsecastNeedsTranscode = false;

  function escapeJsString(str) {
    if (!str) return '';
    return JSON.stringify(String(str)).slice(1, -1).replace(/'/g, "\\\\'");
  }

  function getPulsecastHeaders() {
    return {
      'Content-Type': 'application/json'
    };
  }

  async function checkPulsecastStatus() {
    try {
      const resp = await fetch('/api/pulsecast/status');
      if (resp.ok) {
        const data = await resp.json();
        pulsecastIsOnline = !!data.online;
      } else {
        pulsecastIsOnline = false;
      }
    } catch (e) {
      pulsecastIsOnline = false;
    }
    updatePulsecastStatusBadge();
    return pulsecastIsOnline;
  }

  function updatePulsecastStatusBadge() {
    const badge = document.getElementById('pulsecastOnlineBadge');
    if (!badge) return;
    if (pulsecastIsOnline) {
      badge.textContent = 'ONLINE // PORT 3000';
      badge.style.backgroundColor = '#44dd88';
      badge.style.color = '#000000';
    } else {
      badge.textContent = 'SUBRAUM-RELAY OFFLINE';
      badge.style.backgroundColor = 'var(--c-red)';
      badge.style.color = '#ffffff';
    }
  }

  async function initPulsecastSection() {
    const isLocked = isCategoryLocked('pulsecast');
    const gateView = document.getElementById('pulsecastGateView');
    const offlineNotice = document.getElementById('pulsecastOfflineNotice');
    const activeContent = document.getElementById('pulsecastActiveContent');

    if (isLocked) {
      if (gateView) gateView.style.display = 'block';
      if (offlineNotice) offlineNotice.style.display = 'none';
      if (activeContent) activeContent.style.display = 'none';
      stopPulsecastPolling();
      return;
    }

    if (gateView) gateView.style.display = 'none';

    await checkPulsecastStatus();
    if (!pulsecastIsOnline) {
      if (offlineNotice) offlineNotice.style.display = 'block';
      if (activeContent) activeContent.style.display = 'none';
      stopPulsecastPolling();
      return;
    }

    if (offlineNotice) offlineNotice.style.display = 'none';
    if (activeContent) activeContent.style.display = 'block';

    loadPulsecastLocalCounts();
    switchPulsecastSubtab(pulsecastActiveSubtab || 'downloads');
  }

  function refreshPulsecastData(force) {
    playLcarsBeep(1200, 1600);
    initPulsecastSection();
  }

  let pulsecastToastTimeout = null;

  function showPulsecastToast(message, isError = false, timeout = 3500) {
    let toast = document.getElementById('pulsecastToast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'pulsecastToast';
      document.body.appendChild(toast);
    }
    toast.style.cssText = 'position:fixed; bottom:24px; right:24px; z-index:10000; padding:0.65rem 1.2rem; font-family:var(--font-family); font-weight:700; font-size:0.9rem; letter-spacing:0.05em; border-radius:4px; box-shadow:0 4px 18px rgba(0,0,0,0.7); text-transform:uppercase; transition:opacity 0.25s ease; background:' + (isError ? 'var(--c-red)' : 'var(--c-butterscotch)') + '; color:#000; display:block; opacity:1;';
    toast.textContent = message;
    if (pulsecastToastTimeout) clearTimeout(pulsecastToastTimeout);
    pulsecastToastTimeout = setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => { toast.style.display = 'none'; }, 250);
    }, timeout);
  }

  async function triggerPulsecastSync() {
    playLcarsBeep(1200, 1600);
    const btn = document.getElementById('pulsecastSyncBtn');
    const spinner = document.getElementById('pulsecastSyncSpinner');

    if (btn) {
      btn.disabled = true;
      btn.style.opacity = '0.6';
    }
    if (spinner) spinner.style.display = 'inline-block';

    showPulsecastToast('Sync gestartet...');

    try {
      const resp = await fetch('/api/pulsecast/sync', {
        method: 'POST',
        headers: getPulsecastHeaders(),
        body: JSON.stringify({ xtreamSyncIntervalHours: 2 })
      });

      if (!resp.ok) {
        let errMsg = 'Sync fehlgeschlagen';
        try {
          const errData = await resp.json();
          if (errData && errData.error) errMsg = errData.error;
        } catch (_) {}
        showPulsecastToast(`Fehler: ${errMsg}`, true);
        return;
      }

      if (typeof loadPulsecastCatalog === 'function') {
        await loadPulsecastCatalog(pulsecastCatalogPage || 1);
      }
      if (typeof loadPulsecastLocalCounts === 'function') {
        loadPulsecastLocalCounts();
      }
      if (pulsecastActiveSubtab === 'local' && typeof loadPulsecastLocal === 'function') {
        loadPulsecastLocal(pulsecastLocalPage || 1);
      }
    } catch (e) {
      console.error('PulseCast sync error:', e);
      showPulsecastToast('Netzwerkfehler beim Sync', true);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.style.opacity = '1';
      }
      if (spinner) spinner.style.display = 'none';
    }
  }

  function switchPulsecastSubtab(subtab) {
    playLcarsBeep(1100, 1400);
    pulsecastActiveSubtab = subtab;

    document.querySelectorAll('[id^="pulsecast-tab-btn-"]').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById('pulsecast-tab-btn-' + subtab);
    if (activeBtn) activeBtn.classList.add('active');

    document.querySelectorAll('.pulsecast-subview').forEach(view => view.style.display = 'none');
    const activeView = document.getElementById('pulsecast-subview-' + subtab);
    if (activeView) activeView.style.display = 'block';

    if (subtab === 'downloads') {
      loadPulsecastDownloads();
      startPulsecastPolling();
    } else {
      stopPulsecastPolling();
    }

    if (subtab === 'local') {
      loadPulsecastLocal(pulsecastLocalPage);
    }

    if (subtab === 'catalog') {
      loadPulsecastCatalog(pulsecastCatalogPage);
    }

    if (subtab === 'xdcc') {
      const inp = document.getElementById('pulsecastXdccInput');
      if (inp) inp.focus();
    }
  }

  function startPulsecastPolling() {
    stopPulsecastPolling();
    pulsecastPollTimer = setInterval(() => {
      if (currentCategory === 'pulsecast' && pulsecastActiveSubtab === 'downloads' && pulsecastIsOnline) {
        loadPulsecastDownloads(true);
      }
    }, 2500);
  }

  function stopPulsecastPolling() {
    if (pulsecastPollTimer) {
      clearInterval(pulsecastPollTimer);
      pulsecastPollTimer = null;
    }
  }

  function formatBytes(bytes, decimals = 1) {
    if (!bytes || bytes <= 0) return '0 B';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
  }

  function formatEta(seconds) {
    if (!seconds || seconds <= 0 || !isFinite(seconds)) return '--';
    const s = Math.round(seconds);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    if (h > 0) return `${h}h ${m % 60}m`;
    if (m > 0) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }

  async function loadPulsecastDownloads(isPoll = false) {
    try {
      const resp = await fetch('/api/pulsecast/downloads', {
        headers: getPulsecastHeaders()
      });
      if (resp.status === 403) {
        initPulsecastSection();
        return;
      }
      if (!resp.ok) {
        if (resp.status === 503) {
          pulsecastIsOnline = false;
          initPulsecastSection();
        }
        return;
      }
      const downloads = await resp.json();
      pulsecastDownloadsCache = Array.isArray(downloads) ? downloads : [];
      renderPulsecastDownloads(pulsecastDownloadsCache);
    } catch (e) {
      console.warn('Fehler beim Laden der PulseCast Downloads:', e);
    }
  }

  function renderPulsecastDownloads(list) {
    const container = document.getElementById('pulsecastDownloadsList');
    const emptyEl = document.getElementById('pulsecastDownloadsEmpty');
    const badgeEl = document.getElementById('pulsecastDownloadsCountBadge');
    const summaryEl = document.getElementById('pulsecastQueueSummary');
    const speedBadge = document.getElementById('pulsecastActiveSpeedBadge');

    let totalSpeed = 0;
    let activeCount = 0;

    list.forEach(d => {
      if (d.status === 'downloading' || d.status === 'dcc_downloading') {
        activeCount++;
        totalSpeed += (d.speed || 0);
      }
    });

    if (badgeEl) badgeEl.textContent = String(list.length);
    if (summaryEl) summaryEl.textContent = `${activeCount} AKTIV // ${list.length} GESAMT`;
    if (speedBadge) {
      const mbps = (totalSpeed / (1024 * 1024)).toFixed(2);
      speedBadge.textContent = `${mbps} MB/s`;
      speedBadge.style.color = (totalSpeed > 0) ? '#44dd88' : 'var(--c-butterscotch)';
    }

    if (!container) return;

    if (!list || list.length === 0) {
      container.innerHTML = '';
      if (emptyEl) emptyEl.style.display = 'block';
      return;
    }

    if (emptyEl) emptyEl.style.display = 'none';

    let html = '';
    list.forEach(item => {
      const id = item.id || '';
      const filename = item.filename || item.offeredFilename || 'Unbekannte Datei';
      const expectedSize = item.expectedSize || 0;
      const bytesReceived = item.bytesReceived || 0;
      const pct = (expectedSize > 0) ? Math.min(100, (bytesReceived / expectedSize * 100)).toFixed(1) : 0;
      const speedMb = ((item.speed || 0) / (1024 * 1024)).toFixed(2);
      const etaStr = formatEta(item.eta);
      const status = item.status || 'unknown';

      let statusBadge = '';
      let actionButtons = '';

      if (status === 'downloading' || status === 'dcc_downloading') {
        statusBadge = '<span class="badge-status" style="background:#44dd88; color:#000;">LÄUFT</span>';
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="pausePulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-gold); color:var(--c-gold);" title="Pausieren">
            ⏸ PAUSE
          </button>
          <button type="button" class="left-action-btn" onclick="cancelPulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-red); color:var(--c-red);" title="Abbrechen">
            ✕ ABBRECHEN
          </button>
        `;
      } else if (status === 'paused') {
        statusBadge = '<span class="badge-status" style="background:var(--c-gold); color:#000;">PAUSIERT</span>';
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="resumePulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:#44dd88; color:#44dd88;" title="Fortsetzen">
            ▶ WEITER
          </button>
          <button type="button" class="left-action-btn" onclick="cancelPulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-red); color:var(--c-red);" title="Abbrechen">
            ✕ ABBRECHEN
          </button>
        `;
      } else if (status === 'queued' || status === 'connecting') {
        statusBadge = '<span class="badge-status" style="background:var(--c-blue); color:#000;">WARTESCHLANGE</span>';
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="cancelPulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-red); color:var(--c-red);" title="Aus Warteschlange entfernen">
            ✕ ABBRECHEN
          </button>
        `;
      } else if (status === 'completed') {
        statusBadge = '<span class="badge-status" style="background:var(--c-secondary); color:#000;">FERTIG</span>';
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(filename)}', '${escapeJsString(filename)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;" title="Im lokalen Media Player öffnen">
            ▶ IN PLAYER ÖFFNEN
          </button>
          <button type="button" class="left-action-btn" onclick="deletePulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:#888; color:#aaa;" title="Aus Liste entfernen">
            🗑 ENTFERNEN
          </button>
        `;
      } else {
        statusBadge = `<span class="badge-status" style="background:var(--c-red); color:#fff;">${escapeHtml(status.toUpperCase())}</span>`;
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="deletePulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:#888; color:#aaa;" title="Entfernen">
            🗑 ENTFERNEN
          </button>
        `;
      }

      const icon = filename.match(/\\.(mkv|mp4|avi|webm)$/i) ? '🎬' : '📦';

      html += `
        <div style="background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:0.85rem; border-left:4px solid var(--c-butterscotch);">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.4rem;">
            <div style="display:flex; align-items:center; gap:0.5rem; min-width:0; flex:1;">
              <span style="font-size:1.15rem; flex-shrink:0;">${icon}</span>
              <span style="font-family:var(--font-family); font-weight:700; font-size:0.95rem; color:#fff; word-break:break-all;" title="${escapeHtml(filename)}">
                ${escapeHtml(filename)}
              </span>
            </div>
            <div style="display:flex; align-items:center; gap:0.5rem;">
              ${statusBadge}
              ${actionButtons}
            </div>
          </div>

          <!-- LCARS Progress Bar -->
          <div class="pulsecast-progress-container">
            <div class="pulsecast-progress-fill" style="width:${pct}%;"></div>
          </div>

          <!-- Progress Details -->
          <div style="display:flex; justify-content:space-between; align-items:center; font-family:var(--mono-family); font-size:0.8rem; color:#aaa; flex-wrap:wrap; gap:0.4rem; margin-top:0.35rem;">
            <span>${pct}% // ${formatBytes(bytesReceived)} von ${formatBytes(expectedSize)}</span>
            <div style="display:flex; gap:0.8rem; align-items:center;">
              ${(status === 'downloading' || status === 'dcc_downloading') ? `<span style="color:#44dd88;">⚡ ${speedMb} MB/s</span><span style="color:var(--c-gold);">⏳ ${etaStr}</span>` : ''}
              ${item.server ? `<span style="color:#666;">[${escapeHtml(item.server)}]</span>` : ''}
            </div>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  async function pausePulsecastDownload(encodedId) {
    playLcarsBeep(900, 1100);
    try {
      await fetch(`/api/pulsecast/download/${encodedId}/pause`, {
        method: 'POST',
        headers: getPulsecastHeaders()
      });
      loadPulsecastDownloads();
    } catch (e) {
      console.warn('Fehler bei Pause:', e);
    }
  }

  async function resumePulsecastDownload(encodedId) {
    playLcarsBeep(1100, 1400);
    try {
      await fetch(`/api/pulsecast/download/${encodedId}/resume`, {
        method: 'POST',
        headers: getPulsecastHeaders()
      });
      loadPulsecastDownloads();
    } catch (e) {
      console.warn('Fehler bei Resume:', e);
    }
  }

  async function cancelPulsecastDownload(encodedId) {
    playLcarsBeep(400, 200);
    try {
      await fetch(`/api/pulsecast/download/${encodedId}/cancel`, {
        method: 'POST',
        headers: getPulsecastHeaders()
      });
      loadPulsecastDownloads();
    } catch (e) {
      console.warn('Fehler bei Cancel:', e);
    }
  }

  async function deletePulsecastDownload(encodedId) {
    playLcarsBeep(500, 300);
    try {
      await fetch(`/api/pulsecast/download/${encodedId}`, {
        method: 'DELETE',
        headers: getPulsecastHeaders()
      });
      loadPulsecastDownloads();
    } catch (e) {
      console.warn('Fehler bei Delete:', e);
    }
  }

  // KATALOG CONTROLLER
  function setPulsecastCatalogCategory(cat) {
    playLcarsBeep(1000, 1300);
    pulsecastCatalogCategory = cat;
    pulsecastCatalogSubcategory = 'all';
    pulsecastCatalogPage = 1;

    ['Filme', 'Serien', 'all'].forEach(c => {
      const btn = document.getElementById('cat-pill-' + c);
      if (btn) {
        if (c === cat) {
          btn.classList.add('active');
          btn.style.background = (c === 'Serien') ? 'var(--c-secondary)' : 'var(--c-primary)';
          btn.style.color = '#000';
        } else {
          btn.classList.remove('active');
          btn.style.background = 'rgba(0,0,0,0.5)';
          btn.style.color = (c === 'Serien') ? 'var(--c-secondary)' : (c === 'Filme' ? 'var(--c-primary)' : '#aaa');
        }
      }
    });

    loadPulsecastCatalog(1);
  }

  function onPulsecastSubcatChanged() {
    const sel = document.getElementById('pulsecastSubcatSelect');
    if (sel) {
      pulsecastCatalogSubcategory = sel.value;
      pulsecastCatalogPage = 1;
      loadPulsecastCatalog(1);
    }
  }

  function pulsecastCatalogSearchTrigger() {
    const inp = document.getElementById('pulsecastCatalogSearchInput');
    pulsecastCatalogSearchQuery = inp ? inp.value.trim() : '';
    pulsecastCatalogPage = 1;
    loadPulsecastCatalog(1);
  }

  function pulsecastCatalogSearchClear() {
    const inp = document.getElementById('pulsecastCatalogSearchInput');
    if (inp) inp.value = '';
    pulsecastCatalogSearchQuery = '';
    pulsecastCatalogPage = 1;
    loadPulsecastCatalog(1);
  }

  function pulsecastChangePage(delta) {
    const target = pulsecastCatalogPage + delta;
    if (target >= 1 && target <= pulsecastCatalogTotalPages) {
      playLcarsBeep(1200, 1500);
      loadPulsecastCatalog(target);
    }
  }

  async function loadPulsecastCatalog(page = 1) {
    pulsecastCatalogPage = page;
    const grid = document.getElementById('pulsecastCatalogGrid');
    const loading = document.getElementById('pulsecastCatalogLoading');
    const emptyEl = document.getElementById('pulsecastCatalogEmpty');

    if (loading) loading.style.display = 'block';
    if (grid) grid.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';

    try {
      const params = new URLSearchParams({
        category: pulsecastCatalogCategory,
        subcategory: pulsecastCatalogSubcategory,
        search: pulsecastCatalogSearchQuery,
        page: String(page),
        limit: '40'
      });

      const resp = await fetch(`/api/pulsecast/media-library?${params.toString()}`, {
        headers: getPulsecastHeaders()
      });

      if (loading) loading.style.display = 'none';

      if (resp.status === 403) {
        initPulsecastSection();
        return;
      }

      if (!resp.ok) {
        if (resp.status === 503) {
          pulsecastIsOnline = false;
          initPulsecastSection();
        }
        return;
      }

      const data = await resp.json();
      const items = data.items || [];
      pulsecastCatalogTotalPages = data.totalPages || 1;

      // Update Subcategories dropdown dynamically
      if (Array.isArray(data.availableSubcategories)) {
        updatePulsecastSubcatDropdown(data.availableSubcategories);
      }

      // Update Counts
      if (data.counts) {
        const cFilme = document.getElementById('pulsecastCountFilme');
        const cSerien = document.getElementById('pulsecastCountSerien');
        const cLocal = document.getElementById('pulsecastLocalCountBadge');
        if (cFilme && data.counts.Filme !== undefined) cFilme.textContent = `(${data.counts.Filme})`;
        if (cSerien && data.counts.Serien !== undefined) cSerien.textContent = `(${data.counts.Serien})`;
        if (cLocal && data.counts.Lokal !== undefined) cLocal.textContent = String(data.counts.Lokal);
      }

      // Update pagination UI
      updatePulsecastPaginationUI(data.currentPage || page, pulsecastCatalogTotalPages, data.totalItems || 0);

      if (items.length === 0) {
        if (grid) grid.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'block';
      } else {
        if (grid) {
          grid.style.display = 'grid';
          renderPulsecastCatalogGrid(items);
        }
      }
    } catch (e) {
      if (loading) loading.style.display = 'none';
      console.warn('Fehler beim Laden des Katalogs:', e);
    }
  }

  function updatePulsecastSubcatDropdown(subcats) {
    const sel = document.getElementById('pulsecastSubcatSelect');
    if (!sel) return;
    const currentVal = pulsecastCatalogSubcategory;
    let html = '<option value="all">ALLE KATEGORIEN</option>';
    subcats.forEach(sub => {
      if (sub === 'all') return;
      const isSel = (sub === currentVal) ? 'selected' : '';
      html += `<option value="${escapeHtml(sub)}" ${isSel}>${escapeHtml(sub)}</option>`;
    });
    sel.innerHTML = html;
  }

  function updatePulsecastPaginationUI(current, total, totalItems) {
    const indicator = document.getElementById('pulsecastPageIndicator');
    const prevBtn = document.getElementById('pulsecastPrevPageBtn');
    const nextBtn = document.getElementById('pulsecastNextPageBtn');

    if (indicator) {
      indicator.textContent = `SEITE ${current} VON ${total} (${totalItems} EINTRÄGE)`;
    }
    if (prevBtn) prevBtn.disabled = (current <= 1);
    if (nextBtn) nextBtn.disabled = (current >= total);
  }

  function renderPulsecastCatalogGrid(items) {
    const grid = document.getElementById('pulsecastCatalogGrid');
    if (!grid) return;

    let html = '';
    items.forEach(item => {
      const isSeries = !!item.isGroup || item.type === 'series' || item.category === 'Serien' || item.metadata?.type === 'series';
      const title = item.title || item.metadata?.title || item.filename || 'Ohne Titel';
      const year = item.year || item.metadata?.year || '';
      const poster = item.posterUrl || item.coverUrl || item.metadata?.posterUrl || item.metadata?.coverUrl || '';
      const subcat = item.subcategory || item.metadata?.subcategory || item.category || '';
      const seriesId = item.xtreamSeriesId || item.id || '';
      const isLocal = (item.isXtream === false || item.category === 'Lokal' || item.metadata?.category === 'Lokal') && !!item.filename;

      const safeTitle = escapeHtml(title);
      const safePoster = poster ? escapeHtml(poster) : '';
      const safeSubcat = escapeHtml(subcat);

      html += `
        <div class="pulsecast-card">
          <div style="position:relative; width:100%; aspect-ratio:2/3; background:#111; overflow:hidden;">
            ${poster ? `<img src="${safePoster}" alt="${safeTitle}" class="pulsecast-card-poster" loading="lazy" onerror="this.onerror=null; this.style.display='none'; this.nextElementSibling.style.display='flex';">` : ''}
            <div style="position:absolute; inset:0; display:${poster ? 'none' : 'flex'}; align-items:center; justify-content:center; flex-direction:column; background:rgba(0,0,0,0.6); color:#777; font-size:2.5rem;">
              ${isSeries ? '📺' : '🎬'}
              <span style="font-size:0.75rem; font-family:var(--font-family); color:var(--c-gold); margin-top:0.4rem; padding:0 0.5rem; text-align:center;">${isSeries ? 'SERIE' : 'FILM'}</span>
            </div>
            ${year ? `<span style="position:absolute; top:6px; right:6px; background:rgba(0,0,0,0.75); border:1px solid rgba(255,255,255,0.2); color:var(--c-gold); font-family:var(--mono-family); font-size:0.75rem; padding:2px 6px; border-radius:4px;">${escapeHtml(String(year))}</span>` : ''}
          </div>
          <div class="pulsecast-card-body">
            <div>
              <div style="font-family:var(--font-family); font-size:0.9rem; font-weight:700; color:#fff; line-height:1.25; margin-bottom:0.3rem; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;" title="${safeTitle}">
                ${safeTitle}
              </div>
              ${subcat ? `<div style="font-size:0.72rem; color:var(--c-butterscotch); font-family:var(--mono-family); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-bottom:0.6rem;" title="${safeSubcat}">${safeSubcat}</div>` : ''}
            </div>

            <div style="margin-top:auto;">
              ${isSeries ? `
                <button type="button" class="left-action-btn" onclick="openPulsecastSeriesEpisodes('${escapeHtml(String(seriesId))}', '${safeTitle.replace(/'/g, "\\'")}', '${safePoster}')" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                  <span>📋</span> <span>EPISODEN</span>
                </button>
              ` : (isLocal ? `
                <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(item.filename)}', '${safeTitle.replace(/'/g, "\\'")}')" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;">
                  <span>▶</span> <span>IN PLAYER ÖFFNEN</span>
                </button>
              ` : `
                <button type="button" class="left-action-btn" onclick="triggerPulsecastMovieDownload('${escapeHtml(String(item.streamUrl || item.filename))}', '${safeTitle.replace(/'/g, "\\'")}')" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
                  <span>⬇️</span> <span>DOWNLOAD</span>
                </button>
              `)}
            </div>
          </div>
        </div>
      `;
    });

    grid.innerHTML = html;
  }

  async function triggerPulsecastMovieDownload(url, title) {
    if (!url || !title) return;
    playLcarsBeep(1200, 1600);
    try {
      const resp = await fetch('/api/pulsecast/download/media', {
        method: 'POST',
        headers: getPulsecastHeaders(),
        body: JSON.stringify({ url: url, title: title })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsAcknowledge();
        alert(`Download gestartet: ${title}`);
        switchPulsecastSubtab('downloads');
      } else {
        alert(`Download-Fehler: ${data.error || 'Unbekannter Fehler'}`);
      }
    } catch (e) {
      alert(`Download-Fehler: ${e}`);
    }
  }

  // ==========================================================================
  // LCARS PULSECAST: LOKALE MEDIEN (CONTROLLER & RENDERER)
  // ==========================================================================
  let pulsecastLocalCategory = 'Lokal';
  let pulsecastLocalViewMode = 'grid';
  let pulsecastLocalSearchQuery = '';
  let pulsecastLocalPage = 1;
  let pulsecastLocalTotalPages = 1;
  let pulsecastLocalItemsCache = [];

  async function loadPulsecastLocalCounts() {
    try {
      const resp = await fetch('/api/pulsecast/media-library?category=Lokal&limit=1', {
        headers: getPulsecastHeaders()
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data.counts) {
          updatePulsecastLocalCounts(data.counts);
        }
      }
    } catch (_) {}
  }

  function setPulsecastLocalCategory(cat) {
    playLcarsBeep(1000, 1300);
    pulsecastLocalCategory = cat;

    const pills = [
      { id: 'local-pill-all', active: cat === 'Lokal', bg: 'var(--c-butterscotch)', color: '#000' },
      { id: 'local-pill-Filme', active: cat === 'Lokal_Filme', bg: 'var(--c-primary)', color: '#000' },
      { id: 'local-pill-Serien', active: cat === 'Lokal_Serien', bg: 'var(--c-secondary)', color: '#000' }
    ];
    pills.forEach(p => {
      const el = document.getElementById(p.id);
      if (el) {
        if (p.active) {
          el.classList.add('active');
          el.style.background = p.bg;
          el.style.color = p.color;
          el.style.fontWeight = '700';
        } else {
          el.classList.remove('active');
          el.style.background = 'rgba(0,0,0,0.5)';
          el.style.color = (p.id === 'local-pill-Filme') ? 'var(--c-primary)' : (p.id === 'local-pill-Serien' ? 'var(--c-secondary)' : '#aaa');
          el.style.fontWeight = 'normal';
        }
      }
    });

    pulsecastLocalPage = 1;
    loadPulsecastLocal(1);
  }

  function setPulsecastLocalViewMode(mode) {
    playLcarsBeep(1100, 1400);
    pulsecastLocalViewMode = mode;

    const gridBtn = document.getElementById('pulsecastLocalViewGridBtn');
    const listBtn = document.getElementById('pulsecastLocalViewListBtn');
    const gridContainer = document.getElementById('pulsecastLocalGrid');
    const listContainer = document.getElementById('pulsecastLocalListContainer');

    if (mode === 'grid') {
      if (gridBtn) {
        gridBtn.classList.add('active');
        gridBtn.style.background = 'var(--c-gold)';
        gridBtn.style.color = '#000';
        gridBtn.style.border = 'none';
      }
      if (listBtn) {
        listBtn.classList.remove('active');
        listBtn.style.background = 'rgba(0,0,0,0.5)';
        listBtn.style.color = '#aaa';
        listBtn.style.border = '1px solid rgba(255,255,255,0.2)';
      }
      if (gridContainer) gridContainer.style.display = (pulsecastLocalItemsCache.length > 0) ? 'grid' : 'none';
      if (listContainer) listContainer.style.display = 'none';
      renderPulsecastLocalGrid(pulsecastLocalItemsCache);
    } else {
      if (listBtn) {
        listBtn.classList.add('active');
        listBtn.style.background = 'var(--c-gold)';
        listBtn.style.color = '#000';
        listBtn.style.border = 'none';
      }
      if (gridBtn) {
        gridBtn.classList.remove('active');
        gridBtn.style.background = 'rgba(0,0,0,0.5)';
        gridBtn.style.color = '#aaa';
        gridBtn.style.border = '1px solid rgba(255,255,255,0.2)';
      }
      if (gridContainer) gridContainer.style.display = 'none';
      if (listContainer) listContainer.style.display = (pulsecastLocalItemsCache.length > 0) ? 'block' : 'none';
      renderPulsecastLocalList(pulsecastLocalItemsCache);
    }
  }

  function pulsecastLocalSearchTrigger() {
    const inp = document.getElementById('pulsecastLocalSearchInput');
    pulsecastLocalSearchQuery = inp ? inp.value.trim() : '';
    pulsecastLocalPage = 1;
    loadPulsecastLocal(1);
  }

  function pulsecastLocalSearchClear() {
    const inp = document.getElementById('pulsecastLocalSearchInput');
    if (inp) inp.value = '';
    pulsecastLocalSearchQuery = '';
    pulsecastLocalPage = 1;
    loadPulsecastLocal(1);
  }

  function pulsecastLocalChangePage(delta) {
    const target = pulsecastLocalPage + delta;
    if (target >= 1 && target <= pulsecastLocalTotalPages) {
      playLcarsBeep(1200, 1500);
      loadPulsecastLocal(target);
    }
  }

  async function loadPulsecastLocal(page = 1) {
    pulsecastLocalPage = page;
    const grid = document.getElementById('pulsecastLocalGrid');
    const listContainer = document.getElementById('pulsecastLocalListContainer');
    const loading = document.getElementById('pulsecastLocalLoading');
    const emptyEl = document.getElementById('pulsecastLocalEmpty');

    if (loading) loading.style.display = 'block';
    if (grid) grid.style.display = 'none';
    if (listContainer) listContainer.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';

    try {
      const params = new URLSearchParams({
        category: pulsecastLocalCategory,
        search: pulsecastLocalSearchQuery,
        page: String(page),
        limit: '40'
      });

      const resp = await fetch(`/api/pulsecast/media-library?${params.toString()}`, {
        headers: getPulsecastHeaders()
      });

      if (loading) loading.style.display = 'none';

      if (resp.status === 403) {
        initPulsecastSection();
        return;
      }

      if (!resp.ok) {
        if (resp.status === 503) {
          pulsecastIsOnline = false;
          initPulsecastSection();
        }
        return;
      }

      const data = await resp.json();
      const items = data.items || [];
      pulsecastLocalItemsCache = items;
      pulsecastLocalTotalPages = data.totalPages || 1;

      // Update Counts
      if (data.counts) {
        updatePulsecastLocalCounts(data.counts);
      }

      // Update pagination UI
      updatePulsecastLocalPaginationUI(data.currentPage || page, pulsecastLocalTotalPages, data.totalItems || 0);

      if (items.length === 0) {
        if (grid) grid.style.display = 'none';
        if (listContainer) listContainer.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'block';
      } else {
        if (pulsecastLocalViewMode === 'grid') {
          if (grid) {
            grid.style.display = 'grid';
            renderPulsecastLocalGrid(items);
          }
        } else {
          if (listContainer) {
            listContainer.style.display = 'block';
            renderPulsecastLocalList(items);
          }
        }
      }
    } catch (e) {
      if (loading) loading.style.display = 'none';
      console.warn('Fehler beim Laden der lokalen Medien:', e);
    }
  }

  function updatePulsecastLocalCounts(counts) {
    if (!counts) return;
    const badge = document.getElementById('pulsecastLocalCountBadge');
    const cAll = document.getElementById('pulsecastLocalCountAll');
    const cFilme = document.getElementById('pulsecastLocalCountFilme');
    const cSerien = document.getElementById('pulsecastLocalCountSerien');

    if (badge && counts.Lokal !== undefined) badge.textContent = String(counts.Lokal);
    if (cAll && counts.Lokal !== undefined) cAll.textContent = `(${counts.Lokal})`;
    if (cFilme && counts.Lokal_Filme !== undefined) cFilme.textContent = `(${counts.Lokal_Filme})`;
    if (cSerien && counts.Lokal_Serien !== undefined) cSerien.textContent = `(${counts.Lokal_Serien})`;
  }

  function updatePulsecastLocalPaginationUI(current, total, totalItems) {
    const indicator = document.getElementById('pulsecastLocalPageIndicator');
    const prevBtn = document.getElementById('pulsecastLocalPrevPageBtn');
    const nextBtn = document.getElementById('pulsecastLocalNextPageBtn');

    if (indicator) {
      indicator.textContent = `SEITE ${current} VON ${total} (${totalItems} EINTRÄGE)`;
    }
    if (prevBtn) prevBtn.disabled = (current <= 1);
    if (nextBtn) nextBtn.disabled = (current >= total);
  }

  function renderPulsecastLocalGrid(items) {
    const grid = document.getElementById('pulsecastLocalGrid');
    if (!grid) return;

    let html = '';
    items.forEach((item, idx) => {
      const isSeries = !!item.isGroup || item.category === 'Lokal_Serien' || item.subcategory === 'Serien' || item.metadata?.isSeries;
      const title = item.title || item.metadata?.title || item.filename || 'Ohne Titel';
      const year = item.year || item.metadata?.year || '';
      const poster = item.posterUrl || item.coverUrl || item.metadata?.posterUrl || item.metadata?.coverUrl || '';
      const safeTitle = escapeHtml(title);
      const safePoster = poster ? escapeHtml(poster) : '';

      let sizeInfo = '';
      if (item.sizeBytes) {
        sizeInfo = formatBytes(item.sizeBytes);
      } else if (item.files && item.files.length) {
        const totalSize = item.files.reduce((acc, f) => acc + (f.sizeBytes || 0), 0);
        sizeInfo = `${item.files.length} Folgen (${formatBytes(totalSize)})`;
      }

      html += `
        <div class="pulsecast-card" style="border-color:rgba(235,148,58,0.45);">
          <div style="position:relative; width:100%; aspect-ratio:2/3; background:#111; overflow:hidden;">
            ${poster ? `<img src="${safePoster}" alt="${safeTitle}" class="pulsecast-card-poster" loading="lazy" onerror="this.onerror=null; this.style.display='none'; this.nextElementSibling.style.display='flex';">` : ''}
            <div style="position:absolute; inset:0; display:${poster ? 'none' : 'flex'}; align-items:center; justify-content:center; flex-direction:column; background:rgba(0,0,0,0.6); color:#777; font-size:2.5rem;">
              ${isSeries ? '📺' : '🎬'}
              <span style="font-size:0.75rem; font-family:var(--font-family); color:var(--c-butterscotch); margin-top:0.4rem; padding:0 0.5rem; text-align:center;">${isSeries ? 'LOKALE SERIE' : 'LOKALER FILM'}</span>
            </div>
            ${year ? `<span style="position:absolute; top:6px; right:6px; background:rgba(0,0,0,0.75); border:1px solid rgba(255,255,255,0.2); color:var(--c-gold); font-family:var(--mono-family); font-size:0.75rem; padding:2px 6px; border-radius:4px;">${escapeHtml(String(year))}</span>` : ''}
          </div>
          <div class="pulsecast-card-body">
            <div>
              <div style="font-family:var(--font-family); font-size:0.9rem; font-weight:700; color:#fff; line-height:1.25; margin-bottom:0.3rem; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;" title="${safeTitle}">
                ${safeTitle}
              </div>
              <div style="font-size:0.72rem; color:var(--c-butterscotch); font-family:var(--mono-family); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-bottom:0.6rem;">
                ${sizeInfo ? `📁 ${escapeHtml(sizeInfo)}` : '📁 LOKALE DATEI'}
              </div>
            </div>

            <div style="margin-top:auto;">
              ${(isSeries && item.files && item.files.length) ? `
                <button type="button" class="left-action-btn" onclick="openPulsecastLocalGroupModal(${idx})" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                  <span>📋</span> <span>EPISODEN (${item.files.length})</span>
                </button>
              ` : `
                <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(item.filename)}', '${safeTitle.replace(/'/g, "\\'")}')" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;">
                  <span>▶</span> <span>IN PLAYER ÖFFNEN</span>
                </button>
              `}
            </div>
          </div>
        </div>
      `;
    });

    grid.innerHTML = html;
  }

  function renderPulsecastLocalList(items) {
    const tbody = document.getElementById('pulsecastLocalTableBody');
    if (!tbody) return;

    let html = '';
    items.forEach((item, idx) => {
      const isSeries = !!item.isGroup || item.category === 'Lokal_Serien' || item.subcategory === 'Serien' || item.metadata?.isSeries;
      const title = item.title || item.metadata?.title || item.filename || 'Ohne Titel';
      const filename = item.filename || '';
      const safeTitle = escapeHtml(title);
      const safeFilename = escapeHtml(filename);

      // Dateiendung / Format ermitteln
      let format = '--';
      if (filename) {
        const extIdx = filename.lastIndexOf('.');
        if (extIdx !== -1) format = filename.slice(extIdx + 1).toUpperCase();
      } else if (isSeries) {
        format = 'SERIE';
      }

      // Größe
      let sizeStr = '--';
      if (item.sizeBytes) {
        sizeStr = formatBytes(item.sizeBytes);
      } else if (item.files && item.files.length) {
        const totalBytes = item.files.reduce((acc, f) => acc + (f.sizeBytes || 0), 0);
        sizeStr = `${formatBytes(totalBytes)} (${item.files.length} F.)`;
      }

      // Datum
      let mtimeStr = '--';
      const mtime = item.mtime || (item.files && item.files[0] ? item.files[0].mtime : 0);
      if (mtime) {
        try {
          const d = new Date(mtime);
          mtimeStr = d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) + ' ' +
                     d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
        } catch (_) {}
      }

      const typeIcon = isSeries ? '📺' : '🎬';
      const typeColor = isSeries ? 'var(--c-secondary)' : 'var(--c-primary)';

      html += `
        <tr>
          <td style="text-align:center; font-size:1.1rem;">${typeIcon}</td>
          <td>
            <div style="font-family:var(--font-family); font-weight:700; color:#fff; font-size:0.88rem; line-height:1.2;">
              ${safeTitle}
            </div>
            ${filename ? `<div style="font-family:var(--mono-family); font-size:0.75rem; color:#888; word-break:break-all; margin-top:2px;">${safeFilename}</div>` : ''}
          </td>
          <td style="font-family:var(--mono-family); font-size:0.8rem; color:${typeColor}; font-weight:700;">
            ${escapeHtml(format)}
          </td>
          <td style="font-family:var(--mono-family); font-size:0.82rem; color:#44dd88; white-space:nowrap;">
            ${escapeHtml(sizeStr)}
          </td>
          <td style="font-family:var(--mono-family); font-size:0.78rem; color:var(--c-gold); white-space:nowrap;">
            ${escapeHtml(mtimeStr)}
          </td>
          <td style="text-align:right; white-space:nowrap;">
            ${(isSeries && item.files && item.files.length) ? `
              <button type="button" class="left-action-btn" onclick="openPulsecastLocalGroupModal(${idx})" style="padding:0.3rem 0.7rem; font-size:0.78rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                <span>📋</span> <span>EPISODEN</span>
              </button>
            ` : `
              <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(item.filename)}', '${safeTitle.replace(/'/g, "\\'")}')" style="padding:0.3rem 0.7rem; font-size:0.78rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;">
                <span>▶</span> <span>ÖFFNEN</span>
              </button>
            `}
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = html;
  }

  function openPulsecastLocalGroupModal(idx) {
    playLcarsBeep(980, 1300);
    const item = pulsecastLocalItemsCache[idx];
    if (!item || !item.files) return;

    pulsecastActiveSeries = { id: item.id || '', title: item.title, poster: item.posterUrl };
    const modal = document.getElementById('pulsecastSeriesModal');
    const modalTitle = document.getElementById('pulsecastModalSeriesTitle');
    const modalMeta = document.getElementById('pulsecastModalSeriesMeta');
    const loadingEl = document.getElementById('pulsecastEpisodesLoading');

    if (modalTitle) modalTitle.textContent = item.title;
    if (loadingEl) loadingEl.style.display = 'none';
    if (modalMeta) modalMeta.textContent = `LOKALES ARCHIV // ${item.files.length} EPISODEN VORHANDEN`;

    pulsecastCurrentSeriesEpisodes = item.files;
    populateSeasonFilter(item.files);
    renderEpisodesList(item.files);

    if (modal) modal.style.display = 'flex';
  }

  // SERIEN-EPISODEN MODAL
  async function openPulsecastSeriesEpisodes(seriesId, title, poster) {
    playLcarsBeep(980, 1300);
    pulsecastActiveSeries = { id: seriesId, title: title, poster: poster };
    const modal = document.getElementById('pulsecastSeriesModal');
    const modalTitle = document.getElementById('pulsecastModalSeriesTitle');
    const modalMeta = document.getElementById('pulsecastModalSeriesMeta');
    const listEl = document.getElementById('pulsecastEpisodesList');
    const loadingEl = document.getElementById('pulsecastEpisodesLoading');

    if (modalTitle) modalTitle.textContent = title;
    if (modalMeta) modalMeta.textContent = `SERIEN-ID: ${seriesId} // LADE EPISODEN...`;
    if (listEl) listEl.innerHTML = '';
    if (loadingEl) loadingEl.style.display = 'block';
    if (modal) modal.style.display = 'flex';

    try {
      const resp = await fetch(`/api/pulsecast/series-episodes?seriesId=${encodeURIComponent(seriesId)}`, {
        headers: getPulsecastHeaders()
      });
      if (loadingEl) loadingEl.style.display = 'none';

      if (!resp.ok) {
        const err = await resp.json();
        if (listEl) listEl.innerHTML = `<div style="color:var(--c-red); font-family:var(--mono-family); padding:1rem; text-align:center;">${escapeHtml(err.error || 'Episoden konnten nicht geladen werden')}</div>`;
        return;
      }

      const episodes = await resp.json();
      pulsecastCurrentSeriesEpisodes = Array.isArray(episodes) ? episodes : [];
      if (modalMeta) modalMeta.textContent = `${pulsecastCurrentSeriesEpisodes.length} EPISODEN VERFÜGBAR`;

      populateSeasonFilter(pulsecastCurrentSeriesEpisodes);
      renderEpisodesList(pulsecastCurrentSeriesEpisodes);
    } catch (e) {
      if (loadingEl) loadingEl.style.display = 'none';
      if (listEl) listEl.innerHTML = `<div style="color:var(--c-red); font-family:var(--mono-family); padding:1rem; text-align:center;">Fehler beim Laden: ${escapeHtml(String(e))}</div>`;
    }
  }

  function closePulsecastSeriesModal() {
    playLcarsBeep(500, 300);
    const modal = document.getElementById('pulsecastSeriesModal');
    if (modal) modal.style.display = 'none';
    pulsecastCurrentSeriesEpisodes = [];
    pulsecastActiveSeries = null;
  }

  function handlePulsecastSeriesModalBackdropClick(e) {
    if (e.target && e.target.id === 'pulsecastSeriesModal') {
      closePulsecastSeriesModal();
    }
  }

  function populateSeasonFilter(episodes) {
    const sel = document.getElementById('pulsecastSeasonFilterSelect');
    if (!sel) return;
    const seasons = new Set();
    episodes.forEach(ep => {
      const se = ep.metadata?.seasonEpisode || '';
      const m = se.match(/^S(\\d+)/i);
      if (m) seasons.add(parseInt(m[1], 10));
    });

    let html = '<option value="all">ALLE STAFFELN</option>';
    Array.from(seasons).sort((a, b) => a - b).forEach(s => {
      html += `<option value="S${s}">STAFFEL ${s}</option>`;
    });
    sel.innerHTML = html;
  }

  function filterPulsecastEpisodesBySeason() {
    const sel = document.getElementById('pulsecastSeasonFilterSelect');
    const season = sel ? sel.value : 'all';
    let filtered = pulsecastCurrentSeriesEpisodes;
    if (season !== 'all') {
      const seasonNum = parseInt(String(season).replace(/^S/i, ''), 10);
      filtered = pulsecastCurrentSeriesEpisodes.filter(ep => {
        const se = ep.metadata?.seasonEpisode || '';
        const m = se.match(/^S(\\d+)/i);
        return m ? parseInt(m[1], 10) === seasonNum : false;
      });
    }
    renderEpisodesList(filtered);
  }

  function renderEpisodesList(episodes) {
    const listEl = document.getElementById('pulsecastEpisodesList');
    if (!listEl) return;

    if (episodes.length === 0) {
      listEl.innerHTML = '<div style="text-align:center; padding:1.5rem; color:#888; font-family:var(--mono-family);">KEINE EPISODEN GEFUNDEN</div>';
      return;
    }

    let html = '';
    episodes.forEach((ep, idx) => {
      const title = ep.metadata?.title || ep.filename || `Episode ${idx + 1}`;
      const seasonEpisode = ep.metadata?.seasonEpisode || '';
      const duration = ep.metadata?.cast?.duration || '';
      const rawRating = parseFloat(ep.metadata?.cast?.rating);
      const rating = (!isNaN(rawRating) && rawRating > 0) ? `★ ${rawRating.toFixed(1)}` : '';
      const streamUrl = ep.filename || '';
      const safeTitle = escapeHtml(title);
      const safeUrl = escapeHtml(streamUrl);

      const isLocalEp = (ep.isXtream === false || (ep.filename && !ep.filename.startsWith('http://') && !ep.filename.startsWith('https://')));

      html += `
        <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.08); border-radius:4px; padding:0.6rem 0.8rem; gap:0.5rem; flex-wrap:wrap;">
          <div style="display:flex; align-items:center; gap:0.6rem; min-width:0; flex:1;">
            ${seasonEpisode ? `<span class="badge-status" style="background:var(--c-secondary); color:#000; font-size:0.75rem; flex-shrink:0;">${escapeHtml(seasonEpisode)}</span>` : ''}
            <div style="min-width:0;">
              <div style="font-family:var(--font-family); font-weight:700; font-size:0.9rem; color:#fff; word-break:break-all;">
                ${safeTitle}
              </div>
              <div style="font-family:var(--mono-family); font-size:0.75rem; color:#888; display:flex; gap:0.8rem;">
                ${duration ? `<span>⏱ ${escapeHtml(duration)}</span>` : ''}
                ${rating ? `<span style="color:var(--c-gold);">${rating}</span>` : ''}
              </div>
            </div>
          </div>
          <div style="display:flex; gap:0.4rem; align-items:center;">
            ${isLocalEp ? `
              <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(ep.filename)}', '${safeTitle.replace(/'/g, "\\'")}')" style="padding:0.3rem 0.6rem; font-size:0.78rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;" title="Im lokalen Media Player öffnen">
                ▶ PLAYER
              </button>
            ` : ''}
            <button type="button" class="left-action-btn" onclick="downloadSingleEpisode('${safeUrl}', '${safeTitle.replace(/'/g, "\\'")}', '${seasonEpisode}')" style="padding:0.3rem 0.8rem; font-size:0.78rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
              <span>⬇️</span> <span>DOWNLOAD</span>
            </button>
          </div>
        </div>
      `;
    });

    listEl.innerHTML = html;
  }

  async function downloadSingleEpisode(url, title, seasonEpisode) {
    if (!url) return;
    playLcarsBeep(1200, 1600);
    const seriesTitle = pulsecastActiveSeries ? pulsecastActiveSeries.title : '';
    const fullTitle = seasonEpisode ? `${seasonEpisode} - ${title}` : title;
    try {
      const resp = await fetch('/api/pulsecast/download/media', {
        method: 'POST',
        headers: getPulsecastHeaders(),
        body: JSON.stringify({ url: url, title: fullTitle, seriesTitle: seriesTitle })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsAcknowledge();
        alert(`Download gestartet: ${fullTitle}`);
      } else {
        alert(`Fehler: ${data.error || 'Download fehlgeschlagen'}`);
      }
    } catch (e) {
      alert(`Fehler beim Starten: ${e}`);
    }
  }

  async function downloadAllVisibleEpisodes() {
    const sel = document.getElementById('pulsecastSeasonFilterSelect');
    const season = sel ? sel.value : 'all';
    let episodes = pulsecastCurrentSeriesEpisodes;
    if (season !== 'all') {
      const seasonNum = parseInt(String(season).replace(/^S/i, ''), 10);
      episodes = pulsecastCurrentSeriesEpisodes.filter(ep => {
        const se = ep.metadata?.seasonEpisode || '';
        const m = se.match(/^S(\\d+)/i);
        return m ? parseInt(m[1], 10) === seasonNum : false;
      });
    }

    if (!episodes || episodes.length === 0) {
      alert('Keine Episoden zum Herunterladen vorhanden.');
      return;
    }

    if (!confirm(`${episodes.length} Episoden zur Download-Warteschlange hinzufügen?`)) {
      return;
    }

    playLcarsBeep(1300, 1800);
    const seriesTitle = pulsecastActiveSeries ? pulsecastActiveSeries.title : '';
    const items = episodes.map(ep => {
      const title = ep.metadata?.title || ep.filename;
      const se = ep.metadata?.seasonEpisode || '';
      return {
        url: ep.filename,
        title: se ? `${se} - ${title}` : title,
        seriesTitle: seriesTitle
      };
    });

    try {
      const resp = await fetch('/api/pulsecast/download/media', {
        method: 'POST',
        headers: getPulsecastHeaders(),
        body: JSON.stringify({ items: items })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsAcknowledge();
        alert(`${data.count || items.length} Episoden erfolgreich in die Download-Warteschlange eingereiht!`);
        closePulsecastSeriesModal();
        switchPulsecastSubtab('downloads');
      } else {
        alert(`Batch-Download Fehler: ${data.error || 'Fehlgeschlagen'}`);
      }
    } catch (e) {
      alert(`Batch-Download Fehler: ${e}`);
    }
  }

  // XDCC SUCHE CONTROLLER
  function pulsecastXdccChipSearch(q) {
    const inp = document.getElementById('pulsecastXdccInput');
    if (inp) inp.value = q;
    pulsecastXdccSearch();
  }

  function setPulsecastXdccSource(source) {
    playLcarsBeep(1000, 1300);
    pulsecastXdccSource = source;
    const btnXdcc = document.getElementById('pulsecast-source-pill-xdcc');
    const btnMg = document.getElementById('pulsecast-source-pill-moviegods');
    const topDlBox = document.getElementById('pulsecastMoviegodsTopDlContainer');
    const headerTitle = document.getElementById('pulsecastXdccHeaderTitle');

    if (source === 'moviegods') {
      if (btnXdcc) {
        btnXdcc.classList.remove('active');
        btnXdcc.style.background = 'rgba(0,0,0,0.5)';
        btnXdcc.style.color = 'var(--c-blue)';
        btnXdcc.style.border = '1px solid var(--c-blue)';
      }
      if (btnMg) {
        btnMg.classList.add('active');
        btnMg.style.background = 'var(--c-secondary)';
        btnMg.style.color = '#000';
        btnMg.style.border = 'none';
      }
      if (topDlBox) topDlBox.style.display = 'block';
      if (headerTitle) {
        headerTitle.textContent = 'MOVIE GODS IRC SCANNER & PACKS';
        headerTitle.style.color = 'var(--c-secondary)';
      }
    } else {
      if (btnXdcc) {
        btnXdcc.classList.add('active');
        btnXdcc.style.background = 'var(--c-blue)';
        btnXdcc.style.color = '#000';
        btnXdcc.style.border = 'none';
      }
      if (btnMg) {
        btnMg.classList.remove('active');
        btnMg.style.background = 'rgba(0,0,0,0.5)';
        btnMg.style.color = 'var(--c-secondary)';
        btnMg.style.border = '1px solid var(--c-secondary)';
      }
      if (topDlBox) topDlBox.style.display = 'none';
      if (headerTitle) {
        headerTitle.textContent = 'KLASSISCHE IRC & XDCC PAKET-SUCHE';
        headerTitle.style.color = 'var(--c-blue)';
      }
    }
  }

  async function loadPulsecastMoviegodsTopDl() {
    playLcarsBeep(1200, 1600);
    const statusEl = document.getElementById('pulsecastTopDlStatus');
    const tableContainer = document.getElementById('pulsecastTopDlTableContainer');
    const tbody = document.getElementById('pulsecastTopDlTbody');
    const btn = document.getElementById('pulsecastLoadTopDlBtn');

    if (btn) btn.disabled = true;
    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.textContent = 'FRAGE MOVIEGODS TOP-DOWNLOADS AB (!topdl auf #mg-chat)...';
      statusEl.style.color = 'var(--c-gold)';
    }
    if (tableContainer) tableContainer.style.display = 'none';

    try {
      const resp = await fetch('/api/pulsecast/search?q=!topdl&source=moviegods', {
        headers: getPulsecastHeaders()
      });

      if (btn) btn.disabled = false;

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        if (statusEl) {
          statusEl.textContent = `FEHLER BEIM LADEN DER TOP-DOWNLOADS: ${err.error || 'Serverfehler'}`;
          statusEl.style.color = 'var(--c-red)';
        }
        return;
      }

      const data = await resp.json();
      const results = data.results || (Array.isArray(data) ? data : []);

      if (results.length === 0) {
        if (statusEl) {
          statusEl.textContent = 'KEINE TOP-DOWNLOADS EMPFANGEN (IRC TIMEOUT ODER KEINE DATEN)';
          statusEl.style.color = 'var(--c-gold)';
        }
        return;
      }

      if (statusEl) {
        statusEl.textContent = `${results.length} TOP-DOWNLOADS EMPFANGEN // KLICK AUF EINTRAG STARTET PACK-SCAN:`;
        statusEl.style.color = '#44dd88';
      }

      let html = '';
      results.forEach(item => {
        const gets = item.gets || '';
        const filename = item.filename || '';
        const sizeStr = item.sizeStr || '';
        const safeFilename = escapeHtml(filename);

        html += `
          <tr style="cursor:pointer;" onclick="selectMoviegodsTopDl('${escapeJsString(filename)}')" title="Diesen Release sofort suchen">
            <td style="font-family:var(--mono-family); font-weight:700; color:var(--c-gold);">${escapeHtml(gets)}</td>
            <td style="font-family:var(--mono-family); font-size:0.82rem; word-break:break-all; color:#fff;">
              ${safeFilename}
            </td>
            <td style="font-family:var(--mono-family); color:#44dd88; white-space:nowrap;">${escapeHtml(sizeStr)}</td>
            <td style="text-align:right;">
              <button type="button" class="left-action-btn" onclick="event.stopPropagation(); selectMoviegodsTopDl('${escapeJsString(filename)}')" style="padding:0.25rem 0.6rem; font-size:0.75rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                <span>🔍</span> <span>SUCHEN</span>
              </button>
            </td>
          </tr>
        `;
      });

      if (tbody) tbody.innerHTML = html;
      if (tableContainer) tableContainer.style.display = 'block';

    } catch (e) {
      if (btn) btn.disabled = false;
      if (statusEl) {
        statusEl.textContent = `VERBINDUNGSFEHLER: ${e}`;
        statusEl.style.color = 'var(--c-red)';
      }
    }
  }

  function selectMoviegodsTopDl(filename) {
    if (!filename) return;
    playLcarsBeep(1400, 1800);
    const inp = document.getElementById('pulsecastXdccInput');
    if (inp) inp.value = filename;
    setPulsecastXdccSource('moviegods');
    pulsecastXdccSearch();
  }

  async function pulsecastXdccSearch() {
    const inp = document.getElementById('pulsecastXdccInput');
    const query = inp ? inp.value.trim() : '';
    if (!query) {
      alert('Bitte geben Sie einen Suchbegriff ein.');
      return;
    }

    playLcarsBeep(1200, 1500);
    const statusEl = document.getElementById('pulsecastXdccStatus');
    const tableEl = document.getElementById('pulsecastXdccTable');
    const emptyEl = document.getElementById('pulsecastXdccEmpty');
    const tbody = document.getElementById('pulsecastXdccTbody');

    const isMg = (pulsecastXdccSource === 'moviegods');
    const sourceLabel = isMg ? 'MOVIE GODS IRC (#mg-chat)' : 'XDCC-NETZWERKE';

    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.textContent = `SCANNE ${sourceLabel} NACH "${query.toUpperCase()}"...`;
      statusEl.style.color = isMg ? 'var(--c-secondary)' : 'var(--c-blue)';
    }
    if (tableEl) tableEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';

    try {
      const resp = await fetch(`/api/pulsecast/search?q=${encodeURIComponent(query)}&source=${encodeURIComponent(pulsecastXdccSource)}`, {
        headers: getPulsecastHeaders()
      });

      if (resp.status === 403) {
        initPulsecastSection();
        return;
      }

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        if (statusEl) {
          statusEl.textContent = `SUCHFEHLER: ${err.error || 'Fehler bei der Suche'}`;
          statusEl.style.color = 'var(--c-red)';
        }
        return;
      }

      const data = await resp.json();
      const results = data.results || (Array.isArray(data) ? data : []);

      if (statusEl) {
        statusEl.textContent = `${results.length} PAKET(E) GEFUNDEN [QUELLE: ${isMg ? 'MOVIE GODS' : 'XDCC.EU'}] // FILTER: "${query.toUpperCase()}"`;
        statusEl.style.color = 'var(--c-gold)';
      }

      if (results.length === 0) {
        if (tableEl) tableEl.style.display = 'none';
        if (emptyEl) {
          emptyEl.style.display = 'block';
          emptyEl.textContent = `KEINE PAKETE FÜR "${query.toUpperCase()}" AUF ${isMg ? 'MOVIE GODS' : 'XDCC.EU'} GEFUNDEN`;
        }
        return;
      }

      if (emptyEl) emptyEl.style.display = 'none';
      if (tableEl) tableEl.style.display = 'table';

      let html = '';
      results.forEach(res => {
        const bot = res.botName || 'Bot';
        const pack = res.packNumber || '';
        const file = res.filename || '';
        const sizeStr = res.sizeStr || (res.sizeBytes ? formatBytes(res.sizeBytes) : '--');
        const server = res.server || (isMg ? 'irc.abjects.net' : '');
        const channel = res.channel || (isMg ? '#moviegods' : '');
        const sizeBytes = res.sizeBytes || 0;

        const safeBot = escapeHtml(bot);
        const safePack = escapeHtml(pack);
        const safeFile = escapeHtml(file);
        const safeServer = escapeHtml(server);
        const safeChannel = escapeHtml(channel);

        const accentColor = isMg ? 'var(--c-secondary)' : 'var(--c-blue)';

        html += `
          <tr>
            <td style="font-family:var(--font-family); font-weight:700; color:${accentColor};">${safeBot}</td>
            <td style="font-family:var(--mono-family); font-weight:700; color:var(--c-gold);">#${safePack}</td>
            <td style="font-family:var(--mono-family); font-size:0.85rem; word-break:break-all; color:#fff;" title="${safeFile}">
              ${safeFile}
            </td>
            <td style="font-family:var(--mono-family); color:#44dd88; white-space:nowrap;">${escapeHtml(sizeStr)}</td>
            <td style="font-family:var(--mono-family); font-size:0.75rem; color:#888;">
              ${safeServer ? `<div>${safeServer}</div>` : ''}
              ${safeChannel ? `<div style="color:var(--c-secondary); font-weight:700;">${safeChannel}</div>` : ''}
            </td>
            <td style="text-align:right;">
              <button type="button" class="left-action-btn" onclick="triggerPulsecastXdccDownload('${safeServer}', '${safeChannel}', '${safeBot.replace(/'/g, "\\'")}', '${safePack}', '${safeFile.replace(/'/g, "\\'")}', ${sizeBytes})" style="padding:0.3rem 0.8rem; font-size:0.8rem; border-color:${accentColor}; color:${accentColor}; font-weight:700;">
                <span>⬇️</span> <span>LADEN</span>
              </button>
            </td>
          </tr>
        `;
      });

      if (tbody) tbody.innerHTML = html;
    } catch (e) {
      if (statusEl) {
        statusEl.textContent = `VERBINDUNGSFEHLER: ${e}`;
        statusEl.style.color = 'var(--c-red)';
      }
    }
  }

  async function triggerPulsecastXdccDownload(server, channel, botName, packNumber, filename, expectedSize) {
    playLcarsBeep(1200, 1600);
    const targetChannel = channel || (pulsecastXdccSource === 'moviegods' ? '#moviegods' : '');
    const targetServer = server || (pulsecastXdccSource === 'moviegods' ? 'irc.abjects.net' : '');

    try {
      const resp = await fetch('/api/pulsecast/download/xdcc', {
        method: 'POST',
        headers: getPulsecastHeaders(),
        body: JSON.stringify({
          server: targetServer,
          channel: targetChannel,
          botName: botName,
          packNumber: packNumber,
          filename: filename,
          expectedSize: expectedSize
        })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsAcknowledge();
        alert(`XDCC Download angefordert:\nBot: ${botName}\nPack: #${packNumber}\nChannel: ${targetChannel}\nDatei: ${filename}`);
        switchPulsecastSubtab('downloads');
      } else {
        alert(`XDCC Fehler: ${data.error || 'Fehlgeschlagen'}`);
      }
    } catch (e) {
      alert(`XDCC Fehler: ${e}`);
    }
  }

  // LCARS PLAYER MODAL CONTROLLER
  function openPulsecastPlayerModal(filename, displayTitle) {
    if (!filename) return;
    playLcarsBeep(1200, 1600);
    const modal = document.getElementById('pulsecastPlayerModal');
    const titleEl = document.getElementById('pulsecastPlayerModalTitle');
    const metaEl = document.getElementById('pulsecastPlayerModalMeta');
    const inputEl = document.getElementById('pulsecastPlayerStreamUrlInput');
    const webPlayerContainer = document.getElementById('pulsecastWebPlayerContainer');
    const videoEl = document.getElementById('pulsecastWebPlayerVideo');
    const m3uBtn = document.getElementById('pulsecastM3uDownloadBtn');

    const cleanTitle = displayTitle || filename;
    currentPulsecastFilename = filename;
    currentPulsecastDisplayTitle = cleanTitle;
    currentPulsecastAudioTranscode = false;
    currentPulsecastNeedsTranscode = false;

    if (titleEl) titleEl.textContent = cleanTitle;
    if (metaEl) metaEl.textContent = `DATEI: ${filename} // HTTP RANGE NATIVE STREAM`;

    const code = sessionStorage.getItem('lcars_auth_code') || (typeof currentAuthCode !== 'undefined' ? currentAuthCode : '');
    const cleanFn = (filename || '').replace(/^[/]+/, '');
    const streamUrl = `${window.location.origin}/api/pulsecast/media/stream/${encodeURI(cleanFn)}${code ? `?code=${encodeURIComponent(code)}` : ''}`;
    currentPulsecastStreamUrl = streamUrl;

    if (inputEl) inputEl.value = streamUrl;

    // 1-Click M3U playlist download URL
    const safeTitle = cleanTitle.replace(/[^\\w.-]+/g, '_').replace(/^_+|_+$/g, '') || 'stream';
    const m3uUrl = `${window.location.origin}/api/pulsecast/media/stream.m3u?filename=${encodeURIComponent(cleanFn)}${code ? `&code=${encodeURIComponent(code)}` : ''}&title=${encodeURIComponent(cleanTitle)}`;
    if (m3uBtn) {
      m3uBtn.href = m3uUrl;
      m3uBtn.download = `${safeTitle}.m3u`;
    }

    // Direct app protocol schemes
    const vlcBtn = document.getElementById('playerBtnVlc');
    if (vlcBtn) vlcBtn.href = `vlc://${streamUrl}`;

    const potBtn = document.getElementById('playerBtnPotPlayer');
    if (potBtn) potBtn.href = `potplayer://${streamUrl}`;

    const iinaBtn = document.getElementById('playerBtnIina');
    if (iinaBtn) iinaBtn.href = `iina://weblink?url=${encodeURIComponent(streamUrl)}`;

    const nplayerBtn = document.getElementById('playerBtnNplayer');
    if (nplayerBtn) nplayerBtn.href = `nplayer-${streamUrl}`;

    // Reset web player container
    if (webPlayerContainer) webPlayerContainer.style.display = 'none';
    if (videoEl) {
      videoEl.pause();
      videoEl.removeAttribute('src');
      videoEl.load();
    }

    if (modal) modal.style.display = 'flex';
  }

  function closePulsecastPlayerModal() {
    playLcarsBeep(500, 300);
    const modal = document.getElementById('pulsecastPlayerModal');
    const videoEl = document.getElementById('pulsecastWebPlayerVideo');
    if (videoEl) {
      videoEl.pause();
      videoEl.removeAttribute('src');
      videoEl.load();
    }
    currentPulsecastAudioTranscode = false;
    currentPulsecastNeedsTranscode = false;
    if (modal) modal.style.display = 'none';
  }

  function handlePulsecastPlayerModalBackdropClick(e) {
    if (e.target && e.target.id === 'pulsecastPlayerModal') {
      closePulsecastPlayerModal();
    }
  }

  async function togglePulsecastWebPlayer(show) {
    const container = document.getElementById('pulsecastWebPlayerContainer');
    const videoEl = document.getElementById('pulsecastWebPlayerVideo');
    if (!container || !videoEl) return;
    if (show) {
      playLcarsBeep(1100, 1400);
      container.style.display = 'flex';

      const code = sessionStorage.getItem('lcars_auth_code') || (typeof currentAuthCode !== 'undefined' ? currentAuthCode : '');
      const cleanFn = (currentPulsecastFilename || '').replace(/^[/]+/, '');

      updatePulsecastAudioUI(true, '🔍 Analysiere Audio-Codecs via PulseCast Probe...');

      let needsTranscode = false;
      try {
        const probeUrl = `/api/pulsecast/media/probe/${encodeURI(cleanFn)}${code ? `?code=${encodeURIComponent(code)}` : ''}`;
        const res = await fetch(probeUrl, {
          headers: getPulsecastHeaders()
        });
        if (res.ok) {
          const data = await res.json();
          needsTranscode = !!data.needsAudioTranscode;
        }
      } catch (err) {
        console.warn('PulseCast Probe fehlgeschlagen, nutze Standard:', err);
      }

      currentPulsecastNeedsTranscode = needsTranscode;
      currentPulsecastAudioTranscode = needsTranscode;
      loadPulsecastVideoSource(false);
    } else {
      playLcarsBeep(700, 400);
      videoEl.pause();
      videoEl.removeAttribute('src');
      videoEl.load();
      container.style.display = 'none';
    }
  }

  function loadPulsecastVideoSource(preserveTime = false) {
    const videoEl = document.getElementById('pulsecastWebPlayerVideo');
    if (!videoEl || !currentPulsecastFilename) return;

    const code = sessionStorage.getItem('lcars_auth_code') || (typeof currentAuthCode !== 'undefined' ? currentAuthCode : '');
    const cleanFn = (currentPulsecastFilename || '').replace(/^[/]+/, '');

    let targetSrc = '';
    if (currentPulsecastAudioTranscode) {
      targetSrc = `${window.location.origin}/api/pulsecast/media/transcode/${encodeURI(cleanFn)}${code ? `?code=${encodeURIComponent(code)}` : ''}`;
    } else {
      targetSrc = `${window.location.origin}/api/pulsecast/media/stream/${encodeURI(cleanFn)}${code ? `?code=${encodeURIComponent(code)}` : ''}`;
    }

    const prevTime = preserveTime ? (videoEl.currentTime || 0) : 0;
    videoEl.src = targetSrc;
    if (prevTime > 0) {
      videoEl.onloadedmetadata = () => {
        try { videoEl.currentTime = prevTime; } catch (_) {}
        videoEl.onloadedmetadata = null;
      };
    }
    videoEl.play().catch(e => console.log('Autoplay info:', e));
    updatePulsecastAudioUI();
  }

  function togglePulsecastAudioMode() {
    playLcarsBeep(1200, 1500);
    currentPulsecastAudioTranscode = !currentPulsecastAudioTranscode;
    loadPulsecastVideoSource(true);
  }

  function updatePulsecastAudioUI(probing = false, probingText = '') {
    const toggleBtn = document.getElementById('pulsecastAudioModeToggle');
    const hintEl = document.getElementById('pulsecastAudioStatusHint');

    if (probing) {
      if (hintEl) {
        hintEl.innerHTML = probingText || '🔍 Audio-Codecs werden geprüft...';
        hintEl.style.color = 'var(--c-gold)';
      }
      return;
    }

    if (toggleBtn) {
      if (currentPulsecastAudioTranscode) {
        toggleBtn.innerHTML = '🔊 TON: AAC (KOMPATIBEL)';
        toggleBtn.style.background = 'rgba(68,221,136,0.18)';
        toggleBtn.style.color = '#44dd88';
        toggleBtn.style.borderColor = '#44dd88';
      } else {
        toggleBtn.innerHTML = '🎬 TON: ORIGINAL (NATIV)';
        toggleBtn.style.background = 'rgba(255,153,0,0.18)';
        toggleBtn.style.color = 'var(--c-butterscotch)';
        toggleBtn.style.borderColor = 'var(--c-butterscotch)';
      }
    }

    if (hintEl) {
      if (currentPulsecastAudioTranscode) {
        hintEl.innerHTML = '🔊 Audio-Transcoding aktiv (AAC Stereo/5.1 für ruckelfreien Browser-Ton)';
        hintEl.style.color = '#44dd88';
      } else {
        if (currentPulsecastNeedsTranscode) {
          hintEl.innerHTML = '🎬 Nativer Originalstream aktiv ⚠️ (Achtung: Diese Datei enthält AC3/DTS und bleibt im Browser ohne Transcoding vermutlich stumm!)';
          hintEl.style.color = 'var(--c-primary)';
        } else {
          hintEl.innerHTML = '🎬 Nativer Originalstream aktiv (Browser-kompatibler Ton)';
          hintEl.style.color = '#888';
        }
      }
    }
  }

  function copyPulsecastStreamUrl() {
    playLcarsBeep(1300, 1700);
    if (!currentPulsecastStreamUrl) return;
    navigator.clipboard.writeText(currentPulsecastStreamUrl).then(() => {
      const badge = document.getElementById('pulsecastCopySuccessBadge');
      if (badge) {
        badge.style.display = 'inline';
        setTimeout(() => { badge.style.display = 'none'; }, 3000);
      }
    }).catch(err => {
      const inp = document.getElementById('pulsecastPlayerStreamUrlInput');
      if (inp) {
        inp.select();
        document.execCommand('copy');
        const badge = document.getElementById('pulsecastCopySuccessBadge');
        if (badge) {
          badge.style.display = 'inline';
          setTimeout(() => { badge.style.display = 'none'; }, 3000);
        }
      }
    });
  }

  function openPulsecastStreamInTab() {
    playLcarsBeep(1200, 1500);
    if (currentPulsecastStreamUrl) {
      window.open(currentPulsecastStreamUrl, '_blank');
    }
  }

  // ==========================================================================
  // CACTUS NEEDLE 3 // SUBRAUM COMM ON-DEVICE ENGINE & SPRACHSTEUERUNG
  // ==========================================================================
  let geminiLiveWs = null;
  let geminiLiveMode = 'ptt'; // 'ptt' | 'live'
  let geminiLiveMuted = false;
  let geminiLiveChannelOpen = false;
  let isPttActive = false;
  let pttAudioSent = false;
  let isSpeaking = false;
  let lastSpeechTime = 0;
  let isModelSpeaking = false;
  let geminiAudioStream = null;
  let geminiAudioSourceNode = null;
  let geminiScriptNode = null;
  let geminiMuteNode = null;
  let geminiInputAnalyser = null;
  let geminiOutputAnalyser = null;
  let geminiActiveAudioSources = [];
  let geminiNextPlaybackTime = 0;
  let geminiVizAnimId = null;
  let currentModelTurnEl = null;
  let currentUserTranscriptEl = null;
  let pttStopTimer = null;

  // Live Subraum Audio Stream (Host USB-Mikrofon)
  let subspaceLiveAudioEl = null;
  let subspaceAudioCtx = null;
  let subspaceAudioSourceNode = null;
  let subspaceAudioAnalyser = null;
  let subspaceAudioAnimFrame = null;
  let isSubspaceAudioStreaming = false;
  let isSubspaceAudioMuted = false;
  let subspaceAudioVolume = 0.8;

  // Cactus Execution Mode (Test vs Live)
  let cactusExecutionMode = 'test';

  function setCactusExecutionMode(mode) {
    if (mode !== 'test' && mode !== 'live') mode = 'test';
    cactusExecutionMode = mode;

    if (mode === 'live') {
      playLcarsBeep(1600, 2000);
    } else {
      playLcarsBeep(1200, 800);
    }

    const btnTest = document.getElementById('btnCactusModeTest');
    const btnLive = document.getElementById('btnCactusModeLive');
    const modeBadge = document.getElementById('geminiLiveModeBadge');

    if (mode === 'live') {
      if (btnLive) {
        btnLive.classList.add('active');
        btnLive.style.background = '#ff3344';
        btnLive.style.color = '#fff';
        btnLive.style.borderColor = '#ff3344';
        btnLive.style.boxShadow = '0 0 12px rgba(255, 51, 68, 0.6)';
      }
      if (btnTest) {
        btnTest.classList.remove('active');
        btnTest.style.background = 'rgba(0,0,0,0.5)';
        btnTest.style.color = 'var(--c-gold)';
        btnTest.style.borderColor = 'var(--c-gold)';
        btnTest.style.boxShadow = 'none';
      }
      if (modeBadge) {
        modeBadge.textContent = 'MODUS: 🔴 LIVE (ECHTE SCHALTUNG)';
        modeBadge.style.backgroundColor = 'rgba(255, 51, 68, 0.25)';
        modeBadge.style.color = '#ff5566';
        modeBadge.style.borderColor = '#ff3344';
      }
    } else {
      if (btnTest) {
        btnTest.classList.add('active');
        btnTest.style.background = 'var(--c-gold)';
        btnTest.style.color = '#000';
        btnTest.style.borderColor = 'var(--c-gold)';
        btnTest.style.boxShadow = '0 0 10px rgba(255, 184, 51, 0.4)';
      }
      if (btnLive) {
        btnLive.classList.remove('active');
        btnLive.style.background = 'rgba(0,0,0,0.5)';
        btnLive.style.color = '#44dd88';
        btnLive.style.borderColor = '#44dd88';
        btnLive.style.boxShadow = 'none';
      }
      if (modeBadge) {
        modeBadge.textContent = 'MODUS: 🧪 TEST (SIMULATION)';
        modeBadge.style.backgroundColor = 'rgba(255, 184, 51, 0.2)';
        modeBadge.style.color = 'var(--c-gold)';
        modeBadge.style.borderColor = 'var(--c-gold)';
      }
    }

    appendGeminiLog('system', `Ausführungsmodus gewechselt: ${mode === "live" ? "🔴 LIVE (ECHTE SCHALTUNG)" : "🧪 TEST (SIMULATION)"}`);
  }

  // Cactus STT & TTS State
  let cactusIsListening = false;
  let cactusInMeterInterval = null;
  let cactusTtsMeterInterval = null;
  let cactusMediaRecorder = null;
  let cactusAudioChunks = [];
  let cactusIsRecording = false;
  let cactusLastVoiceTime = 0;
  let cactusIsLiveListening = false;

  async function checkGeminiLiveStatus() {
    try {
      const resp = await fetch('/api/gemini-live/status');
      if (!resp.ok) return;
      const data = await resp.json();
      const modelBadge = document.getElementById('geminiLiveModelBadge');
      if (modelBadge && data.model) {
        modelBadge.textContent = 'MODEL: ' + data.model.toUpperCase();
      }
      const sttBadge = document.getElementById('geminiLiveSttBadge');
      if (sttBadge && data.stt) {
        sttBadge.textContent = 'STT: ' + data.stt.toUpperCase();
      }
      const statusBadge = document.getElementById('geminiLiveStatusBadge');
      if (statusBadge) {
        statusBadge.textContent = 'STATUS: ON-DEVICE BEREIT';
        statusBadge.style.backgroundColor = 'var(--c-green, #44dd88)';
        statusBadge.style.color = '#000';
      }
    } catch (e) {
      // Ignorieren falls Endpunkt noch nicht erreichbar
    }
  }

  function initGeminiLiveSection() {
    const isLocked = isCategoryLocked('gemini_live');
    const gateView = document.getElementById('geminiLiveGateView');
    const activeContent = document.getElementById('geminiLiveActiveContent');

    if (isLocked) {
      if (gateView) gateView.style.display = 'block';
      if (activeContent) activeContent.style.display = 'none';
      if (geminiLiveChannelOpen) toggleGeminiLiveChannel();
      if (typeof stopSubspaceLiveAudio === 'function') stopSubspaceLiveAudio();
      return;
    }

    if (gateView) gateView.style.display = 'none';
    if (activeContent) activeContent.style.display = 'block';

    checkGeminiLiveStatus();
    initGeminiMeterDOM();
    updateGeminiLiveModeUI();
    setCactusExecutionMode(cactusExecutionMode);
    setupGeminiPttListeners();
  }

  function initGeminiMeterDOM() {
    const inMeter = document.getElementById('geminiInMeter');
    const outMeter = document.getElementById('geminiOutMeter');
    const streamMeter = document.getElementById('subspaceStreamMeter');
    if (inMeter && !inMeter.children.length) {
      for (let i = 0; i < 12; i++) {
        const seg = document.createElement('div');
        seg.className = 'lcars-meter-seg';
        seg.dataset.index = i;
        inMeter.appendChild(seg);
      }
    }
    if (outMeter && !outMeter.children.length) {
      for (let i = 0; i < 12; i++) {
        const seg = document.createElement('div');
        seg.className = 'lcars-meter-seg';
        seg.dataset.index = i;
        outMeter.appendChild(seg);
      }
    }
    if (streamMeter && !streamMeter.children.length) {
      for (let i = 0; i < 12; i++) {
        const seg = document.createElement('div');
        seg.className = 'lcars-meter-seg';
        seg.dataset.index = i;
        streamMeter.appendChild(seg);
      }
    }
  }

  function setSubspaceStreamVolume(val) {
    subspaceAudioVolume = parseFloat(val);
    const txt = document.getElementById('subspaceStreamVolText');
    if (txt) txt.textContent = Math.round(subspaceAudioVolume * 100) + '%';
    const audioEl = subspaceLiveAudioEl || document.getElementById('subspaceLiveAudio');
    if (audioEl) {
      if (isSubspaceAudioMuted && subspaceAudioVolume > 0) {
        isSubspaceAudioMuted = false;
        const muteBtn = document.getElementById('btnSubspaceStreamMute');
        if (muteBtn) {
          muteBtn.textContent = '🔊 TON AN';
          muteBtn.style.color = 'var(--c-secondary)';
          muteBtn.style.borderColor = 'var(--c-secondary)';
        }
      }
      audioEl.volume = isSubspaceAudioMuted ? 0 : subspaceAudioVolume;
    }
  }

  function toggleSubspaceStreamMute() {
    isSubspaceAudioMuted = !isSubspaceAudioMuted;
    const muteBtn = document.getElementById('btnSubspaceStreamMute');
    if (muteBtn) {
      if (isSubspaceAudioMuted) {
        muteBtn.textContent = '🔇 STUMM';
        muteBtn.style.color = '#ff5566';
        muteBtn.style.borderColor = '#ff5566';
      } else {
        muteBtn.textContent = '🔊 TON AN';
        muteBtn.style.color = 'var(--c-secondary)';
        muteBtn.style.borderColor = 'var(--c-secondary)';
      }
    }
    const audioEl = subspaceLiveAudioEl || document.getElementById('subspaceLiveAudio');
    if (audioEl) {
      audioEl.volume = isSubspaceAudioMuted ? 0 : subspaceAudioVolume;
    }
  }

  function renderSubspaceStreamMeter(level) {
    const txt = document.getElementById('subspaceStreamMeterText');
    if (txt) txt.textContent = level + '%';
    const meter = document.getElementById('subspaceStreamMeter');
    if (meter) {
      const segs = meter.children;
      const activeCount = Math.round((level / 100) * segs.length);
      for (let i = 0; i < segs.length; i++) {
        segs[i].className = 'lcars-meter-seg';
        if (i < activeCount) {
          if (i < 8) segs[i].classList.add('active-low');
          else if (i < 10) segs[i].classList.add('active-mid');
          else segs[i].classList.add('active-high');
        }
      }
    }
  }

  function startSubspaceAnalyserLoop() {
    if (subspaceAudioAnimFrame) cancelAnimationFrame(subspaceAudioAnimFrame);
    const dataArray = subspaceAudioAnalyser ? new Uint8Array(subspaceAudioAnalyser.frequencyBinCount) : null;

    function tick() {
      if (!isSubspaceAudioStreaming) {
        renderSubspaceStreamMeter(0);
        return;
      }
      if (subspaceAudioAnalyser && dataArray) {
        subspaceAudioAnalyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i];
        }
        const avg = sum / dataArray.length;
        const level = Math.min(100, Math.round((avg / 128) * 100));
        renderSubspaceStreamMeter(level);
      }
      subspaceAudioAnimFrame = requestAnimationFrame(tick);
    }
    tick();
  }

  function stopSubspaceLiveAudio() {
    if (!isSubspaceAudioStreaming && (!subspaceLiveAudioEl || subspaceLiveAudioEl.paused)) return;
    const playBtn = document.getElementById('btnSubspaceStreamPlay');
    const badge = document.getElementById('subspaceAudioStreamStatus');
    const audioEl = subspaceLiveAudioEl || document.getElementById('subspaceLiveAudio');

    isSubspaceAudioStreaming = false;
    if (subspaceAudioAnimFrame) {
      cancelAnimationFrame(subspaceAudioAnimFrame);
      subspaceAudioAnimFrame = null;
    }

    if (audioEl) {
      try {
        audioEl.pause();
        audioEl.removeAttribute('src');
        audioEl.load();
      } catch (e) {}
    }

    if (playBtn) {
      playBtn.textContent = '▶ STREAM STARTEN';
      playBtn.style.color = '#44dd88';
      playBtn.style.borderColor = '#44dd88';
    }
    if (badge) {
      badge.textContent = 'STREAM: GETRENNT';
      badge.style.background = 'rgba(255,255,255,0.08)';
      badge.style.color = '#888';
      badge.style.border = 'none';
    }
    renderSubspaceStreamMeter(0);
  }

  function toggleSubspaceLiveAudio() {
    if (isSubspaceAudioStreaming) {
      stopSubspaceLiveAudio();
      return;
    }

    playLcarsBeep(1200, 1600);
    const playBtn = document.getElementById('btnSubspaceStreamPlay');
    const badge = document.getElementById('subspaceAudioStreamStatus');

    if (playBtn) {
      playBtn.textContent = '⏳ VERBINDE...';
      playBtn.style.color = 'var(--c-gold)';
      playBtn.style.borderColor = 'var(--c-gold)';
    }
    if (badge) {
      badge.textContent = 'STREAM: VERBINDE...';
      badge.style.background = 'rgba(255,184,51,0.15)';
      badge.style.color = 'var(--c-gold)';
      badge.style.border = '1px solid var(--c-gold)';
    }

    if (!subspaceLiveAudioEl) {
      subspaceLiveAudioEl = document.getElementById('subspaceLiveAudio');
      if (!subspaceLiveAudioEl) {
        subspaceLiveAudioEl = document.createElement('audio');
        subspaceLiveAudioEl.id = 'subspaceLiveAudio';
        subspaceLiveAudioEl.preload = 'none';
        subspaceLiveAudioEl.style.display = 'none';
        document.body.appendChild(subspaceLiveAudioEl);
      }

      subspaceLiveAudioEl.crossOrigin = 'anonymous';

      subspaceLiveAudioEl.addEventListener('playing', () => {
        isSubspaceAudioStreaming = true;
        if (playBtn) {
          playBtn.textContent = '⏹ STREAM STOPPEN';
          playBtn.style.color = '#ff5566';
          playBtn.style.borderColor = '#ff5566';
        }
        if (badge) {
          badge.textContent = 'STREAM: LIVE VERBUNDEN';
          badge.style.background = 'var(--c-green, #44dd88)';
          badge.style.color = '#000';
          badge.style.border = 'none';
        }
        startSubspaceAnalyserLoop();
      });

      subspaceLiveAudioEl.addEventListener('error', (e) => {
        if (!isSubspaceAudioStreaming || !subspaceLiveAudioEl.getAttribute('src')) return;
        console.warn('[Subraum] Audio Stream Fehler:', e);
        isSubspaceAudioStreaming = false;
        if (subspaceAudioAnimFrame) cancelAnimationFrame(subspaceAudioAnimFrame);
        if (playBtn) {
          playBtn.textContent = '▶ STREAM STARTEN';
          playBtn.style.color = '#44dd88';
          playBtn.style.borderColor = '#44dd88';
        }
        if (badge) {
          badge.textContent = 'STREAM: FEHLER';
          badge.style.background = 'rgba(255,85,102,0.2)';
          badge.style.color = '#ff5566';
          badge.style.border = '1px solid #ff5566';
        }
        renderSubspaceStreamMeter(0);
      });

      subspaceLiveAudioEl.addEventListener('ended', () => {
        stopSubspaceLiveAudio();
      });
    }

    // Set volume
    subspaceLiveAudioEl.volume = isSubspaceAudioMuted ? 0 : subspaceAudioVolume;

    // Connect Web Audio API AnalyserNode if supported
    try {
      const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
      if (AudioCtxClass && !subspaceAudioCtx) {
        subspaceAudioCtx = new AudioCtxClass();
      }
      if (subspaceAudioCtx && subspaceAudioCtx.state === 'suspended') {
        subspaceAudioCtx.resume();
      }
      if (subspaceAudioCtx && !subspaceAudioSourceNode && subspaceLiveAudioEl) {
        subspaceAudioSourceNode = subspaceAudioCtx.createMediaElementSource(subspaceLiveAudioEl);
        subspaceAudioAnalyser = subspaceAudioCtx.createAnalyser();
        subspaceAudioAnalyser.fftSize = 64;
        subspaceAudioSourceNode.connect(subspaceAudioAnalyser);
        subspaceAudioAnalyser.connect(subspaceAudioCtx.destination);
      }
    } catch (err) {
      console.warn('[Subraum] Web Audio API Analyser nicht verfügbar oder bereits verbunden:', err);
    }

    const streamUrl = `/api/voice/stream?format=mp3&t=${Date.now()}`;
    subspaceLiveAudioEl.src = streamUrl;
    subspaceLiveAudioEl.play().catch(err => {
      console.warn('[Subraum] Audio Playback Startfehler:', err);
      if (badge) {
        badge.textContent = 'STREAM: FEHLER / BLOCKIERT';
        badge.style.background = 'rgba(255,85,102,0.2)';
        badge.style.color = '#ff5566';
        badge.style.border = '1px solid #ff5566';
      }
      if (playBtn) {
        playBtn.textContent = '▶ STREAM STARTEN';
        playBtn.style.color = '#44dd88';
        playBtn.style.borderColor = '#44dd88';
      }
      isSubspaceAudioStreaming = false;
    });
  }

  function setGeminiLiveMode(mode) {
    if (geminiLiveMode === mode) return;
    playLcarsBeep(1200, 1600);
    geminiLiveMode = mode;
    isSpeaking = false;
    isPttActive = false;
    const turnInd = document.getElementById('geminiLiveTurnIndicator');
    if (turnInd && !isModelSpeaking) turnInd.textContent = 'BEREIT // ZUHÖREN';
    updateGeminiLiveModeUI();

    if (geminiLiveMode === 'live' && geminiLiveChannelOpen && !isModelSpeaking) {
      startLiveRecognition();
    } else if (geminiLiveMode === 'ptt') {
      stopLiveRecognition();
    }
  }

  function updateGeminiLiveModeUI() {
    const btnPtt = document.getElementById('btnGeminiModePtt');
    const btnLive = document.getElementById('btnGeminiModeLive');
    const audioBadge = document.getElementById('geminiLiveAudioBadge');
    const pttArea = document.getElementById('geminiPttActionArea');
    const liveArea = document.getElementById('geminiLiveStatusArea');

    if (geminiLiveMode === 'ptt') {
      if (btnPtt) {
        btnPtt.style.background = 'var(--c-secondary)';
        btnPtt.style.color = '#000';
        btnPtt.style.border = 'none';
      }
      if (btnLive) {
        btnLive.style.background = 'rgba(0,0,0,0.5)';
        btnLive.style.color = 'var(--c-gold)';
        btnLive.style.border = '1px solid var(--c-gold)';
      }
      if (audioBadge) audioBadge.textContent = 'AUDIO: PTT';
      if (pttArea) pttArea.style.display = 'block';
      if (liveArea) liveArea.style.display = 'none';
    } else {
      if (btnPtt) {
        btnPtt.style.background = 'rgba(0,0,0,0.5)';
        btnPtt.style.color = 'var(--c-secondary)';
        btnPtt.style.border = '1px solid var(--c-secondary)';
      }
      if (btnLive) {
        btnLive.style.background = 'var(--c-gold)';
        btnLive.style.color = '#000';
        btnLive.style.border = 'none';
      }
      if (audioBadge) audioBadge.textContent = 'AUDIO: DAUERHAFT LIVE';
      if (pttArea) pttArea.style.display = 'none';
      if (liveArea) liveArea.style.display = 'block';
    }
  }

  function toggleGeminiLiveMute() {
    geminiLiveMuted = !geminiLiveMuted;
    playLcarsBeep(880, 1320);
    const muteBtn = document.getElementById('btnGeminiMute');
    if (muteBtn) {
      if (geminiLiveMuted) {
        muteBtn.textContent = '🔇 MIKROFON: STUMM';
        muteBtn.style.borderColor = 'var(--c-red)';
        muteBtn.style.color = 'var(--c-red)';
        stopLiveRecognition();
      } else {
        muteBtn.textContent = '🎤 MIKROFON: AN';
        muteBtn.style.borderColor = 'var(--c-gold)';
        muteBtn.style.color = 'var(--c-gold)';
        if (geminiLiveMode === 'live' && geminiLiveChannelOpen) {
          startLiveRecognition();
        }
      }
    }
  }

  function toggleGeminiLiveChannel() {
    geminiLiveChannelOpen = !geminiLiveChannelOpen;
    playLcarsBeep(1200, 1600);
    updateGeminiToggleBtn(geminiLiveChannelOpen);

    if (geminiLiveChannelOpen) {
      updateGeminiConnBadge('STATUS: ON-DEVICE BEREIT', 'var(--c-green, #44dd88)', '#000');
      appendGeminiLog('system', 'Subraum-Sprachkanal geöffnet. Cactus Needle 3 Sprachengine aktiv.');
      startGeminiAudioCapture();
      if (geminiLiveMode === 'live') {
        startLiveRecognition();
      }
    } else {
      updateGeminiConnBadge('GETRENNT', 'var(--c-red)', '#fff');
      appendGeminiLog('system', 'Subraum-Sprachkanal geschlossen.');
      stopLiveRecognition();
      stopGeminiAudioCapture();
      stopAllGeminiAudio();
    }
  }

  function updateGeminiConnBadge(text, bg, fg = '#000') {
    const badge = document.getElementById('geminiLiveStatusBadge');
    if (badge) {
      badge.textContent = text;
      badge.style.backgroundColor = bg;
      badge.style.color = fg;
    }
  }

  function updateGeminiToggleBtn(isOpen) {
    const btn = document.getElementById('btnGeminiToggleChannel');
    if (!btn) return;
    if (isOpen) {
      btn.textContent = '⏹ KANAL TRENNEN';
      btn.style.borderColor = 'var(--c-red)';
      btn.style.color = 'var(--c-red)';
    } else {
      btn.textContent = '▶ SUBRAUM-KANAL ÖFFNEN';
      btn.style.borderColor = '#44dd88';
      btn.style.color = '#44dd88';
    }
  }

  // ==========================================================================
  // DASHBOARD UI CONTEXT & NAVIGATION ENGINE FÜR CACTUS
  // ==========================================================================
  function getDashboardUiContext() {
    const activeSection = currentCategory || 'system';
    const activeSectionTitle = CATEGORY_NAMES[activeSection] || activeSection;

    const visibleNav = [];
    document.querySelectorAll('.lcars-pill-btn').forEach(btn => {
      const isVisible = window.getComputedStyle(btn).display !== 'none';
      const id = btn.id ? btn.id.replace('btn-cat-', '') : '';
      const text = (btn.innerText || btn.textContent || '').trim().replace(/\\s+/g, ' ');
      if (isVisible && id) {
        visibleNav.push({
          id: id,
          label: text,
          active: id === activeSection
        });
      }
    });

    let pagination = {
      has_pagination: false,
      scope: null,
      current_page: 1,
      total_pages: 1,
      has_next: false,
      has_prev: false
    };

    if (activeSection === 'pulsecast') {
      if (typeof pulsecastActiveSubtab !== 'undefined' && pulsecastActiveSubtab === 'local') {
        const cur = typeof pulsecastLocalPage === 'number' ? pulsecastLocalPage : 1;
        const tot = typeof pulsecastLocalTotalPages === 'number' ? pulsecastLocalTotalPages : 1;
        pagination = {
          has_pagination: true,
          scope: 'pulsecast_local',
          current_page: cur,
          total_pages: tot,
          has_next: cur < tot,
          has_prev: cur > 1
        };
      } else {
        const cur = typeof pulsecastCatalogPage === 'number' ? pulsecastCatalogPage : 1;
        const tot = typeof pulsecastCatalogTotalPages === 'number' ? pulsecastCatalogTotalPages : 1;
        pagination = {
          has_pagination: true,
          scope: 'pulsecast_catalog',
          current_page: cur,
          total_pages: tot,
          has_next: cur < tot,
          has_prev: cur > 1
        };
      }
    }

    const canGoBack = typeof categoryHistoryIndex !== 'undefined' && categoryHistoryIndex > 0;
    const canGoForward = typeof categoryHistoryIndex !== 'undefined' && typeof categoryHistory !== 'undefined' && categoryHistoryIndex < categoryHistory.length - 1;

    return {
      active_section: activeSection,
      active_section_title: activeSectionTitle,
      visible_navigation: visibleNav,
      pagination: pagination,
      can_go_back: canGoBack,
      can_go_forward: canGoForward
    };
  }

  function normalizeCategoryTarget(target) {
    if (!target) return null;
    target = target.toLowerCase().trim();
    if (CATEGORY_NAMES[target]) return target;
    const map = {
      'system': 'system', 'hardware': 'system', 'terminal': 'system', 'sensor': 'system', 'sensoren': 'system', 'cpu': 'system', 'ram': 'system', 'agy-pi': 'system', 'agypi': 'system',
      'services': 'services', 'service': 'services', 'prozesse': 'services', 'scanner': 'services', 'prozess-scanner': 'services', 'server': 'services', 'dienste': 'services',
      'ai': 'ai', 'ki': 'ai', 'ki-bereich': 'ai', 'ki bereich': 'ai', 'künstliche intelligenz': 'ai', 'kuenstliche intelligenz': 'ai', 'ki themen': 'ai', 'ai themen': 'ai',
      '9router': '9router', '9 router': '9router', 'router': '9router', '9router comm': '9router', 'router comm': '9router',
      'hermes': 'hermes', 'hermes agenten': 'hermes', 'system-agenten': 'hermes', 'system agenten': 'hermes',
      'ide': 'ide', 'antigravity': 'ide', 'antigravity ide': 'ide', 'entwicklungsumgebung': 'ide',
      'agents': '9router', 'agenten': '9router', 'chat': '9router', 'ki-agenten': 'ai', 'ki agenten': 'ai',
      'ai-info': 'ai-info', 'ai_info': 'ai-info', 'ki-info': 'ai-info', 'ki_info': 'ai-info', 'telemetrie': 'ai-info', 'neural': 'ai-info',
      'config': 'config', 'konfiguration': 'config', 'einstellungen': 'config', 'settings': 'config', 'farbmodi': 'config', 'farbmodus': 'config', 'theme': 'config',
      'fantasy': 'fantasy', 'espn': 'fantasy', 'football': 'fantasy', 'incomplete pass': 'fantasy',
      'personal': 'personal', 'persönlich': 'personal', 'persoenlich': 'personal', 'persönlicher bereich': 'personal', 'persoenlicher bereich': 'personal', 'privat': 'personal',
      'solar': 'solar', 'balkonsolar': 'solar', 'energie': 'solar', 'photovoltaik': 'solar', 'pv': 'solar', 'akku': 'solar', 'strom': 'solar', 'hausverbrauch': 'solar',
      'homeassistant': 'homeassistant', 'ha': 'homeassistant', 'haussteuerung': 'homeassistant', 'smart home': 'homeassistant', 'smarthome': 'homeassistant',
      'cycle': 'cycle', 'zyklus': 'cycle', 'bio': 'cycle', 'bio-telemetrie': 'cycle', 'partnerin': 'cycle',
      'pulsecast': 'pulsecast', 'mediathek': 'pulsecast', 'downloads': 'pulsecast', 'katalog': 'pulsecast', 'media': 'pulsecast', 'filme': 'pulsecast', 'serien': 'pulsecast',
      'gemini_live': 'gemini_live', 'gemini-live': 'gemini_live', 'subraum': 'gemini_live', 'subraum comm': 'gemini_live', 'cactus': 'gemini_live', 'sprachsteuerung': 'gemini_live',
      'devteam': 'devteam', 'dev-team': 'devteam', 'kanban': 'devteam', 'tasks': 'devteam', 'workflow engine': 'devteam'
    };
    return map[target] || null;
  }

  function refreshCurrentView() {
    playLcarsBeep(880, 1760);
    if (typeof fetchLiveStats === 'function') fetchLiveStats(false);
    if (currentCategory === 'system') {
      if (typeof initHistoryChart === 'function') initHistoryChart();
    } else if (currentCategory === 'ai') {
      if (typeof initAiOverview === 'function') initAiOverview();
    } else if (currentCategory === 'personal') {
      if (typeof initPersonalOverview === 'function') initPersonalOverview();
    } else if (currentCategory === 'fantasy') {
      if (typeof loadFantasyData === 'function') loadFantasyData(false);
    } else if (currentCategory === 'homeassistant') {
      if (typeof loadHomeAssistantData === 'function') loadHomeAssistantData(false);
    } else if (currentCategory === 'solar') {
      if (typeof loadSolarData === 'function') loadSolarData(false);
    } else if (currentCategory === 'pulsecast') {
      if (typeof pulsecastActiveSubtab !== 'undefined') {
        if (pulsecastActiveSubtab === 'catalog' && typeof loadPulsecastCatalog === 'function') {
          loadPulsecastCatalog(pulsecastCatalogPage || 1);
        } else if (pulsecastActiveSubtab === 'local' && typeof loadPulsecastLocal === 'function') {
          loadPulsecastLocal(pulsecastLocalPage || 1);
        } else if (typeof fetchPulsecastDownloads === 'function') {
          fetchPulsecastDownloads();
        }
      }
    } else if (currentCategory === 'devteam') {
      if (typeof fetchDevteamData === 'function') fetchDevteamData(true);
    }
  }

  function executeDashboardUiAction(res) {
    if (!res) return;
    const toolCall = res.tool_call || {};
    const toolName = toolCall.name || (res.ui_action ? res.ui_action.type : null);
    const args = toolCall.arguments || {};
    const uiAct = res.ui_action || {};

    // 1. Sektions-Navigation (navigate_section)
    if (toolName === 'navigate_section' || uiAct.type === 'navigate') {
      const target = (args.target || uiAct.target || uiAct.section || '').trim().toLowerCase();
      const normalizedTarget = normalizeCategoryTarget(target);
      if (normalizedTarget && CATEGORY_NAMES[normalizedTarget]) {
        playLcarsBeep(980, 1400);
        switchCategory(normalizedTarget);
        appendGeminiLog('computer', `[UI-AKTION] Zu Sektion '${CATEGORY_NAMES[normalizedTarget]}' gewechselt.`);
      } else {
        console.warn('Unbekannte Zielkategorie für Navigation:', target);
        playLcarsBeep(440, 220);
        appendGeminiLog('error', `[UI-AKTION] Unbekannte Zielkategorie '${target}'.`);
      }
      return;
    }

    // 2. Paginierung in Tabellen/Listen (paginate)
    if (toolName === 'paginate' || uiAct.type === 'paginate') {
      const dir = (args.direction || uiAct.direction || '').toLowerCase();
      const delta = typeof uiAct.delta === 'number' ? uiAct.delta : (dir === 'next' || dir === 'weiter' || dir === 'vor' ? 1 : -1);

      if (currentCategory === 'pulsecast') {
        if (typeof pulsecastActiveSubtab !== 'undefined' && pulsecastActiveSubtab === 'local') {
          if (typeof pulsecastLocalChangePage === 'function') {
            pulsecastLocalChangePage(delta);
            appendGeminiLog('computer', `[UI-AKTION] PulseCast Lokale Medien Seite ${delta > 0 ? 'vor' : 'zurück'} geblättert.`);
          }
        } else {
          if (typeof pulsecastChangePage === 'function') {
            pulsecastChangePage(delta);
            appendGeminiLog('computer', `[UI-AKTION] PulseCast Katalog Seite ${delta > 0 ? 'vor' : 'zurück'} geblättert.`);
          }
        }
      } else {
        appendGeminiLog('computer', `[UI-AKTION] Paginierung in Sektion '${currentCategory}' nicht verfügbar.`);
      }
      return;
    }

    // 3. UI-Aktionen: Verlauf vor/zurück, Refresh, Vollbild (ui_action)
    if (toolName === 'ui_action' || uiAct.type === 'ui_action') {
      const act = (args.action || uiAct.action || '').toLowerCase();
      if (act === 'back' || act === 'zurueck' || act === 'zurück') {
        const moved = navigateCategoryHistory(-1);
        if (moved) {
          appendGeminiLog('computer', `[UI-AKTION] Zurück zu Sektion '${CATEGORY_NAMES[currentCategory] || currentCategory}' navigiert.`);
        } else {
          playLcarsBeep(440, 220);
          appendGeminiLog('computer', '[UI-AKTION] Kein vorheriger Verlauf vorhanden.');
        }
      } else if (act === 'forward' || act === 'vor') {
        const moved = navigateCategoryHistory(1);
        if (moved) {
          appendGeminiLog('computer', `[UI-AKTION] Vorwärts zu Sektion '${CATEGORY_NAMES[currentCategory] || currentCategory}' navigiert.`);
        } else {
          playLcarsBeep(440, 220);
          appendGeminiLog('computer', '[UI-AKTION] Kein weiterer Vorwärts-Verlauf vorhanden.');
        }
      } else if (act === 'refresh' || act === 'reload' || act === 'aktualisieren') {
        refreshCurrentView();
        appendGeminiLog('computer', `[UI-AKTION] Sektion '${CATEGORY_NAMES[currentCategory] || currentCategory}' aktualisiert.`);
      } else if (act === 'fullscreen' || act === 'vollbild') {
        if (typeof toggleFullscreen === 'function') {
          toggleFullscreen();
          appendGeminiLog('computer', '[UI-AKTION] Vollbildmodus umgeschaltet.');
        }
      }
      return;
    }
  }

  // ==========================================================================
  // CACTUS NEEDLE 3 INFERENZ & PROMPT ENGINE
  // ==========================================================================
  async function sendPromptToCactus(promptText) {
    promptText = (promptText || '').trim();
    if (!promptText) return;

    playLcarsBeep(1200, 1600);
    const turnInd = document.getElementById('geminiLiveTurnIndicator');
    if (turnInd) turnInd.textContent = 'CACTUS INFERENZ...';
    updateGeminiConnBadge('VERARBEITET...', 'var(--c-gold)', '#000');

    // 1. Protokolliert im Subraum-Logbuch: [BENUTZER] promptText inklusive aktivem Modus
    const modePrefix = cactusExecutionMode === 'live' ? '[🔴 LIVE]' : '[🧪 TEST]';
    appendGeminiLog('user', `${modePrefix} ${promptText}`);

    try {
      const resp = await fetch('/api/cactus/process', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          prompt: promptText,
          mode: cactusExecutionMode,
          context: getDashboardUiContext()
        })
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        const errMsg = errData.error || `HTTP ${resp.status} ${resp.statusText}`;
        appendGeminiLog('error', errMsg);
        updateGeminiConnBadge('FEHLER', 'var(--c-red)', '#fff');
        if (turnInd) turnInd.textContent = 'FEHLER // INFERENZ';
        playLcarsBeep(440, 220);
        return;
      }

      const res = await resp.json();
      if (!res.success && res.error) {
        appendGeminiLog('error', res.error);
        updateGeminiConnBadge('FEHLER', 'var(--c-red)', '#fff');
        if (turnInd) turnInd.textContent = 'FEHLER // INFERENZ';
        playLcarsBeep(440, 220);
        return;
      }

      // 2. Protokolliert im Subraum-Logbuch: [CACTUS ENGINE] tool, confidence, latency, mode
      let toolStr = 'Kein Tool-Aufruf (Direktantwort)';
      if (res.tool_call && res.tool_call.name) {
        const argsStr = res.tool_call.arguments ? JSON.stringify(res.tool_call.arguments) : '';
        toolStr = `${res.tool_call.name}(${argsStr})`;
      } else if (res.action) {
        toolStr = `${res.action} [${res.entity_id || ''}]`;
      }

      const activeMode = (res.mode || cactusExecutionMode) === 'live' ? '🔴 LIVE' : '🧪 TEST';
      const engineMsg = `Modus: ${activeMode} | Tool: ${toolStr} | Konfidenz: ${res.confidence}% | Latenz: ${res.latency_ms} ms`;
      appendGeminiLog('cactus', engineMsg);

      // 3. Protokolliert im Subraum-Logbuch: [COMPUTER] message
      appendGeminiLog('computer', res.message || 'Befehl ausgeführt.');

      // 4. Sprachausgabe (TTS): Liest message mit window.speechSynthesis vor und animiert das Ausgabemeter
      speakCactusMessage(res.message || 'Befehl ausgeführt.');

      // 5. UI-Aktionen im Dashboard ausführen (Kategorie-Umschaltung, Vor-/Zurück, Paginierung)
      if (res.ui_action || (res.tool_call && ['navigate_section', 'paginate', 'ui_action'].includes(res.tool_call.name))) {
        executeDashboardUiAction(res);
      }

    } catch (err) {
      appendGeminiLog('error', `Verbindungsfehler: ${err.message}`);
      updateGeminiConnBadge('FEHLER', 'var(--c-red)', '#fff');
      if (turnInd) turnInd.textContent = 'FEHLER // NETZWERK';
      playLcarsBeep(440, 220);
    }
  }

  function runCactusTestCase(promptText) {
    sendPromptToCactus(promptText);
  }

  function sendGeminiLiveText() {
    const inp = document.getElementById('geminiLiveTextInput');
    if (!inp) return;
    const val = inp.value.trim();
    if (!val) return;
    inp.value = '';
    sendPromptToCactus(val);
  }

  // ==========================================================================
  // SPRACHAUSGABE (TTS) & AUSGABEMETER-ANIMATION
  // ==========================================================================
  function speakCactusMessage(message) {
    if (!message) return;
    if (!('speechSynthesis' in window)) {
      console.warn('SpeechSynthesis API nicht verfügbar');
      return;
    }

    try {
      window.speechSynthesis.cancel();
    } catch (e) {}
    stopCactusTtsMeter();

    const utter = new SpeechSynthesisUtterance(message);
    utter.lang = 'de-DE';
    utter.rate = 1.0;
    utter.pitch = 1.0;

    const voices = window.speechSynthesis.getVoices ? window.speechSynthesis.getVoices() : [];
    const deVoice = voices.find(v => v.lang && (v.lang === 'de-DE' || v.lang.startsWith('de')));
    if (deVoice) utter.voice = deVoice;

    utter.onstart = () => {
      isModelSpeaking = true;
      updateGeminiConnBadge('SPRICHT...', 'var(--c-blue)', '#000');
      const turnInd = document.getElementById('geminiLiveTurnIndicator');
      if (turnInd) turnInd.textContent = 'BORDCOMPUTER SPRICHT...';
      startCactusTtsMeter();
    };

    utter.onend = () => {
      isModelSpeaking = false;
      stopCactusTtsMeter();
      updateGeminiConnBadge('STATUS: ON-DEVICE BEREIT', '#44dd88', '#000');
      const turnInd = document.getElementById('geminiLiveTurnIndicator');
      if (turnInd) turnInd.textContent = 'BEREIT // ZUHÖREN';
      if (geminiLiveMode === 'live' && geminiLiveChannelOpen) {
        startLiveRecognition();
      }
    };

    utter.onerror = (e) => {
      isModelSpeaking = false;
      stopCactusTtsMeter();
      updateGeminiConnBadge('STATUS: ON-DEVICE BEREIT', '#44dd88', '#000');
      const turnInd = document.getElementById('geminiLiveTurnIndicator');
      if (turnInd) turnInd.textContent = 'BEREIT // ZUHÖREN';
      if (geminiLiveMode === 'live' && geminiLiveChannelOpen) {
        startLiveRecognition();
      }
    };

    window.speechSynthesis.speak(utter);
  }

  function startCactusTtsMeter() {
    stopCactusTtsMeter();
    cactusTtsMeterInterval = setInterval(() => {
      const level = Math.floor(Math.random() * 55) + 40;
      renderGeminiOutputMeterOnly(level);
    }, 90);
  }

  function stopCactusTtsMeter() {
    if (cactusTtsMeterInterval) {
      clearInterval(cactusTtsMeterInterval);
      cactusTtsMeterInterval = null;
    }
    renderGeminiOutputMeterOnly(0);
  }

  function renderGeminiOutputMeterOnly(outLevel) {
    const outText = document.getElementById('geminiOutLevelText');
    if (outText) outText.textContent = outLevel + '%';

    const outMeter = document.getElementById('geminiOutMeter');
    if (outMeter) {
      const segs = outMeter.children;
      const activeCount = Math.round((outLevel / 100) * segs.length);
      for (let i = 0; i < segs.length; i++) {
        segs[i].className = 'lcars-meter-seg';
        if (i < activeCount) {
          if (i < 8) segs[i].classList.add('active-low');
          else if (i < 10) segs[i].classList.add('active-mid');
          else segs[i].classList.add('active-high');
        }
      }
    }
  }

  function renderGeminiInputMeterOnly(inLevel) {
    const inText = document.getElementById('geminiInLevelText');
    if (inText) inText.textContent = inLevel + '%';

    const inMeter = document.getElementById('geminiInMeter');
    if (inMeter) {
      const segs = inMeter.children;
      const activeCount = Math.round((inLevel / 100) * segs.length);
      for (let i = 0; i < segs.length; i++) {
        segs[i].className = 'lcars-meter-seg';
        if (i < activeCount) {
          if (i < 8) segs[i].classList.add('active-low');
          else if (i < 10) segs[i].classList.add('active-mid');
          else segs[i].classList.add('active-high');
        }
      }
    }
  }

  function startInputMeterAnim() {
    if (!geminiInputAnalyser) {
      if (cactusInMeterInterval) clearInterval(cactusInMeterInterval);
      cactusInMeterInterval = setInterval(() => {
        const level = Math.floor(Math.random() * 50) + 35;
        renderGeminiInputMeterOnly(level);
      }, 95);
    }
  }

  function stopInputMeterAnim() {
    if (cactusInMeterInterval) {
      clearInterval(cactusInMeterInterval);
      cactusInMeterInterval = null;
    }
    if (!geminiInputAnalyser) {
      renderGeminiInputMeterOnly(0);
    }
  }

  // ==========================================================================
  // SPRACHEINGABE (STT) VIA LOKALES FASTER-WHISPER & MEDIARECORDER
  // ==========================================================================
  function getSupportedAudioMimeType() {
    if (typeof MediaRecorder === 'undefined') return '';
    const types = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/ogg',
      'audio/mp4'
    ];
    for (const t of types) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return '';
  }

  async function sendAudioToWhisper(audioBlob) {
    if (!audioBlob || audioBlob.size < 500) {
      const turnInd = document.getElementById('geminiLiveTurnIndicator');
      if (turnInd && !isModelSpeaking) turnInd.textContent = 'BEREIT // ZUHÖREN';
      updateGeminiConnBadge('STATUS: ON-DEVICE BEREIT', '#44dd88', '#000');
      return;
    }

    const turnInd = document.getElementById('geminiLiveTurnIndicator');
    if (turnInd) turnInd.textContent = 'WHISPER BASE INFERENZ (LOKAL)...';
    updateGeminiConnBadge('STT INFERENZ...', 'var(--c-gold)', '#000');

    const formData = new FormData();
    formData.append('audio', audioBlob, 'subraum_speech.webm');
    formData.append('mode', cactusExecutionMode);
    formData.append('process', 'true');
    formData.append('context', JSON.stringify(getDashboardUiContext()));

    try {
      const resp = await fetch('/api/voice/transcribe', {
        method: 'POST',
        body: formData
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        const errMsg = errData.error || `HTTP ${resp.status} ${resp.statusText}`;
        appendGeminiLog('error', `STT Fehler: ${errMsg}`);
        updateGeminiConnBadge('FEHLER', 'var(--c-red)', '#fff');
        if (turnInd) turnInd.textContent = 'FEHLER // STT';
        playLcarsBeep(440, 220);
        return;
      }

      const res = await resp.json();
      if (!res.success && res.error) {
        appendGeminiLog('error', res.error);
        updateGeminiConnBadge('FEHLER', 'var(--c-red)', '#fff');
        if (turnInd) turnInd.textContent = 'FEHLER // INFERENZ';
        playLcarsBeep(440, 220);
        return;
      }

      const recognized = (res.text || '').trim();
      if (!recognized) {
        if (turnInd) turnInd.textContent = 'KEINE SPRACHE ERKANNT';
        updateGeminiConnBadge('STATUS: ON-DEVICE BEREIT', '#44dd88', '#000');
        return;
      }

      if (turnInd) {
        turnInd.textContent = `ERKANNT: "${recognized}"`;
      }

      // Log User speech
      const modePrefix = cactusExecutionMode === 'live' ? '[🔴 LIVE]' : '[🧪 TEST]';
      appendGeminiLog('user', `${modePrefix} ${recognized}`);

      // Log Cactus Engine result
      let toolStr = 'Kein Tool-Aufruf (Direktantwort)';
      if (res.tool_call && res.tool_call.name) {
        const argsStr = res.tool_call.arguments ? JSON.stringify(res.tool_call.arguments) : '';
        toolStr = `${res.tool_call.name}(${argsStr})`;
      } else if (res.action) {
        toolStr = `${res.action} [${res.entity_id || ''}]`;
      }
      const activeMode = (res.mode || cactusExecutionMode) === 'live' ? '🔴 LIVE' : '🧪 TEST';
      const engineMsg = `Modus: ${activeMode} | Tool: ${toolStr} | Konfidenz: ${res.confidence}% | Latenz: ${res.latency_ms} ms`;
      appendGeminiLog('cactus', engineMsg);

      // Log Computer response
      appendGeminiLog('computer', res.message || 'Befehl ausgeführt.');

      // Update badge & sound
      updateGeminiConnBadge('STATUS: ON-DEVICE BEREIT', '#44dd88', '#000');
      playLcarsBeep(1200, 1600);

      // TTS speech output
      speakCactusMessage(res.message || 'Befehl ausgeführt.');

      // 5. UI-Aktionen im Dashboard ausführen (Kategorie-Umschaltung, Vor-/Zurück, Paginierung)
      if (res.ui_action || (res.tool_call && ['navigate_section', 'paginate', 'ui_action'].includes(res.tool_call.name))) {
        executeDashboardUiAction(res);
      }

    } catch (err) {
      appendGeminiLog('error', `Verbindungsfehler STT: ${err.message}`);
      updateGeminiConnBadge('FEHLER', 'var(--c-red)', '#fff');
      if (turnInd) turnInd.textContent = 'FEHLER // NETZWERK';
      playLcarsBeep(440, 220);
    }
  }

  function startLiveRecognition() {
    cactusIsLiveListening = true;
    startGeminiAudioCapture();
    const turnInd = document.getElementById('geminiLiveTurnIndicator');
    if (turnInd && !isModelSpeaking) turnInd.textContent = 'LIVE // ZUHÖREN (WHISPER BASE)...';
    updateGeminiConnBadge('LIVE // ZUHÖREN', '#44dd88', '#000');
  }

  function stopLiveRecognition() {
    cactusIsLiveListening = false;
    if (cactusMediaRecorder && cactusMediaRecorder.state !== 'inactive') {
      try { cactusMediaRecorder.stop(); } catch (e) {}
    }
    cactusIsRecording = false;
  }

  // ==========================================================================
  // PUSH-TO-TALK LISTENER (TASTE & LEERTASTE) VIA MEDIARECORDER
  // ==========================================================================
  async function startCactusPttRecording() {
    if (isPttActive) return;
    if (isModelSpeaking || ('speechSynthesis' in window && window.speechSynthesis.speaking)) {
      stopAllGeminiAudio();
    }

    isPttActive = true;
    const pttBtn = document.getElementById('btnGeminiPttAction');
    if (pttBtn) {
      pttBtn.style.background = 'var(--c-primary)';
      pttBtn.style.color = '#000';
      pttBtn.style.borderColor = 'var(--c-primary)';
      pttBtn.textContent = '🔴 SPRECHEN... (AUFNAHME AKTIV)';
    }
    const turnInd = document.getElementById('geminiLiveTurnIndicator');
    if (turnInd) turnInd.textContent = 'COMMANDER SPRICHT (PTT)...';
    updateGeminiConnBadge('AUFNAHME...', 'var(--c-primary)', '#000');
    playLcarsBeep(880, 1760);

    const stream = await startGeminiAudioCapture();
    if (!stream) {
      appendGeminiLog('error', 'Mikrofon nicht verfügbar oder Zugriff verweigert.');
      stopCactusPttRecording();
      return;
    }

    try {
      const mime = getSupportedAudioMimeType();
      cactusAudioChunks = [];
      cactusMediaRecorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
      cactusMediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) cactusAudioChunks.push(e.data);
      };
      cactusMediaRecorder.onstop = () => {
        const audioBlob = new Blob(cactusAudioChunks, { type: cactusMediaRecorder.mimeType || 'audio/webm' });
        cactusAudioChunks = [];
        sendAudioToWhisper(audioBlob);
      };
      cactusMediaRecorder.start(100);
      cactusIsRecording = true;
    } catch (err) {
      console.warn('[PTT] MediaRecorder Start Fehler:', err);
    }
  }

  function stopCactusPttRecording() {
    if (!isPttActive) return;
    isPttActive = false;

    const pttBtn = document.getElementById('btnGeminiPttAction');
    if (pttBtn) {
      pttBtn.style.background = 'rgba(186,164,229,0.15)';
      pttBtn.style.color = 'var(--c-secondary)';
      pttBtn.style.borderColor = 'var(--c-secondary)';
      pttBtn.textContent = '🎙️ SPRECHEN (GEDRÜCKT HALTEN / LEERTASTE)';
    }
    playLcarsBeep(1200, 880);

    if (cactusMediaRecorder && cactusMediaRecorder.state !== 'inactive') {
      try {
        cactusMediaRecorder.stop();
      } catch (err) {}
      cactusIsRecording = false;
    }
  }

  function setupGeminiPttListeners() {
    const pttBtn = document.getElementById('btnGeminiPttAction');
    if (!pttBtn || pttBtn.dataset.bound === 'true') return;
    pttBtn.dataset.bound = 'true';

    const startPtt = (e) => {
      if (e) e.preventDefault();
      if (geminiLiveMode !== 'ptt' || isPttActive) return;
      startCactusPttRecording();
    };

    const stopPtt = (e) => {
      if (e) e.preventDefault();
      if (!isPttActive) return;
      stopCactusPttRecording();
    };

    pttBtn.addEventListener('mousedown', startPtt);
    pttBtn.addEventListener('mouseup', stopPtt);
    pttBtn.addEventListener('mouseleave', stopPtt);

    pttBtn.addEventListener('touchstart', startPtt, { passive: false });
    pttBtn.addEventListener('touchend', stopPtt, { passive: false });
    pttBtn.addEventListener('touchcancel', stopPtt, { passive: false });

    window.addEventListener('keydown', (e) => {
      if (e.code === 'Space' && currentCategory === 'gemini_live' && geminiLiveMode === 'ptt') {
        const activeTag = document.activeElement ? document.activeElement.tagName : '';
        if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;
        if (!e.repeat) {
          startPtt(e);
        }
      }
    });

    window.addEventListener('keyup', (e) => {
      if (e.code === 'Space' && currentCategory === 'gemini_live' && geminiLiveMode === 'ptt') {
        const activeTag = document.activeElement ? document.activeElement.tagName : '';
        if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;
        stopPtt(e);
      }
    });
  }

  // ==========================================================================
  // WEB AUDIO API MIKROFON & VISUALIZER
  // ==========================================================================
  async function startGeminiAudioCapture() {
    if (geminiAudioStream) return geminiAudioStream;
    try {
      const audioCtx = getAudioCtx();
      if (audioCtx.state === 'suspended') await audioCtx.resume();

      geminiAudioStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: false,
          autoGainControl: true
        }
      });

      geminiAudioSourceNode = audioCtx.createMediaStreamSource(geminiAudioStream);
      geminiInputAnalyser = audioCtx.createAnalyser();
      geminiInputAnalyser.fftSize = 64;
      geminiAudioSourceNode.connect(geminiInputAnalyser);

      startGeminiVisualizer();
      return geminiAudioStream;
    } catch (err) {
      console.warn('Mikrofon-Zugriff via Web Audio API nicht verfügbar:', err);
      return null;
    }
  }

  function stopGeminiAudioCapture() {
    if (geminiAudioSourceNode) {
      try { geminiAudioSourceNode.disconnect(); } catch (e) {}
      geminiAudioSourceNode = null;
    }
    if (geminiAudioStream) {
      try { geminiAudioStream.getTracks().forEach(t => t.stop()); } catch (e) {}
      geminiAudioStream = null;
    }
    geminiInputAnalyser = null;
    stopGeminiVisualizer();
  }

  function startGeminiVisualizer() {
    if (geminiVizAnimId) return;

    function renderFrame() {
      if (currentCategory !== 'gemini_live') {
        geminiVizAnimId = requestAnimationFrame(renderFrame);
        return;
      }

      let inLevel = 0;
      if (geminiInputAnalyser && !geminiLiveMuted && (geminiLiveMode === 'live' || isPttActive)) {
        const data = new Uint8Array(geminiInputAnalyser.frequencyBinCount);
        geminiInputAnalyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        inLevel = Math.min(100, Math.round((sum / data.length / 255) * 160));
        renderGeminiInputMeterOnly(inLevel);

        // VAD handling for Live Mode
        if (geminiLiveMode === 'live' && cactusIsLiveListening && !isModelSpeaking) {
          const now = Date.now();
          if (inLevel > 18) {
            cactusLastVoiceTime = now;
            if (!cactusIsRecording && geminiAudioStream) {
              try {
                const mime = getSupportedAudioMimeType();
                cactusAudioChunks = [];
                cactusMediaRecorder = new MediaRecorder(geminiAudioStream, mime ? { mimeType: mime } : {});
                cactusMediaRecorder.ondataavailable = (e) => {
                  if (e.data && e.data.size > 0) cactusAudioChunks.push(e.data);
                };
                cactusMediaRecorder.onstop = () => {
                  const blob = new Blob(cactusAudioChunks, { type: cactusMediaRecorder.mimeType || 'audio/webm' });
                  cactusAudioChunks = [];
                  sendAudioToWhisper(blob);
                };
                cactusMediaRecorder.start(100);
                cactusIsRecording = true;
                const turnInd = document.getElementById('geminiLiveTurnIndicator');
                if (turnInd) turnInd.textContent = 'SPRACHE ERKANNT // AUFNAHME...';
                updateGeminiConnBadge('SPRICHT...', 'var(--c-primary)', '#000');
              } catch (e) {
                console.warn('[LIVE VAD] Recorder Start Fehler:', e);
              }
            }
          } else if (cactusIsRecording && (now - cactusLastVoiceTime > 1200)) {
            cactusIsRecording = false;
            if (cactusMediaRecorder && cactusMediaRecorder.state !== 'inactive') {
              try { cactusMediaRecorder.stop(); } catch (e) {}
            }
          }
        }
      }

      geminiVizAnimId = requestAnimationFrame(renderFrame);
    }

    geminiVizAnimId = requestAnimationFrame(renderFrame);
  }

  function stopGeminiVisualizer() {
    if (geminiVizAnimId) {
      cancelAnimationFrame(geminiVizAnimId);
      geminiVizAnimId = null;
    }
    renderGeminiInputMeterOnly(0);
  }

  function stopAllGeminiAudio() {
    if ('speechSynthesis' in window) {
      try { window.speechSynthesis.cancel(); } catch (e) {}
    }
    stopCactusTtsMeter();
    geminiActiveAudioSources.forEach(src => {
      try { src.stop(); } catch (e) {}
    });
    geminiActiveAudioSources = [];
    isModelSpeaking = false;
    const audioCtx = getAudioCtx();
    if (audioCtx) geminiNextPlaybackTime = audioCtx.currentTime;
  }

  // ==========================================================================
  // TRANSKRIPT & SUBRAUM LOGBUCH
  // ==========================================================================
  function appendGeminiLog(role, text) {
    if (!text && text !== '') return;
    const box = document.getElementById('geminiLiveTranscript');
    if (!box) return;

    const timeStr = new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const row = document.createElement('div');
    row.style.wordBreak = 'break-word';

    if (role === 'user') {
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-primary); font-weight:700;">[BENUTZER]:</span> <span style="color:#fff;">${escapeHtml(text)}</span>`;
    } else if (role === 'cactus') {
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-gold); font-weight:700;">[CACTUS ENGINE]:</span> <span style="color:#ffe066; font-family:var(--mono-family); font-size:0.82rem;">${escapeHtml(text)}</span>`;
    } else if (role === 'model' || role === 'computer') {
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-secondary); font-weight:700;">[COMPUTER]:</span> <span style="color:#e0e8ff;">${escapeHtml(text)}</span>`;
    } else if (role === 'error') {
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-red); font-weight:700;">[FEHLER]:</span> <span style="color:var(--c-red);">${escapeHtml(text)}</span>`;
    } else {
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-gold);">[SYSTEM]:</span> <span style="color:#ccc;">${escapeHtml(text)}</span>`;
    }

    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }

  function clearGeminiLiveTranscript() {
    playLcarsBeep(880, 440);
    const box = document.getElementById('geminiLiveTranscript');
    if (box) {
      box.innerHTML = '<div style="color:#666; font-style:italic;">[SYSTEM] Subraum-Logbuch zurückgesetzt.</div>';
    }
  }

  // ==========================================================================
  // DEV-TEAM KANBAN WORKFLOW CONTROLLER
  // ==========================================================================
  var lastDevteamTasksFingerprint = '';
  var lastDevteamStatsFingerprint = '';
  var devteamRefreshInterval = null;
  var isDevteamDispatching = false;
  var isDevteamCreatingTask = false;

  function getDevteamAssigneeColor(assignee) {
    if (!assignee) return 'var(--c-primary)';
    const a = String(assignee).toLowerCase();
    if (a === 'coder') return 'var(--c-blue)';
    if (a === 'qa') return 'var(--c-gold)';
    if (a === 'reviewer') return 'var(--c-red)';
    return 'var(--c-secondary)';
  }

  function getDevteamAssigneeBg(assignee) {
    if (!assignee) return 'rgba(235, 148, 58, 0.15)';
    const a = String(assignee).toLowerCase();
    if (a === 'coder') return 'rgba(136, 153, 255, 0.18)';
    if (a === 'qa') return 'rgba(255, 204, 102, 0.18)';
    if (a === 'reviewer') return 'rgba(207, 79, 79, 0.18)';
    return 'rgba(186, 164, 229, 0.18)';
  }

  function getDevteamStatusDotColor(status) {
    if (!status) return '#888888';
    const s = String(status).toLowerCase();
    if (s === 'done' || s === 'completed') return '#00e676';
    if (s === 'running') return '#00d2ff';
    if (s === 'blocked') return '#ff5252';
    if (s === 'ready') return '#ea9c72';
    if (s === 'triage') return '#baa4e5';
    return '#888888';
  }

  function formatDevteamTimestamp(ts) {
    if (!ts) return '--';
    try {
      const d = new Date(Number(ts) * 1000);
      if (isNaN(d.getTime())) return String(ts);
      return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) + ' ' +
             d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch (e) {
      return String(ts);
    }
  }

  async function fetchDevteamData(force) {
    try {
      const [tasksRes, statsRes] = await Promise.all([
        fetch('/api/devteam/tasks'),
        fetch('/api/devteam/stats')
      ]);

      if (statsRes.ok) {
        const stats = await statsRes.json();
        const statsFp = `${stats.total}:${stats.running}:${stats.blocked}:${stats.done}`;
        if (statsFp !== lastDevteamStatsFingerprint || force) {
          lastDevteamStatsFingerprint = statsFp;
          renderDevteamStats(stats);
        }
      }

      if (tasksRes.ok) {
        const tasks = await tasksRes.json();
        const tasksFp = Array.isArray(tasks) ? tasks.map(t => `${t.id}:${t.status}:${t.assignee}:${t.title}:${t.started_at || ''}:${t.completed_at || ''}`).join('|') : '';
        if (tasksFp !== lastDevteamTasksFingerprint || force) {
          lastDevteamTasksFingerprint = tasksFp;
          renderDevteamBoard(Array.isArray(tasks) ? tasks : []);
        }
      }

      const syncEl = document.getElementById('devteamLastSyncText');
      if (syncEl) {
        const now = new Date();
        syncEl.textContent = 'SYNC: ' + now.toLocaleTimeString('de-DE');
      }
    } catch (e) {
      console.warn('Fehler beim Laden der DEV-TEAM Daten:', e);
    }
  }

  function renderDevteamStats(stats) {
    const totalEl = document.getElementById('devteamStatTotal');
    const runningEl = document.getElementById('devteamStatRunning');
    const blockedEl = document.getElementById('devteamStatBlocked');
    const doneEl = document.getElementById('devteamStatDone');
    const blockedBadge = document.getElementById('devteamStatBlockedBadge');

    if (totalEl) totalEl.textContent = stats.total ?? 0;
    if (runningEl) runningEl.textContent = stats.running ?? 0;
    if (blockedEl) blockedEl.textContent = stats.blocked ?? 0;
    if (doneEl) doneEl.textContent = stats.done ?? 0;

    if (blockedBadge) {
      if ((stats.blocked || 0) > 0) {
        blockedBadge.textContent = '⚠️ ' + stats.blocked + ' BLOCKIERT';
        blockedBadge.style.background = 'var(--c-red)';
        blockedBadge.style.color = '#ffffff';
      } else {
        blockedBadge.textContent = 'KEINE BLOCKER';
        blockedBadge.style.background = 'rgba(255,255,255,0.08)';
        blockedBadge.style.color = '#aaaaaa';
      }
    }
  }

  function renderDevteamBoard(tasks) {
    const columns = {
      'triage': document.getElementById('devteamCol-triage'),
      'ready': document.getElementById('devteamCol-ready'),
      'running': document.getElementById('devteamCol-running'),
      'blocked': document.getElementById('devteamCol-blocked'),
      'done': document.getElementById('devteamCol-done')
    };

    const counts = { 'triage': 0, 'ready': 0, 'running': 0, 'blocked': 0, 'done': 0 };
    const htmls = { 'triage': '', 'ready': '', 'running': '', 'blocked': '', 'done': '' };

    tasks.forEach(task => {
      let colKey = (task.status || '').toLowerCase();
      if (colKey === 'todo' || colKey === 'scheduled') {
        colKey = 'triage';
      }
      if (!columns[colKey]) {
        colKey = 'triage';
      }

      counts[colKey] = (counts[colKey] || 0) + 1;

      const aColor = getDevteamAssigneeColor(task.assignee);
      const aBg = getDevteamAssigneeBg(task.assignee);
      const dotColor = getDevteamStatusDotColor(task.status);
      const isRunning = (task.status === 'running');

      htmls[colKey] += `
        <div class="devteam-task-card" onclick="openDevteamTaskModal('${escapeHtml(task.id)}')"
             style="background:#1a1a2e; border:1px solid rgba(255,255,255,0.08); border-left:5px solid ${aColor}; border-radius:0 8px 8px 0; padding:0.75rem; cursor:pointer; transition:transform 0.15s, border-color 0.15s; display:flex; flex-direction:column; gap:0.4rem;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-family:var(--mono-family); font-size:0.75rem; color:#888; font-weight:700;">#${escapeHtml(task.id)}</span>
            <div style="display:flex; align-items:center; gap:6px;">
              <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${dotColor}; box-shadow:0 0 6px ${dotColor}; ${isRunning ? 'animation:lcarsPulse 1.2s infinite ease-in-out;' : ''}"></span>
              <span style="font-size:0.72rem; font-family:var(--mono-family); color:#aaa; text-transform:uppercase;">${escapeHtml(task.status)}</span>
            </div>
          </div>
          <div style="font-weight:700; font-size:0.88rem; color:#fff; line-height:1.3; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;">
            ${escapeHtml(task.title)}
          </div>
          <div style="display:flex; justify-content:space-between; align-items:center; margin-top:0.25rem;">
            <span style="display:inline-block; font-size:0.72rem; font-weight:700; font-family:var(--mono-family); text-transform:uppercase; padding:0.15rem 0.5rem; border-radius:4px; background:${aBg}; color:${aColor}; border:1px solid ${aColor}44;">
              ${escapeHtml(task.assignee || 'UNASSIGNED')}
            </span>
            ${task.completed_at ? `<span style="font-size:0.7rem; font-family:var(--mono-family); color:#00e676;">✓ DONE</span>` : (task.started_at ? `<span style="font-size:0.7rem; font-family:var(--mono-family); color:var(--c-blue);">⚡ AKTIV</span>` : '')}
          </div>
        </div>
      `;
    });

    Object.keys(columns).forEach(key => {
      const colEl = columns[key];
      const countEl = document.getElementById('devteamColCount-' + key);
      if (countEl) countEl.textContent = counts[key] || 0;
      if (colEl) {
        if (counts[key] === 0) {
          colEl.innerHTML = '<div style="text-align:center; padding:1.5rem 0.5rem; color:#666; font-size:0.8rem; font-style:italic;">Keine Tasks</div>';
        } else {
          colEl.innerHTML = htmls[key];
        }
      }
    });
  }

  async function openDevteamTaskModal(taskId) {
    playLcarsBeep(880, 1400);
    const modal = document.getElementById('devteamDetailModal');
    const modalTitle = document.getElementById('devteamDetailModalTitle');
    const modalBody = document.getElementById('devteamDetailModalBody');
    if (!modal || !modalBody) return;

    if (modalTitle) modalTitle.textContent = 'TASK DETAIL // #' + taskId;
    modalBody.innerHTML = '<div style="padding:2rem; text-align:center; color:#aaa; font-family:var(--mono-family);">Lade Task Details...</div>';
    modal.style.display = 'flex';

    try {
      const resp = await fetch('/api/devteam/task/' + encodeURIComponent(taskId));
      if (!resp.ok) {
        modalBody.innerHTML = '<div style="padding:2rem; color:var(--c-red); font-family:var(--mono-family);">Fehler beim Laden des Tasks (Status ' + resp.status + ')</div>';
        return;
      }
      const t = await resp.json();
      const aColor = getDevteamAssigneeColor(t.assignee);
      const aBg = getDevteamAssigneeBg(t.assignee);
      const dotColor = getDevteamStatusDotColor(t.status);

      let eventsHtml = '<div style="color:#777; font-size:0.8rem; font-style:italic;">Keine Events vorhanden</div>';
      if (Array.isArray(t.events) && t.events.length > 0) {
        eventsHtml = '<div style="display:flex; flex-direction:column; gap:0.4rem;">';
        t.events.forEach(ev => {
          let payloadStr = '';
          if (ev.payload) {
            try {
              const p = typeof ev.payload === 'string' ? JSON.parse(ev.payload) : ev.payload;
              if (p.summary) payloadStr = p.summary;
              else if (p.error) payloadStr = 'Fehler: ' + p.error;
              else payloadStr = JSON.stringify(p);
            } catch (e) {
              payloadStr = String(ev.payload);
            }
          }
          eventsHtml += `
            <div style="background:rgba(255,255,255,0.03); border-left:3px solid var(--c-primary); border-radius:0 4px 4px 0; padding:0.4rem 0.6rem; font-family:var(--mono-family); font-size:0.78rem;">
              <div style="display:flex; justify-content:space-between; color:#aaa; margin-bottom:2px;">
                <span style="font-weight:700; color:var(--c-gold); text-transform:uppercase;">${escapeHtml(ev.kind || 'EVENT')}</span>
                <span>${formatDevteamTimestamp(ev.created_at)}</span>
              </div>
              ${payloadStr ? `<div style="color:#e0e0e0; word-break:break-word;">${escapeHtml(payloadStr)}</div>` : ''}
            </div>
          `;
        });
        eventsHtml += '</div>';
      }

      let commentsHtml = '<div style="color:#777; font-size:0.8rem; font-style:italic;">Keine Kommentare vorhanden</div>';
      if (Array.isArray(t.comments) && t.comments.length > 0) {
        commentsHtml = '<div style="display:flex; flex-direction:column; gap:0.5rem;">';
        t.comments.forEach(cm => {
          commentsHtml += `
            <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:4px; padding:0.6rem;">
              <div style="display:flex; justify-content:space-between; margin-bottom:0.3rem; font-family:var(--mono-family); font-size:0.78rem; color:var(--c-blue);">
                <span style="font-weight:700;">${escapeHtml(cm.author || 'ANONYM')}</span>
                <span style="color:#888;">${formatDevteamTimestamp(cm.created_at)}</span>
              </div>
              <div style="font-size:0.85rem; color:#e0e0e0; white-space:pre-wrap; word-break:break-word;">${escapeHtml(cm.body || '')}</div>
            </div>
          `;
        });
        commentsHtml += '</div>';
      }

      modalBody.innerHTML = `
        <div style="margin-bottom:1.25rem;">
          <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.75rem;">
            <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
              <span style="display:inline-block; font-size:0.8rem; font-weight:700; font-family:var(--mono-family); text-transform:uppercase; padding:0.2rem 0.6rem; border-radius:4px; background:${aBg}; color:${aColor}; border:1px solid ${aColor}44;">
                ${escapeHtml(t.assignee || 'UNASSIGNED')}
              </span>
              <span style="display:inline-flex; align-items:center; gap:6px; font-size:0.8rem; font-family:var(--mono-family); text-transform:uppercase; padding:0.2rem 0.6rem; border-radius:4px; background:rgba(255,255,255,0.06); color:#fff;">
                <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${dotColor};"></span>
                ${escapeHtml(t.status)}
              </span>
              <span style="font-size:0.8rem; font-family:var(--mono-family); color:#888;">PRIO: ${t.priority ?? 0}</span>
            </div>
            <span style="font-family:var(--mono-family); font-size:0.8rem; color:#888;">ID: #${escapeHtml(t.id)}</span>
          </div>

          <h3 style="color:#fff; font-size:1.25rem; font-weight:700; margin:0.5rem 0 1rem 0; line-height:1.35;">
            ${escapeHtml(t.title)}
          </h3>

          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:0.6rem; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:0.75rem; margin-bottom:1.25rem; font-family:var(--mono-family); font-size:0.78rem;">
            <div><span style="color:#888;">Erstellt:</span> <span style="color:#ddd;">${formatDevteamTimestamp(t.created_at)}</span></div>
            <div><span style="color:#888;">Gestartet:</span> <span style="color:#ddd;">${formatDevteamTimestamp(t.started_at)}</span></div>
            <div><span style="color:#888;">Beendet:</span> <span style="color:#ddd;">${formatDevteamTimestamp(t.completed_at)}</span></div>
            <div style="grid-column:1 / -1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(t.workspace || '')}">
              <span style="color:#888;">Workspace:</span> <span style="color:var(--c-blue);">${escapeHtml(t.workspace || '--')}</span>
            </div>
          </div>

          <div style="margin-bottom:1.25rem;">
            <div style="font-size:0.85rem; font-weight:700; color:var(--c-secondary); margin-bottom:0.4rem; letter-spacing:0.05em;">BESCHREIBUNG</div>
            <div style="background:#07070b; border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.85rem; font-family:var(--mono-family); font-size:0.82rem; line-height:1.55; white-space:pre-wrap; word-break:break-word; color:#e0e0e0; max-height:220px; overflow-y:auto;">
              ${escapeHtml(t.body || 'Keine Beschreibung vorhanden.')}
            </div>
          </div>

          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:1rem;">
            <div>
              <div style="font-size:0.85rem; font-weight:700; color:var(--c-gold); margin-bottom:0.4rem; letter-spacing:0.05em;">TIMELINE // EVENTS (LETZTE 10)</div>
              <div style="max-height:240px; overflow-y:auto; padding-right:4px;">
                ${eventsHtml}
              </div>
            </div>
            <div>
              <div style="font-size:0.85rem; font-weight:700; color:var(--c-blue); margin-bottom:0.4rem; letter-spacing:0.05em;">KOMMENTARE</div>
              <div style="max-height:240px; overflow-y:auto; padding-right:4px;">
                ${commentsHtml}
              </div>
            </div>
          </div>
        </div>
      `;
    } catch (e) {
      modalBody.innerHTML = '<div style="padding:2rem; color:var(--c-red); font-family:var(--mono-family);">Fehler beim Laden: ' + escapeHtml(String(e)) + '</div>';
    }
  }

  function closeDevteamDetailModal() {
    playLcarsBeep(660, 440);
    const modal = document.getElementById('devteamDetailModal');
    if (modal) modal.style.display = 'none';
  }

  function handleDevteamDetailModalBackdrop(e) {
    if (e.target && e.target.id === 'devteamDetailModal') {
      closeDevteamDetailModal();
    }
  }

  function openDevteamNewTaskModal() {
    playLcarsBeep(880, 1400);
    const form = document.getElementById('devteamNewTaskForm');
    if (form) form.reset();
    const modal = document.getElementById('devteamNewTaskModal');
    if (modal) modal.style.display = 'flex';
    const titleInput = document.getElementById('devteamFormTitle');
    if (titleInput) titleInput.focus();
  }

  function closeDevteamNewTaskModal() {
    playLcarsBeep(660, 440);
    const modal = document.getElementById('devteamNewTaskModal');
    if (modal) modal.style.display = 'none';
  }

  function handleDevteamNewTaskModalBackdrop(e) {
    if (e.target && e.target.id === 'devteamNewTaskModal') {
      closeDevteamNewTaskModal();
    }
  }

  async function submitDevteamNewTask(e) {
    if (e) e.preventDefault();
    if (isDevteamCreatingTask) return;

    const titleEl = document.getElementById('devteamFormTitle');
    const bodyEl = document.getElementById('devteamFormBody');
    const assigneeEl = document.getElementById('devteamFormAssignee');
    const submitBtn = document.getElementById('btnDevteamSubmitTask');
    const spinner = document.getElementById('devteamSubmitTaskSpinner');

    const title = titleEl ? titleEl.value.trim() : '';
    if (!title) {
      alert('Bitte geben Sie einen Task-Titel ein.');
      return;
    }
    const body = bodyEl ? bodyEl.value.trim() : '';
    const assignee = assigneeEl ? assigneeEl.value.trim() : 'coder';

    isDevteamCreatingTask = true;
    if (submitBtn) submitBtn.disabled = true;
    if (spinner) spinner.style.display = 'inline-block';

    try {
      const resp = await fetch('/api/devteam/task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, body, assignee })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsBeep(1200, 1600);
        closeDevteamNewTaskModal();
        await fetchDevteamData(true);
      } else {
        alert('Fehler beim Erstellen des Tasks: ' + (data.error || 'Unbekannter Fehler'));
      }
    } catch (err) {
      alert('Netzwerkfehler beim Erstellen: ' + err);
    } finally {
      isDevteamCreatingTask = false;
      if (submitBtn) submitBtn.disabled = false;
      if (spinner) spinner.style.display = 'none';
    }
  }

  async function triggerDevteamDispatch() {
    if (isDevteamDispatching) return;
    playLcarsBeep(980, 1400);

    const btn = document.getElementById('btnDevteamDispatch');
    const spinner = document.getElementById('devteamDispatchSpinner');
    const icon = document.getElementById('devteamDispatchIcon');
    const text = document.getElementById('devteamDispatchText');

    isDevteamDispatching = true;
    if (btn) btn.disabled = true;
    if (spinner) spinner.style.display = 'inline-block';
    if (icon) icon.style.display = 'none';
    if (text) text.textContent = 'DISPATCHING...';

    try {
      const resp = await fetch('/api/devteam/dispatch', { method: 'POST' });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsBeep(1200, 1600);
      } else {
        alert('Dispatch Fehler: ' + (data.error || 'Unbekannter Fehler'));
      }
      await fetchDevteamData(true);
    } catch (e) {
      alert('Fehler beim Dispatch: ' + e);
    } finally {
      isDevteamDispatching = false;
      if (btn) btn.disabled = false;
      if (spinner) spinner.style.display = 'none';
      if (icon) icon.style.display = 'inline-block';
      if (text) text.textContent = 'DISPATCH';
    }
  }

  function startDevteamAutoRefresh() {
    if (devteamRefreshInterval) clearInterval(devteamRefreshInterval);
    devteamRefreshInterval = setInterval(() => {
      if (currentCategory === 'devteam') {
        fetchDevteamData(false);
      }
    }, 30000);
  }

  // -------------------------------------------------------------------------
  // KNOWLEDGE BASE (WISSEN) CONTROLLER & MARKDOWN RENDERER
  // -------------------------------------------------------------------------
  let currentKnowledgeCategory = 'all';
  let currentKnowledgeSearch = '';
  let activeKnowledgeArticles = [];
  let currentKnowledgeArticleId = null;
  let knowledgeSearchTimeout = null;
  let knowledgeRefreshInterval = null;

  function renderLcarsMarkdown(md) {
    if (!md) return '';
    let text = String(md);

    // Escape raw HTML first
    text = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

    // Code blocks: ```lang ... ```
    text = text.replace(/```([a-zA-Z0-9_-]*)\\n?([\\s\\S]*?)```/g, function(match, lang, code) {
      return '<pre style="background:#0a0a14; border:1px solid rgba(255,255,255,0.15); border-left:4px solid var(--c-secondary); border-radius:4px; padding:0.8rem; overflow-x:auto; font-family:var(--mono-family); font-size:0.85rem; color:#44dd88; margin:0.8rem 0;"><code>' + code.trim() + '</code></pre>';
    });

    // Inline code: `...`
    text = text.replace(/`([^`]+)`/g, '<code style="background:rgba(255,255,255,0.08); padding:0.15rem 0.35rem; border-radius:3px; font-family:var(--mono-family); color:#ffcc66; font-size:0.88em;">$1</code>');

    // Headings
    text = text.replace(/^### (.*$)/gim, '<h4 style="color:var(--c-gold); font-size:1rem; margin:1rem 0 0.4rem 0; font-family:var(--font-family); text-transform:uppercase;">$1</h4>');
    text = text.replace(/^## (.*$)/gim, '<h3 style="color:var(--c-primary); font-size:1.15rem; margin:1.2rem 0 0.5rem 0; font-family:var(--font-family); text-transform:uppercase; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:0.3rem;">$1</h3>');
    text = text.replace(/^# (.*$)/gim, '<h2 style="color:var(--c-secondary); font-size:1.35rem; margin:1.2rem 0 0.6rem 0; font-family:var(--font-family); text-transform:uppercase; border-bottom:2px solid var(--c-secondary); padding-bottom:0.4rem;">$1</h2>');

    // Bold & Italic
    text = text.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong style="color:#fff; font-weight:700;">$1</strong>');
    text = text.replace(/\\*([^*]+)\\*/g, '<em style="color:#ddd;">$1</em>');

    // Blockquotes
    text = text.replace(/^> (.*$)/gim, '<blockquote style="border-left:3px solid var(--c-gold); margin:0.6rem 0; padding:0.4rem 0.8rem; background:rgba(255,255,255,0.02); color:#bbb; font-style:italic;">$1</blockquote>');

    // Horizontal Rule
    text = text.replace(/^---$/gim, '<hr style="border:none; border-top:1px solid rgba(255,255,255,0.15); margin:1rem 0;">');

    // Unordered lists (- or *)
    text = text.replace(/^\\s*[-*]\\s+(.*$)/gim, '<li style="margin-left:1.4rem; list-style-type:square; color:#ddd; margin-bottom:0.25rem;">$1</li>');

    // Ordered lists (1. )
    text = text.replace(/^\\s*\\d+\\.\\s+(.*$)/gim, '<li style="margin-left:1.4rem; list-style-type:decimal; color:#ddd; margin-bottom:0.25rem;">$1</li>');

    // Paragraph linebreaks (convert double newlines to p, single to br when not inside lists/blocks)
    const lines = text.split(String.fromCharCode(10));
    let inList = false;
    let html = '';
    for (let i = 0; i < lines.length; i++) {
      let l = lines[i];
      if (l.startsWith('<li')) {
        if (!inList) { html += '<ul style="margin:0.5rem 0; padding:0;">'; inList = true; }
        html += l;
      } else {
        if (inList) { html += '</ul>'; inList = false; }
        if (l.trim() === '') {
          html += '<div style="height:0.6rem;"></div>';
        } else if (l.startsWith('<h') || l.startsWith('<pre') || l.startsWith('<blockquote') || l.startsWith('<hr')) {
          html += l;
        } else {
          html += '<p style="margin:0 0 0.4rem 0; line-height:1.5; color:#ccc;">' + l + '</p>';
        }
      }
    }
    if (inList) html += '</ul>';

    return html;
  }

  function getKnowledgeCategoryBadge(cat) {
    const c = (cat || 'allgemein').toLowerCase();
    switch (c) {
      case 'qa':
        return { label: 'QA-REPORT', bg: '#0099ff', color: '#fff' };
      case 'review':
        return { label: 'CODE-REVIEW', bg: 'var(--c-butterscotch)', color: '#000' };
      case 'architecture':
      case 'adr':
        return { label: 'ADR', bg: '#44dd88', color: '#000' };
      case 'runbook':
        return { label: 'RUNBOOK', bg: 'var(--c-gold)', color: '#000' };
      default:
        return { label: c.toUpperCase(), bg: 'var(--c-secondary)', color: '#000' };
    }
  }

  async function fetchKnowledgeData(force = false) {
    try {
      let url = '/api/knowledge?limit=100';
      if (currentKnowledgeCategory && currentKnowledgeCategory !== 'all') {
        url += '&category=' + encodeURIComponent(currentKnowledgeCategory);
      }
      if (currentKnowledgeSearch) {
        url += '&q=' + encodeURIComponent(currentKnowledgeSearch);
      }

      const [listRes, statsRes] = await Promise.all([
        fetch(url),
        fetch('/api/knowledge/stats')
      ]);

      if (statsRes.ok) {
        const stats = await statsRes.json();
        renderKnowledgeStats(stats);
      }

      if (listRes.ok) {
        const articles = await listRes.json();
        activeKnowledgeArticles = Array.isArray(articles) ? articles : [];
        renderKnowledgeArticles(activeKnowledgeArticles);
      }

      const syncEl = document.getElementById('knowledgeLastSyncText');
      if (syncEl) {
        const now = new Date();
        syncEl.textContent = 'SYNC: ' + now.toLocaleTimeString('de-DE');
      }
    } catch (e) {
      console.warn('Fehler beim Laden der Knowledge-Base Daten:', e);
    }
  }

  function renderKnowledgeStats(stats) {
    const totEl = document.getElementById('knowledgeStatTotal');
    const qaEl = document.getElementById('knowledgeStatQa');
    const revEl = document.getElementById('knowledgeStatReview');
    const adrEl = document.getElementById('knowledgeStatAdr');

    const byCat = stats?.by_category || {};
    if (totEl) totEl.textContent = stats?.total ?? 0;
    if (qaEl) qaEl.textContent = byCat['qa'] || 0;
    if (revEl) revEl.textContent = byCat['review'] || 0;
    if (adrEl) adrEl.textContent = (byCat['architecture'] || 0) + (byCat['adr'] || 0);
  }

  function renderKnowledgeArticles(articles) {
    const listEl = document.getElementById('knowledgeArticleList');
    if (!listEl) return;

    if (!articles || articles.length === 0) {
      listEl.innerHTML = '<div style="text-align:center; padding:3rem 1rem; color:#777; font-family:var(--mono-family); font-style:italic;">Keine Wissensartikel gefunden. Erstellen Sie einen Eintrag oder senden Sie Testberichte via API.</div>';
      return;
    }

    let html = '';
    articles.forEach(art => {
      const badge = getKnowledgeCategoryBadge(art.category);
      const tags = Array.isArray(art.tags) ? art.tags : [];
      const tagsHtml = tags.map(t => `<span style="background:rgba(255,255,255,0.06); color:#aaa; padding:0.1rem 0.4rem; border-radius:3px; font-size:0.7rem; font-family:var(--mono-family);">#${escapeHtml(t)}</span>`).join(' ');
      const dateStr = art.updated_at ? new Date(art.updated_at * 1000).toLocaleString('de-DE') : '--';
      const summaryText = art.summary || art.snippet || '';

      html += `
        <div class="devteam-task-card" onclick="openKnowledgeArticleDetail('${escapeHtml(art.id)}')"
             style="background:#141424; border:1px solid rgba(255,255,255,0.08); border-left:5px solid ${badge.bg}; border-radius:0 8px 8px 0; padding:1rem; cursor:pointer; transition:transform 0.15s, border-color 0.15s; display:flex; flex-direction:column; gap:0.5rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
            <div style="display:flex; align-items:center; gap:0.6rem;">
              <span style="display:inline-block; font-size:0.72rem; font-weight:700; font-family:var(--mono-family); text-transform:uppercase; padding:0.15rem 0.55rem; border-radius:4px; background:${badge.bg}; color:${badge.color};">
                ${badge.label}
              </span>
              <span style="font-family:var(--mono-family); font-size:0.75rem; color:#888;">ID: ${escapeHtml(art.id)}</span>
            </div>
            <div style="font-size:0.75rem; font-family:var(--mono-family); color:#aaa;">
              <span>👤 ${escapeHtml(art.author || 'system')}</span> &bull; <span>🕒 ${dateStr}</span>
            </div>
          </div>
          <div style="font-weight:700; font-size:1.05rem; color:#fff; font-family:var(--font-family); letter-spacing:0.02em;">
            ${escapeHtml(art.title)}
          </div>
          ${summaryText ? `<div style="font-size:0.85rem; color:#bbb; line-height:1.4; max-height:2.8rem; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(summaryText)}</div>` : ''}
          ${tagsHtml ? `<div style="display:flex; gap:0.4rem; flex-wrap:wrap; margin-top:0.2rem;">${tagsHtml}</div>` : ''}
        </div>
      `;
    });

    listEl.innerHTML = html;
  }

  function filterKnowledgeCategory(cat) {
    currentKnowledgeCategory = cat;
    document.querySelectorAll('.kb-filter-btn').forEach(btn => {
      if (btn.getAttribute('data-cat') === cat) {
        btn.classList.add('active');
        btn.style.borderColor = 'var(--c-secondary)';
        btn.style.color = 'var(--c-secondary)';
        btn.style.fontWeight = '700';
      } else {
        btn.classList.remove('active');
        btn.style.borderColor = 'rgba(255,255,255,0.2)';
        btn.style.color = '#bbb';
        btn.style.fontWeight = 'normal';
      }
    });
    fetchKnowledgeData(false);
  }

  function handleKnowledgeSearchInput() {
    if (knowledgeSearchTimeout) clearTimeout(knowledgeSearchTimeout);
    knowledgeSearchTimeout = setTimeout(() => {
      const inp = document.getElementById('knowledgeSearchInput');
      currentKnowledgeSearch = (inp?.value || '').trim();
      fetchKnowledgeData(false);
    }, 250);
  }

  function clearKnowledgeSearch() {
    const inp = document.getElementById('knowledgeSearchInput');
    if (inp) inp.value = '';
    currentKnowledgeSearch = '';
    fetchKnowledgeData(false);
  }

  async function openKnowledgeArticleDetail(articleId) {
    playLcarsBeep(880, 1400);
    currentKnowledgeArticleId = articleId;
    const modal = document.getElementById('knowledgeDetailModal');
    const modalTitle = document.getElementById('knowledgeDetailModalTitle');
    const modalMeta = document.getElementById('knowledgeDetailModalMeta');
    const modalBody = document.getElementById('knowledgeDetailModalBody');
    if (!modal || !modalBody) return;

    if (modalTitle) modalTitle.textContent = 'LADE ARTIKEL...';
    modalBody.innerHTML = '<div style="padding:2rem; text-align:center; color:#aaa; font-family:var(--mono-family);">Lade Inhalt aus ODN Archiv...</div>';
    modal.style.display = 'flex';

    try {
      const resp = await fetch('/api/knowledge/' + encodeURIComponent(articleId));
      if (!resp.ok) {
        modalBody.innerHTML = '<div style="padding:2rem; color:var(--c-red); font-family:var(--mono-family);">Fehler beim Laden des Artikels (Status ' + resp.status + ')</div>';
        return;
      }
      const art = await resp.json();
      if (modalTitle) modalTitle.textContent = art.title || 'WISSENSARTIKEL';

      const badge = getKnowledgeCategoryBadge(art.category);
      const tags = Array.isArray(art.tags) ? art.tags : [];
      const tagsHtml = tags.map(t => `<span style="background:rgba(255,255,255,0.1); color:#fff; padding:0.15rem 0.45rem; border-radius:3px; font-size:0.75rem; font-family:var(--mono-family);">#${escapeHtml(t)}</span>`).join(' ');
      const dateStr = art.updated_at ? new Date(art.updated_at * 1000).toLocaleString('de-DE') : '--';

      if (modalMeta) {
        modalMeta.textContent = `KATEGORIE: ${badge.label} // AUTOR: ${art.author || 'system'} // STAND: ${dateStr}`;
      }

      let contentHtml = renderLcarsMarkdown(art.content || '*Kein Inhalt hinterlegt.*');

      let metaSection = '';
      if (art.metadata && Object.keys(art.metadata).length > 0) {
        metaSection = `
          <div style="margin-top:1.5rem; padding:0.8rem; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.08); border-radius:4px;">
            <div style="font-size:0.78rem; font-family:var(--mono-family); color:var(--c-gold); font-weight:700; text-transform:uppercase; margin-bottom:0.4rem;">METADATEN / SYSTEM-ATTRIBUTES</div>
            <pre style="margin:0; font-family:var(--mono-family); font-size:0.75rem; color:#aaa; overflow-x:auto;">${escapeHtml(JSON.stringify(art.metadata, null, 2))}</pre>
          </div>
        `;
      }

      modalBody.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:1.2rem; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:0.75rem;">
          <div style="display:flex; align-items:center; gap:0.6rem;">
            <span style="display:inline-block; font-size:0.75rem; font-weight:700; font-family:var(--mono-family); text-transform:uppercase; padding:0.2rem 0.6rem; border-radius:4px; background:${badge.bg}; color:${badge.color};">
              ${badge.label}
            </span>
            <span style="font-family:var(--mono-family); font-size:0.78rem; color:#888;">ID: ${escapeHtml(art.id)}</span>
          </div>
          <div>${tagsHtml}</div>
        </div>
        <div style="line-height:1.6; font-size:0.95rem; color:#e0e0e0;">
          ${contentHtml}
        </div>
        ${metaSection}
      `;
    } catch (e) {
      modalBody.innerHTML = '<div style="padding:2rem; color:var(--c-red); font-family:var(--mono-family);">Fehler: ' + escapeHtml(String(e)) + '</div>';
    }
  }

  function closeKnowledgeDetailModal() {
    playLcarsBeep(600, 100);
    const modal = document.getElementById('knowledgeDetailModal');
    if (modal) modal.style.display = 'none';
    currentKnowledgeArticleId = null;
  }

  function handleKnowledgeDetailModalBackdrop(event) {
    if (event.target && event.target.id === 'knowledgeDetailModal') {
      closeKnowledgeDetailModal();
    }
  }

  async function deleteCurrentKnowledgeArticle() {
    if (!currentKnowledgeArticleId) return;
    if (!confirm('Diesen Wissensartikel wirklich unwiderruflich aus dem Archiv löschen?')) return;
    try {
      const resp = await fetch('/api/knowledge/' + encodeURIComponent(currentKnowledgeArticleId), { method: 'DELETE' });
      if (resp.ok) {
        closeKnowledgeDetailModal();
        fetchKnowledgeData(true);
      } else {
        const d = await resp.json();
        alert('Fehler beim Löschen: ' + (d.error || 'Serverfehler'));
      }
    } catch (e) {
      alert('Fehler: ' + e);
    }
  }

  function openKnowledgeNewModal() {
    playLcarsBeep(980, 1400);
    const form = document.getElementById('knowledgeNewForm');
    if (form) form.reset();
    const modal = document.getElementById('knowledgeNewModal');
    if (modal) modal.style.display = 'flex';
  }

  function closeKnowledgeNewModal() {
    playLcarsBeep(600, 100);
    const modal = document.getElementById('knowledgeNewModal');
    if (modal) modal.style.display = 'none';
  }

  function handleKnowledgeNewModalBackdrop(event) {
    if (event.target && event.target.id === 'knowledgeNewModal') {
      closeKnowledgeNewModal();
    }
  }

  async function submitKnowledgeNewArticle(event) {
    event.preventDefault();
    const title = (document.getElementById('knowledgeFormTitle')?.value || '').trim();
    const category = document.getElementById('knowledgeFormCategory')?.value || 'allgemein';
    const tagsStr = document.getElementById('knowledgeFormTags')?.value || '';
    const summary = (document.getElementById('knowledgeFormSummary')?.value || '').trim();
    const content = (document.getElementById('knowledgeFormContent')?.value || '').trim();

    if (!title || !content) {
      alert('Titel und Inhalt sind Pflichtfelder.');
      return;
    }

    const tags = tagsStr.split(',').map(s => s.trim()).filter(Boolean);
    const spinner = document.getElementById('knowledgeSubmitSpinner');
    const btn = document.getElementById('btnKnowledgeSubmitArticle');
    if (spinner) spinner.style.display = 'inline-block';
    if (btn) btn.disabled = true;

    try {
      const resp = await fetch('/api/knowledge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: title,
          category: category,
          tags: tags,
          summary: summary,
          content: content,
          metadata: { created_via: 'lcars_ui' }
        })
      });

      if (resp.ok) {
        closeKnowledgeNewModal();
        await fetchKnowledgeData(true);
      } else {
        const d = await resp.json();
        alert('Fehler beim Speichern: ' + (d.error || 'Serverfehler'));
      }
    } catch (e) {
      alert('Fehler: ' + e);
    } finally {
      if (spinner) spinner.style.display = 'none';
      if (btn) btn.disabled = false;
    }
  }

  function startKnowledgeAutoRefresh() {
    if (knowledgeRefreshInterval) clearInterval(knowledgeRefreshInterval);
    knowledgeRefreshInterval = setInterval(() => {
      if (currentCategory === 'knowledge') {
        fetchKnowledgeData(false);
      }
    }, 30000);
  }

  // -------------------------------------------------------------------------
  // RESEARCH RUBRIK CONTROLLER & TAB MANAGER
  // -------------------------------------------------------------------------
  let currentResearchStatus = 'all';
  let currentResearchSearch = '';
  let activeResearchReports = [];
  let currentResearchReportId = null;
  let currentResearchReportData = null;
  let activeResearchDetailTab = 'summary';
  let researchSearchTimeout = null;
  let researchRefreshInterval = null;

  function getResearchStatusBadge(status) {
    const s = (status || 'completed').toLowerCase();
    switch (s) {
      case 'running':
      case 'laufend':
        return { label: 'LAUFEND', bg: 'var(--c-butterscotch)', color: '#000' };
      case 'planned':
      case 'geplant':
        return { label: 'GEPLANT', bg: 'var(--c-secondary)', color: '#000' };
      case 'completed':
      case 'abgeschlossen':
      default:
        return { label: 'ABGESCHLOSSEN', bg: '#44dd88', color: '#000' };
    }
  }

  async function fetchResearchData(force = false) {
    try {
      let url = '/api/research?limit=100';
      if (currentResearchStatus && currentResearchStatus !== 'all') {
        url += '&status=' + encodeURIComponent(currentResearchStatus);
      }
      if (currentResearchSearch) {
        url += '&q=' + encodeURIComponent(currentResearchSearch);
      }

      const [listRes, statsRes] = await Promise.all([
        fetch(url),
        fetch('/api/research/stats')
      ]);

      if (statsRes.ok) {
        const stats = await statsRes.json();
        renderResearchStats(stats);
      }

      if (listRes.ok) {
        const reports = await listRes.json();
        activeResearchReports = Array.isArray(reports) ? reports : [];
        renderResearchReports(activeResearchReports);
      }

      const syncEl = document.getElementById('researchLastSyncText');
      if (syncEl) {
        const now = new Date();
        syncEl.textContent = 'SYNC: ' + now.toLocaleTimeString('de-DE');
      }
    } catch (e) {
      console.warn('Fehler beim Laden der Research-Daten:', e);
    }
  }

  function renderResearchStats(stats) {
    const totEl = document.getElementById('researchStatTotal');
    const compEl = document.getElementById('researchStatCompleted');
    const runEl = document.getElementById('researchStatRunning');
    const planEl = document.getElementById('researchStatPlanned');

    const byStatus = stats?.by_status || {};
    if (totEl) totEl.textContent = stats?.total ?? 0;
    if (compEl) compEl.textContent = byStatus['completed'] || byStatus['abgeschlossen'] || 0;
    if (runEl) runEl.textContent = byStatus['running'] || byStatus['laufend'] || 0;
    if (planEl) planEl.textContent = byStatus['planned'] || byStatus['geplant'] || 0;
  }

  function renderResearchReports(reports) {
    const listEl = document.getElementById('researchReportList');
    if (!listEl) return;

    if (!reports || reports.length === 0) {
      listEl.innerHTML = '<div style="text-align:center; padding:3rem 1rem; color:#777; font-family:var(--mono-family); font-style:italic;">Keine Recherche-Berichte vorhanden. Starten Sie eine neue Recherche über "+ RECHERCHE STARTEN" oder lassen Sie den Bot Ergebnisse pushen.</div>';
      return;
    }

    let html = '';
    reports.forEach(rep => {
      const badge = getResearchStatusBadge(rep.status);
      const tags = Array.isArray(rep.tags) ? rep.tags : [];
      const tagsHtml = tags.map(t => `<span style="background:rgba(255,255,255,0.06); color:#aaa; padding:0.1rem 0.4rem; border-radius:3px; font-size:0.7rem; font-family:var(--mono-family);">#${escapeHtml(t)}</span>`).join(' ');
      const dateStr = rep.updated_at ? new Date(rep.updated_at * 1000).toLocaleString('de-DE') : '--';
      const summaryText = rep.summary || rep.snippet || rep.topic || '';
      const takeawaysCount = Array.isArray(rep.key_takeaways) ? rep.key_takeaways.length : 0;
      const sourcesCount = Array.isArray(rep.sources) ? rep.sources.length : 0;

      html += `
        <div class="devteam-task-card" onclick="openResearchReportDetail('${escapeHtml(rep.id)}')"
             style="background:#141424; border:1px solid rgba(255,255,255,0.08); border-left:5px solid ${badge.bg}; border-radius:0 8px 8px 0; padding:1rem; cursor:pointer; transition:transform 0.15s, border-color 0.15s; display:flex; flex-direction:column; gap:0.5rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
            <div style="display:flex; align-items:center; gap:0.6rem;">
              <span style="display:inline-block; font-size:0.72rem; font-weight:700; font-family:var(--mono-family); text-transform:uppercase; padding:0.15rem 0.55rem; border-radius:4px; background:${badge.bg}; color:${badge.color};">
                ${badge.label}
              </span>
              <span style="font-family:var(--mono-family); font-size:0.75rem; color:#888;">ID: ${escapeHtml(rep.id)}</span>
            </div>
            <div style="font-size:0.75rem; font-family:var(--mono-family); color:#aaa;">
              <span>🤖 ${escapeHtml(rep.author || 'researcher')}</span> &bull; <span>🕒 ${dateStr}</span>
            </div>
          </div>
          <div style="font-weight:700; font-size:1.05rem; color:#fff; font-family:var(--font-family); letter-spacing:0.02em;">
            ${escapeHtml(rep.title)}
          </div>
          ${summaryText ? `<div style="font-size:0.85rem; color:#bbb; line-height:1.4; max-height:2.8rem; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(summaryText)}</div>` : ''}
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-top:0.2rem;">
            ${tagsHtml ? `<div style="display:flex; gap:0.4rem; flex-wrap:wrap;">${tagsHtml}</div>` : '<div></div>'}
            <div style="display:flex; gap:0.8rem; font-size:0.75rem; font-family:var(--mono-family); color:#88bbcc;">
              <span>💡 ${takeawaysCount} Takeaways</span>
              <span>🔗 ${sourcesCount} Quellen</span>
            </div>
          </div>
        </div>
      `;
    });

    listEl.innerHTML = html;
  }

  function filterResearchStatus(status) {
    currentResearchStatus = status;
    document.querySelectorAll('.res-filter-btn').forEach(btn => {
      if (btn.getAttribute('data-status') === status) {
        btn.classList.add('active');
        btn.style.borderColor = '#33bbcc';
        btn.style.color = '#33bbcc';
        btn.style.fontWeight = '700';
      } else {
        btn.classList.remove('active');
        btn.style.borderColor = 'rgba(255,255,255,0.2)';
        btn.style.color = '#bbb';
        btn.style.fontWeight = 'normal';
      }
    });
    fetchResearchData(false);
  }

  function handleResearchSearchInput() {
    if (researchSearchTimeout) clearTimeout(researchSearchTimeout);
    researchSearchTimeout = setTimeout(() => {
      const inp = document.getElementById('researchSearchInput');
      currentResearchSearch = (inp?.value || '').trim();
      fetchResearchData(false);
    }, 250);
  }

  function clearResearchSearch() {
    const inp = document.getElementById('researchSearchInput');
    if (inp) inp.value = '';
    currentResearchSearch = '';
    fetchResearchData(false);
  }

  async function openResearchReportDetail(reportId) {
    playLcarsBeep(880, 1400);
    currentResearchReportId = reportId;
    activeResearchDetailTab = 'summary';
    const modal = document.getElementById('researchDetailModal');
    const modalTitle = document.getElementById('researchDetailModalTitle');
    const modalMeta = document.getElementById('researchDetailModalMeta');
    const modalBody = document.getElementById('researchDetailModalBody');
    if (!modal || !modalBody) return;

    if (modalTitle) modalTitle.textContent = 'LADE BERICHT...';
    modalBody.innerHTML = '<div style="padding:2rem; text-align:center; color:#aaa; font-family:var(--mono-family);">Lade Recherche-Bericht aus ODN Archiv...</div>';
    modal.style.display = 'flex';

    try {
      const resp = await fetch('/api/research/' + encodeURIComponent(reportId));
      if (!resp.ok) {
        modalBody.innerHTML = '<div style="padding:2rem; color:var(--c-red); font-family:var(--mono-family);">Fehler beim Laden des Berichts (Status ' + resp.status + ')</div>';
        return;
      }
      currentResearchReportData = await resp.json();
      if (modalTitle) modalTitle.textContent = currentResearchReportData.title || 'RECHERCHE-BERICHT';
      if (modalMeta) {
        const badge = getResearchStatusBadge(currentResearchReportData.status);
        const dt = currentResearchReportData.updated_at ? new Date(currentResearchReportData.updated_at * 1000).toLocaleString('de-DE') : '--';
        modalMeta.innerHTML = `<span style="color:${badge.bg}; font-weight:700;">[${badge.label}]</span> &bull; <span>AUTOR: ${escapeHtml(currentResearchReportData.author || 'researcher')}</span> &bull; <span>STAND: ${dt}</span> &bull; <span>ID: ${escapeHtml(currentResearchReportData.id)}</span>`;
      }
      switchResearchDetailTab('summary');
    } catch (e) {
      modalBody.innerHTML = '<div style="padding:2rem; color:var(--c-red); font-family:var(--mono-family);">Netzwerkfehler: ' + escapeHtml(String(e)) + '</div>';
    }
  }

  function switchResearchDetailTab(tabName) {
    activeResearchDetailTab = tabName;
    document.querySelectorAll('.res-detail-tab-btn').forEach(btn => {
      if (btn.getAttribute('data-tab') === tabName) {
        btn.classList.add('active');
        btn.style.borderColor = '#33bbcc';
        btn.style.color = '#33bbcc';
        btn.style.fontWeight = '700';
      } else {
        btn.classList.remove('active');
        btn.style.borderColor = 'rgba(255,255,255,0.2)';
        btn.style.color = '#bbb';
        btn.style.fontWeight = 'normal';
      }
    });
    renderResearchDetailTabContent();
  }

  function renderResearchDetailTabContent() {
    const modalBody = document.getElementById('researchDetailModalBody');
    if (!modalBody || !currentResearchReportData) return;
    const rep = currentResearchReportData;

    if (activeResearchDetailTab === 'summary') {
      const takeaways = Array.isArray(rep.key_takeaways) ? rep.key_takeaways : [];
      let takeawaysHtml = '';
      if (takeaways.length > 0) {
        takeawaysHtml = `
          <div style="margin-top:1.2rem; background:rgba(51, 187, 204, 0.08); border-left:4px solid #33bbcc; border-radius:4px; padding:1rem;">
            <div style="font-weight:700; color:#33bbcc; font-size:0.95rem; margin-bottom:0.6rem; font-family:var(--font-family); text-transform:uppercase;">
              💡 KEY TAKEAWAYS & KERNPUNKTE
            </div>
            <ul style="margin:0; padding-left:1.2rem; color:#ddd; line-height:1.5;">
              ${takeaways.map(t => `<li style="margin-bottom:0.4rem;">${escapeHtml(t)}</li>`).join('')}
            </ul>
          </div>
        `;
      }
      modalBody.innerHTML = `
        <div style="line-height:1.6; color:#ccc;">
          <div style="font-size:1.1rem; color:#fff; font-weight:700; margin-bottom:0.8rem;">
            ${escapeHtml(rep.title)}
          </div>
          <div style="font-size:0.95rem; color:#eee; background:rgba(0,0,0,0.3); padding:1rem; border-radius:6px; border:1px solid rgba(255,255,255,0.08);">
            ${rep.summary ? escapeHtml(rep.summary).replace(/\\n/g, '<br>') : '<em>Keine Kurzzusammenfassung angegeben.</em>'}
          </div>
          ${takeawaysHtml}
        </div>
      `;
    } else if (activeResearchDetailTab === 'details') {
      modalBody.innerHTML = `
        <div class="knowledge-content" style="line-height:1.6; color:#ddd; font-size:0.92rem;">
          ${renderLcarsMarkdown(rep.content || '*Kein ausführlicher Inhalt hinterlegt.*')}
        </div>
      `;
    } else if (activeResearchDetailTab === 'sources') {
      const sources = Array.isArray(rep.sources) ? rep.sources : [];
      if (sources.length === 0) {
        modalBody.innerHTML = '<div style="padding:2rem; text-align:center; color:#777; font-family:var(--mono-family);">Keine Quellen für diesen Bericht hinterlegt.</div>';
        return;
      }
      let html = '<div style="display:flex; flex-direction:column; gap:0.6rem;">';
      sources.forEach((src, idx) => {
        const isUrl = typeof src === 'string' && (src.startsWith('http://') || src.startsWith('https://'));
        html += `
          <div style="background:#141424; border:1px solid rgba(255,255,255,0.08); border-left:4px solid var(--c-gold); padding:0.8rem 1rem; border-radius:4px; display:flex; align-items:center; gap:0.75rem;">
            <span style="font-family:var(--mono-family); color:var(--c-gold); font-size:0.85rem; font-weight:700;">[${idx + 1}]</span>
            <div style="flex:1; min-width:0; font-family:var(--mono-family); font-size:0.88rem; overflow:hidden; text-overflow:ellipsis;">
              ${isUrl ? `<a href="${escapeHtml(src)}" target="_blank" rel="noopener noreferrer" style="color:#66ccff; text-decoration:none;">${escapeHtml(src)} ↗</a>` : `<span style="color:#eee;">${escapeHtml(String(src))}</span>`}
            </div>
          </div>
        `;
      });
      html += '</div>';
      modalBody.innerHTML = html;
    } else if (activeResearchDetailTab === 'raw') {
      const rawObj = {
        id: rep.id,
        title: rep.title,
        status: rep.status,
        topic: rep.topic,
        tags: rep.tags,
        author: rep.author,
        summary: rep.summary,
        key_takeaways: rep.key_takeaways,
        sources: rep.sources,
        structured_data: rep.structured_data,
        created_at: rep.created_at,
        updated_at: rep.updated_at
      };
      modalBody.innerHTML = `
        <div style="margin-bottom:0.75rem; font-size:0.8rem; color:#888; font-family:var(--mono-family);">
          JSON STRUKTURDATEN // LCARS EXPORT
        </div>
        <pre style="background:#0a0a14; border:1px solid rgba(255,255,255,0.12); border-left:4px solid #33bbcc; border-radius:4px; padding:1rem; overflow-x:auto; font-family:var(--mono-family); font-size:0.82rem; color:#44dd88;">${escapeHtml(JSON.stringify(rawObj, null, 2))}</pre>
      `;
    }
  }

  function closeResearchDetailModal() {
    playLcarsBeep(600, 100);
    const modal = document.getElementById('researchDetailModal');
    if (modal) modal.style.display = 'none';
    currentResearchReportId = null;
    currentResearchReportData = null;
  }

  function handleResearchDetailModalBackdrop(event) {
    if (event.target && event.target.id === 'researchDetailModal') {
      closeResearchDetailModal();
    }
  }

  async function deleteCurrentResearchReport() {
    if (!currentResearchReportId) return;
    if (!confirm('Diesen Recherche-Bericht wirklich unwiderruflich aus dem Archiv löschen?')) return;
    try {
      const resp = await fetch('/api/research/' + encodeURIComponent(currentResearchReportId), { method: 'DELETE' });
      if (resp.ok) {
        closeResearchDetailModal();
        fetchResearchData(true);
      } else {
        const d = await resp.json();
        alert('Fehler beim Löschen: ' + (d.error || 'Serverfehler'));
      }
    } catch (e) {
      alert('Fehler: ' + e);
    }
  }

  function openResearchTriggerModal() {
    playLcarsBeep(980, 1400);
    const form = document.getElementById('researchTriggerForm');
    if (form) form.reset();
    const modal = document.getElementById('researchTriggerModal');
    if (modal) modal.style.display = 'flex';
  }

  function closeResearchTriggerModal() {
    playLcarsBeep(600, 100);
    const modal = document.getElementById('researchTriggerModal');
    if (modal) modal.style.display = 'none';
  }

  function handleResearchTriggerModalBackdrop(event) {
    if (event.target && event.target.id === 'researchTriggerModal') {
      closeResearchTriggerModal();
    }
  }

  async function submitResearchTrigger(event) {
    event.preventDefault();
    const title = (document.getElementById('researchFormTitle')?.value || '').trim();
    const topic = (document.getElementById('researchFormTopic')?.value || '').trim();
    const tagsStr = document.getElementById('researchFormTags')?.value || '';
    const notes = (document.getElementById('researchFormNotes')?.value || '').trim();

    if (!title) {
      alert('Titel der Recherche ist ein Pflichtfeld.');
      return;
    }

    const tags = tagsStr.split(',').map(s => s.trim()).filter(Boolean);
    const spinner = document.getElementById('researchSubmitSpinner');
    const btn = document.getElementById('btnResearchSubmitTrigger');
    if (spinner) spinner.style.display = 'inline-block';
    if (btn) btn.disabled = true;

    try {
      const resp = await fetch('/api/research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: title,
          topic: topic,
          tags: tags,
          notes: notes,
          action: 'trigger'
        })
      });

      if (resp.ok) {
        closeResearchTriggerModal();
        await fetchResearchData(true);
      } else {
        const d = await resp.json();
        alert('Fehler beim Starten der Recherche: ' + (d.error || 'Serverfehler'));
      }
    } catch (e) {
      alert('Fehler: ' + e);
    } finally {
      if (spinner) spinner.style.display = 'none';
      if (btn) btn.disabled = false;
    }
  }

  function startResearchAutoRefresh() {
    if (researchRefreshInterval) clearInterval(researchRefreshInterval);
    researchRefreshInterval = setInterval(() => {
      if (currentCategory === 'research') {
        fetchResearchData(false);
      }
    }, 30000);
  }

  // Initialer Boot-Ablauf
  function bootDashboard() {
    renderStats(initialStats);
    if (initialStats?.nine_router) {
      renderNineRouterStats(initialStats.nine_router);
    }
    const initialTimeEl = document.getElementById('chatInitialTime');
    if (initialTimeEl) initialTimeEl.textContent = formatTimeNow();
    loadChatModels();
    ensureChart(() => {
      initHistoryChart();
      initNineRouterCharts();
      initHermesChart();
    });
    startFantasyAutoRefresh();
    checkHomeAssistantConfig();
    startHaAutoRefresh();
    loadSolarData(false);
    startSolarAutoRefresh();
    initLcarsVoiceComm();
    fetchPermissionsStatus();
    loadCycleData();
    checkPulsecastStatus();
    checkGeminiLiveStatus();
    startDevteamAutoRefresh();
    startKnowledgeAutoRefresh();
    startResearchAutoRefresh();
    checkLcarsAuthUser();
  }

  async function checkLcarsAuthUser() {
    try {
      const resp = await fetch('/api/auth/me');
      if (resp.ok) {
        const data = await resp.json();
        if (data.authenticated && data.user) {
          currentLcarsUser = data.user;
          const uname = (data.user.display_name || data.user.username || 'CB').toUpperCase();
          const el = document.getElementById('topUserDisplay');
          if (el) el.textContent = uname;
          const pfx = document.getElementById('topUserPrefix');
          if (pfx) pfx.textContent = 'ANGEMELDET ALS:';
          const logoutBtn = document.getElementById('topLogoutBtn');
          if (logoutBtn) {
            logoutBtn.style.display = 'inline-block';
            logoutBtn.style.backgroundColor = 'var(--c-red)';
            logoutBtn.style.color = '#ffffff';
            logoutBtn.textContent = '⏻ LOGOUT';
            logoutBtn.onclick = lcarsLogout;
          }
          applyPermissionsVisibility();
        } else {
          currentLcarsUser = null;
          const el = document.getElementById('topUserDisplay');
          if (el) el.textContent = 'GAST';
          const pfx = document.getElementById('topUserPrefix');
          if (pfx) pfx.textContent = 'STATUS:';
          const logoutBtn = document.getElementById('topLogoutBtn');
          if (logoutBtn) {
            logoutBtn.style.display = 'inline-block';
            logoutBtn.style.backgroundColor = 'var(--c-primary)';
            logoutBtn.style.color = '#000000';
            logoutBtn.textContent = '🔓 LOGIN';
            logoutBtn.onclick = openLcarsLoginOverlay;
          }
          applyPermissionsVisibility();
        }
      }
    } catch (_) {}
  }

  async function lcarsLogout() {
    playLcarsBeep(600, 300);
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch (_) {}
    window.location.href = '/login';
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootDashboard);
  } else {
    bootDashboard();
  }

// ── MOTION ALARM ─────────────────────────────────────────────────────────
  var _alarmEnabled = localStorage.getItem('haMotionAlarmEnabled') === 'true';

  function _syncAlarmUI() {
    var statusEl = document.getElementById('alarmStatus');
    var badgeEl  = document.getElementById('topVitalAlarm');
    var toggle   = document.getElementById('alarmToggle');
    if (statusEl) {
      statusEl.textContent = _alarmEnabled ? 'ON' : 'OFF';
      statusEl.style.color = _alarmEnabled ? '#0f0' : '';
    }
    if (badgeEl) {
      badgeEl.style.opacity = _alarmEnabled ? '1' : '0.45';
      if (_alarmEnabled) {
        badgeEl.classList.add('vital-alert');
      } else {
        badgeEl.classList.remove('vital-alert');
      }
    }
    if (toggle) toggle.checked = _alarmEnabled;
  }

  function toggleMotionAlarm() {
    _alarmEnabled = !_alarmEnabled;
    localStorage.setItem('haMotionAlarmEnabled', _alarmEnabled.toString());
    _syncAlarmUI();
    if (typeof playLcarsBeep === 'function') playLcarsBeep(_alarmEnabled ? 880 : 440, _alarmEnabled ? 1760 : 880);
  }

  // Initialize alarm state on load
  document.addEventListener('DOMContentLoaded', function() {
    _syncAlarmUI();
  });

  // ── DEVTEAM REPORT MODAL ─────────────────────────────────────────────────
  function openDevteamReportModal() {
    var modal = document.getElementById('devteamReportModal');
    if (!modal) return;
    // Reset fields
    var t = document.getElementById('reportTitle');
    var d = document.getElementById('reportDescription');
    var c = document.getElementById('reportComponent');
    var u = document.getElementById('reportUrgency');
    var f = document.getElementById('reportFeedback');
    if (t) t.value = '';
    if (d) d.value = '';
    if (c) c.value = 'agydashboard';
    if (u) u.value = 'medium';
    if (f) { f.style.display = 'none'; f.textContent = ''; }
    modal.style.display = 'flex';
    if (typeof playLcarsBeep === 'function') playLcarsBeep(880, 1760);
    setTimeout(function() { var t2 = document.getElementById('reportTitle'); if(t2) t2.focus(); }, 100);
  }

  function closeDevteamReportModal() {
    var modal = document.getElementById('devteamReportModal');
    if (modal) modal.style.display = 'none';
  }

  async function submitDevteamReport() {
    var title       = (document.getElementById('reportTitle')?.value || '').trim();
    var description = (document.getElementById('reportDescription')?.value || '').trim();
    var component   = document.getElementById('reportComponent')?.value || 'general';
    var urgency     = document.getElementById('reportUrgency')?.value || 'medium';
    var feedback    = document.getElementById('reportFeedback');

    if (!title) {
      if (feedback) {
        feedback.style.display = 'block';
        feedback.style.background = 'rgba(255,0,0,0.15)';
        feedback.style.color = 'var(--c-red)';
        feedback.textContent = '⚠ TITEL ist ein Pflichtfeld.';
      }
      document.getElementById('reportTitle')?.focus();
      return;
    }
    if (!description) {
      if (feedback) {
        feedback.style.display = 'block';
        feedback.style.background = 'rgba(255,0,0,0.15)';
        feedback.style.color = 'var(--c-red)';
        feedback.textContent = '⚠ BESCHREIBUNG ist ein Pflichtfeld.';
      }
      document.getElementById('reportDescription')?.focus();
      return;
    }

    if (feedback) {
      feedback.style.display = 'block';
      feedback.style.background = 'rgba(255,200,0,0.1)';
      feedback.style.color = 'var(--c-gold)';
      feedback.textContent = '⏳ Wird gesendet…';
    }

    try {
      var resp = await fetch('/api/devteam/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title, body: description, component: component, urgency: urgency })
      });
      if (resp.ok) {
        if (feedback) {
          feedback.style.background = 'rgba(0,255,0,0.1)';
          feedback.style.color = '#0f0';
          feedback.textContent = '✅ Report erfolgreich gesendet!';
        }
        setTimeout(function() { closeDevteamReportModal(); }, 1500);
      } else {
        var errText = await resp.text().catch(function() { return resp.status; });
        if (feedback) {
          feedback.style.background = 'rgba(255,0,0,0.15)';
          feedback.style.color = 'var(--c-red)';
          feedback.textContent = '❌ Fehler: ' + errText;
        }
      }
    } catch (err) {
      if (feedback) {
        feedback.style.background = 'rgba(255,0,0,0.15)';
        feedback.style.color = 'var(--c-red)';
        feedback.textContent = '❌ Netzwerkfehler: ' + err.message;
      }
    }
  }
