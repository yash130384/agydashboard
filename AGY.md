# AGY.md — Antigravity Projekt-Verknüpfung & Systemarchitektur

Projekt: agydashboard  
Pfad: `/home/yash/Projects/agydashboard`  
GitHub: `https://github.com/yash130384/agydashboard`  

## Zweck
Dashboard-Projekt. Antigravity nutzt dieses Verzeichnis als Workspace (Start mit `agy --project agydashboard` oder `cd` hierher + `agy`).

## Konventionen
- Sprache: Deutsch für Dokumentation, Englisch für Quellcode & Variablen.
- Commits: Conventional Commits `type: subject` (`fix:`, `feat:`, `docs:`, `chore:`).

---

# Architektur- & Implementierungsplan: Migration auf Cloudflare Named Tunnel (`pimmel.site`)

## 1. Zielsetzung & Übersicht

Alle Cloudflare-Tunnel des `agydashboard` werden vollständig von unzuverlässigen, temporären `*.trycloudflare.com` Quick-Tunnels auf den offiziellen Named Tunnel **`pimmel-tunnel`** unter der Domain **`pimmel.site`** umgestellt.

### Eckdaten der Infrastruktur
| Komponente | Pfad / Bezeichner | Zweck / Konfiguration |
|---|---|---|
| **Named Tunnel Name** | `pimmel-tunnel` | Fester Cloudflare Named Tunnel |
| **Tunnel UUID** | `b5c7fa60-f43e-487e-bad6-70975ca93823` | Cloudflare Tunnel ID |
| **Konfigurationsdatei** | `/home/yash/.cloudflared/config.yml` | Ingress-Regeln & Credentials |
| **Credentials Datei** | `/home/yash/.cloudflared/b5c7fa60-f43e-487e-bad6-70975ca93823.json` | Tunnel Auth Key |
| **Cloudflared Binary** | `/home/yash/bin/cloudflared` | CLI & Tunnel Connector Binary |
| **Systemd Service** | `cloudflared.service` (User Mode) | `systemctl --user restart cloudflared` |
| **Basis-Domain** | `pimmel.site` | Offizielle Domain mit Cloudflare DNS & TLS |

---

## 2. Definierte Service-Subdomains (2–4 Buchstaben)

Alle bekannten und primären Systemdienste erhalten prägnante Subdomains von 2 bis maximal 4 Buchstaben:

| Subdomain | Vollständige URL | Lokaler Port | Dienst / Prozess | Aktueller Status |
|---|---|---|---|---|
| **ha** | `https://ha.pimmel.site` | `8123` | Home Assistant Core (`hass`) | Bereits in `config.yml` & DNS aktiv |
| **ai** | `https://ai.pimmel.site` | `20128` | 9Router AI Gateway (`next-server`) | Bereits in `config.yml` & DNS aktiv |
| **dash** | `https://dash.pimmel.site` *(sowie `https://pimmel.site`)* | `5000` | agydashboard System Dashboard | `pimmel.site` aktiv; `dash` neu |
| **cast** | `https://cast.pimmel.site` | `3000` | xdcc-load-cast / PulseCast (`node`) | Neu anzulegen |
| **tele** | `https://tele.pimmel.site` | `8000` | TelemetryVault ACC Telemetry (`uvicorn`) | Neu anzulegen |
| **mat** | `https://mat.pimmel.site` | `5580` | Python Matter Server (`matter-server`) | Neu anzulegen |
| **head** | `https://head.pimmel.site` | `8787` | Headroom AI Context Compression | Neu anzulegen |

---

## 3. Architektur des dynamischen Named-Tunnel-Managers

Anstelle des bisherigen `CloudflaredTunnelManager`, der eigene Subprozesse mit `--url http://127.0.0.1:<port>` gestartet hat, übernimmt künftig ein thread-sicherer **`CloudflaredNamedTunnelManager`** die Verwaltung:

```mermaid
flowchart TD
    A[WebserverScanner entdeckt LISTEN Port] --> B{Port in Filterliste? 22, 111, 5432...}
    B -- Ja --> C[Ignorieren]
    B -- Nein --> D{Bereits in config.yml / Static Map?}
    D -- Ja --> E[Sofort https://subdomain.pimmel.site zurückgeben]
    D -- Nein --> F[Auto-Generierung: Eindeutiges 2-4 Buchstaben Kürzel]
    F --> G[DNS CNAME erstellen via cloudflared route dns]
    G --> H[config.yml sperren & Ingress Rule vor 404 einfügen]
    H --> I[Validation: cloudflared tunnel ingress validate]
    I -- Validierung OK --> J[systemctl --user restart cloudflared]
    I -- Fehler --> K[Backup config.yml.bak wiederherstellen & Warnung]
    J --> L[URL im Memory-Cache & UI-Chips aktualisieren]
    E --> M[UI-Chip: CF: https://subdomain.pimmel.site]
    L --> M
```

### 3.1 Subdomain-Generierungs-Algorithmus (2–4 Zeichen)
Für neu gefundene Webdienste erzeugt die Funktion `generate_subdomain(port, process_name, title)` automatisch ein valides Kürzel:
1. **Prioritäts-Lookup**: Prüfen, ob für den Port ein statischer Eintrag in `STATIC_PORT_SUBDOMAINS` existiert.
2. **Kandidaten aus Namen extrahieren**:
   - Bereinigen von `process_name` / `title` (nur `a-z0-9`, Kleinbuchstaben).
   - Generische Namen wie `python`, `node`, `docker`, `system` werden übersprungen.
   - Prägnante 3-4 Buchstaben-Präfixe (z.B. `grafana` -> `graf`, `ollama` -> `olla`, `plex` -> `plex`, `caddy` -> `cad`).
3. **Fallback bei Namenskollision oder generischen Diensten**:
   - `s` + letzte 2-3 Stellen des Ports (z.B. Port `8080` -> `s808`, Port `9000` -> `s900`, Port `4200` -> `s420`).
   - Garantiert strikt das Format: `^[a-z0-9]{2,4}$`.
4. **Kollisionsprüfung**:
   - Prüfung gegen alle bereits in `/home/yash/.cloudflared/config.yml` vorhandenen Hostnames und DNS-Reservierungen.

### 3.2 DNS-Provisionierung
Für jedes neue Kürzel wird folgender Befehl ausgeführt:
```bash
/home/yash/bin/cloudflared tunnel route dns -f pimmel-tunnel <subdomain>.pimmel.site
```
- `-f` (`--overwrite-dns`): Überschreibt eventuell verwaiste alte DNS-Records sicher.
- Nutzt die existierenden Credentials in `/home/yash/.cloudflared/cert.pem`.

### 3.3 Ingress-Regel in `config.yml` atomar einpflegen
Die Konfiguration `/home/yash/.cloudflared/config.yml` besitzt die Ingress-Struktur:
```yaml
tunnel: b5c7fa60-f43e-487e-bad6-70975ca93823
credentials-file: /home/yash/.cloudflared/b5c7fa60-f43e-487e-bad6-70975ca93823.json

ingress:
  - hostname: pimmel.site
    service: http://localhost:5000
  - hostname: dash.pimmel.site
    service: http://localhost:5000
  - hostname: ha.pimmel.site
    service: http://localhost:8123
  - hostname: ai.pimmel.site
    service: http://localhost:20128
  - hostname: cast.pimmel.site
    service: http://localhost:3000
  - hostname: tele.pimmel.site
    service: http://localhost:8000
  - hostname: mat.pimmel.site
    service: http://localhost:5580
  - hostname: head.pimmel.site
    service: http://localhost:8787
  # [Dynamische Ingress-Regeln werden hier eingefügt]
  - service: http_status:404
```
**Sicherheitsmechanismus:**
- Vor jedem Schreibvorgang wird `/home/yash/.cloudflared/config.yml.bak` erstellt.
- Neue Hostname-Einträge werden *vor* dem Catch-All `- service: http_status:404` eingefügt.
- Nach dem Schreiben erfolgt die Validierung via:
  ```bash
  /home/yash/bin/cloudflared tunnel --config /home/yash/.cloudflared/config.yml ingress validate
  ```
- Nur wenn die Validierung `OK` (Exit-Code 0) zurückgibt, wird der Daemon neu gestartet. Andernfalls erfolgt ein sofortiges Rollback.

### 3.4 Tunnel-Service Neustart
```bash
systemctl --user restart cloudflared
```
Erfolgt über Python `subprocess.run(["systemctl", "--user", "restart", "cloudflared"], check=True)`.
Ein Mutex (`threading.Lock`) und ein kurzes Cooldown-Debouncing (3–5 Sekunden) verhindern mehrfache Neustarts bei Scans mehrerer neuer Ports.

---

## 4. Vollständige Bereinigung aller alten Quick-Tunnel

1. **Beenden aller aktiven Quick-Tunnel-Prozesse**:
   - Suche aller Prozesse mit `cmdline` enthält `cloudflared` und `--url` oder `.trycloudflare.com` und Senden von `SIGTERM` / `SIGKILL`.
2. **Deaktivierung des alten systemd-Dienstes**:
   ```bash
   systemctl --user stop cloudflared-tunnels.service || true
   systemctl --user disable cloudflared-tunnels.service || true
   ```
3. **Bereinigung alter Dateien**:
   - Entfernen oder Leeren von `/tmp/cloudflared_*.log`.
   - Migration bzw. Aufräumen von `/home/yash/.cloudflared-urls/*.url`.
   - Ersetzen von `/home/yash/bin/cloudflared-tunnel.sh` durch ein Deprecation-Script oder Stilllegung.
4. **Code-Bereinigung in `app.py`**:
   - Entfernung aller Regex-Suchen nach `trycloudflare.com`.
   - Entfernung von `subprocess.Popen([self.binary_path, "tunnel", "--url", ...])`.

---

## 5. Änderungen im Detail (File by File)

### 5.1 `/home/yash/.cloudflared/config.yml`
- Eintragen der 7 festen Services (`pimmel.site`, `dash`, `ha`, `ai`, `cast`, `tele`, `mat`, `head`).
- Catch-All Regel `- service: http_status:404` am Ende sicherstellen.

### 5.2 DNS CNAMEs via Cloudflared CLI
Ausführen der initialen Registrierungen:
```bash
/home/yash/bin/cloudflared tunnel route dns -f pimmel-tunnel dash.pimmel.site
/home/yash/bin/cloudflared tunnel route dns -f pimmel-tunnel cast.pimmel.site
/home/yash/bin/cloudflared tunnel route dns -f pimmel-tunnel tele.pimmel.site
/home/yash/bin/cloudflared tunnel route dns -f pimmel-tunnel mat.pimmel.site
/home/yash/bin/cloudflared tunnel route dns -f pimmel-tunnel head.pimmel.site
```

### 5.3 `/home/yash/Projects/agydashboard/app.py`
1. **`SERVICE_REGISTRY`**:
   - Hinzufügen von `mat` (5580, `Matter Server`).
   - Vorkonfigurierte `cf_url`-Einträge:
     - `dashboard` (5000): `https://dash.pimmel.site`
     - `telemetry` (8000): `https://tele.pimmel.site`
     - `xdcc` (3000): `https://cast.pimmel.site`
     - `9router` (20128): `https://ai.pimmel.site`
     - `headroom` (8787): `https://head.pimmel.site`
     - `homeassistant` (8123): `https://ha.pimmel.site`
     - `matter` (5580): `https://mat.pimmel.site`
2. **Klasse `CloudflaredNamedTunnelManager`**:
   - `get_url(port)`: Liest statische & dynamische Zuordnungen aus `config.yml` oder Cache (`https://<subdomain>.pimmel.site`).
   - `ensure_tunnel(port, process_name="", title="")`: Prüft `config.yml`, generiert Subdomain falls unbekannt, erstellt DNS-Eintrag, fügt Ingress-Regel ein, validiert & startet `cloudflared.service` neu.
   - `kill_legacy_quick_tunnels()`: Tötet laufende Quick-Tunnel-Prozesse beim Start.
   - `stop_tunnel(port)`: Entfernt Ingress-Regel optional bei Dienst-Beendigung oder belässt sie persistent.
3. **`get_cloudflared_urls()`**:
   - Gibt das Mapping aller Ports/Dienste auf ihre `https://<subdomain>.pimmel.site` URLs zurück.
4. **`WebserverScanner.scan()` & `get_cloudflared_map()`**:
   - Verwendet direkt `cf_tunnel_manager.get_all_urls()` bzw. `ensure_tunnel(port)`.
   - Bereitstellung sauberer `cloudflared_url` Werte für alle erkannten Server.
5. **UI-Chips & Templates**:
   - Jinja2-Template & JavaScript-Aktualisierung:
     - Badge zeigt direkt den sauberen Link: `☁️ CF: https://<subdomain>.pimmel.site` (oder gekürzt `☁️ <subdomain>.pimmel.site`).
     - Kein Lade-Hänger mehr wegen fehlender Quick-Tunnel-Logs.

---

## 6. Verifikationsplan

### Schritt 1: DNS- und Tunnel-Auflösung
```bash
python3 -c "
import socket
for sub in ['dash', 'ha', 'ai', 'cast', 'tele', 'mat', 'head']:
    host = f'{sub}.pimmel.site'
    print(host, '->', socket.gethostbyname(host))
"
```
*Erwartung*: Alle 7 Subdomains lösen auf Cloudflare Edge IPs (z.B. `188.114.96.4` / `188.114.97.4`) auf.

### Schritt 2: Ingress-Konfiguration und Validierung
```bash
/home/yash/bin/cloudflared tunnel --config /home/yash/.cloudflared/config.yml ingress validate
```
*Erwartung*: `Validating rules... OK`.

### Schritt 3: Systemd-Status & Prozessprüfung
```bash
systemctl --user status cloudflared
systemctl --user status cloudflared-tunnels.service  # Soll inaktiv/disabled sein
ps aux | grep -E "cloudflared.*--url"               # Soll 0 Treffer liefern
```

### Schritt 4: End-to-End HTTP(S)-Aufrufe
Prüfung der HTTPS-Routen von extern/über Cloudflare:
- `https://dash.pimmel.site` -> HTTP 200 (agydashboard)
- `https://ha.pimmel.site` -> Home Assistant Login
- `https://ai.pimmel.site` -> 9Router Gateway
- `https://cast.pimmel.site` -> PulseCast / XDCC
- `https://tele.pimmel.site` -> TelemetryVault
- `https://mat.pimmel.site` -> Matter Server

### Schritt 5: Dynamische Auto-Generierung testen
Simulierter Testlauf: Starten eines Test-HTTP-Servers auf Port 8899:
```bash
python3 -m http.server 8899 &
```
- Überprüfen, ob `agydashboard` den Server erkennt.
- Überprüfen, ob ein 2-4 Buchstaben-Kürzel generiert wird.
- Überprüfen, ob `config.yml` aktualisiert und `cloudflared` neu gestartet wird.
- Beenden des Test-Servers.

### Schritt 6: Dashboard-UI Verifikation
Aufruf des Dashboards unter `http://localhost:5000` bzw. `https://dash.pimmel.site`:
- Überprüfung der Chips in der LCARS Webserver-Liste: Jeder Server zeigt einen `☁️ CF: https://<subdomain>.pimmel.site` Chip, der per Klick die Seite öffnet.

---

# PulseCast Media & XDCC Erweiterungen

## 1. Direkte Wiedergabe im lokalen Media Player (HTTP Range Streaming & M3U-Streamdateien)
- **Streaming-Proxy-Endpunkt**:
  `GET /api/pulsecast/media/stream/<path:filename>` (sowie `HEAD`)
  Leitet native HTTP Range Requests transparent an `http://127.0.0.1:3000/api/media/stream/<filename>` weiter.
  - Transparente Weiterleitung von Request-Headern (`Range`, `If-Range`) und Query-Parametern (`transcode=audio`, `ss=...`).
  - Durchreichen relevanter Response-Header (`206 Partial Content`, `Content-Range`, `Accept-Ranges: bytes`, `Content-Type`, `Content-Length`, `Cache-Control`, `ETag`, `Last-Modified`).
  - Flüssiges Seeken und latenzfreies Streaming.
- **M3U Stream-Datei Generator / Proxy**:
  `GET /api/pulsecast/media/stream.m3u`
  - Parameter: `filename` (z.B. `Filme/1108046_Coyote_vs_ACME_2026_NEU.mkv`), optional `title` (Anzeigetitel), optional `code` (Command Code z.B. `0901`).
  - Validiert Autorisierung (`_pulsecast_authorized()`).
  - Generiert `#EXTM3U` Playlist-Datei mit dynamischer Stream-URL (`http(s)://<host>/api/pulsecast/media/stream/<filename>?code=...`).
  - Response-Header: `Content-Type: application/x-mpegurl; charset=utf-8`, `Content-Disposition: attachment; filename="<clean_title>.m3u"`, `Cache-Control: no-cache`.
  - **1-Klick Wiedergabe**: Beim Anklicken/Download öffnet das Betriebssystem (Windows, macOS, Linux, Android) die Datei sofort im verknüpften Player (VLC, PotPlayer, IINA) mit voller nativer Audio-Unterstützung (AC3, E-AC3, DTS, TrueHD) und Seeking!
- **Audio-Transcode Proxy & Codec-Probing**:
  - `GET /api/pulsecast/media/probe/<path:filename>`:
    Fragt `http://127.0.0.1:3000/api/media/probe/<filename>` ab und liefert JSON `{ filename, needsAudioTranscode: true/false }` zurück (mittels `ffprobe`-Analyse unkompatibler Browser-Audiocodecs wie AC3, E-AC3, DTS).
  - `GET /api/pulsecast/media/transcode/<path:filename>` (sowie `HEAD`):
    Transparentes Streaming von `http://127.0.0.1:3000/api/media/transcode/<filename>` mit On-the-Fly Konvertierung von Video-Audiospuren nach AAC.
  - Query-Parameter `?transcode=audio` oder `?transcode=true` an `/api/pulsecast/media/stream/<filename>` wird transparent an Port 3000 durchgereicht.
- **LCARS Media Player Modal (`#pulsecastPlayerModal`)**:
  - **📥 1-Klick M3U Stream-Datei**: Prominenter LCARS Action-Button `📥 VLC / MEDIA PLAYER STREAM-DATEI (.M3U)` zum direkten Download der Playlist für externe Player.
  - **Web-Player (Browser)**: Integriertes LCARS HTML5 `<video controls autoplay playsinline>` Player-Modal direkt im Dashboard.
    - Automatisches Codec-Probing beim Start: Erkennt AC-3/DTS und schaltet automatisch auf Audio-Transcoding um.
    - Audio-Umschalter im UI: `🔊 TON: AAC (KOMPATIBEL)` / `🎬 TON: ORIGINAL (NATIV)` inklusive Beibehaltung der aktuellen Abspielposition (`currentTime`).
    - Visueller Status-Hinweis über aktiven Audiomodus (`🔊 Audio-Transcoding aktiv (AAC Stereo/5.1 für ruckelfreien Browser-Ton)`).
  - **App-Protokoll URL-Schemes**:
    - **VLC Media Player**: `vlc://<stream_url>`
    - **PotPlayer (Windows)**: `potplayer://<stream_url>`
    - **IINA (macOS)**: `iina://weblink?url=<encoded_url>`
    - **nPlayer (Mobile)**: `nplayer-<stream_url>`
    - Ergänzt um deutliche Hinweise: `(Nur wenn App-Protokoll registriert)`.
  - **Im neuen Tab öffnen**: Direkte Wiedergabe via `window.open(streamUrl, '_blank')`.
  - **Stream-URL kopieren**: Kopiert direkte HTTP Range Stream-URL in die Zwischenablage inkl. optischer Erfolgsanzeige.
- **UI-Aktion `▶ IN PLAYER ÖFFNEN`**:
  - In der Download-Warteschlange bei Status `completed` (FERTIG).
  - Im Katalog-Browser bei lokalen Mediendateien (`isXtream === false`).
  - Im Episoden-Modal bei lokal verfügbaren Serien-Episoden.

## 2. XDCC-Suche: Quellenauswahl & Movie Gods Top-Downloads
- **Quellenauswahl**:
  - LCARS-Pills in der XDCC-Suchmaske: `XDCC.EU Relay` (Standard) und `Movie Gods (IRC)`.
- **Movie Gods Top-Downloads / Favoriten**:
  - Bei aktiver Movie Gods Quelle wird der Bereich `⭐ MOVIE GODS TOP-DOWNLOADS / FAVORITEN` eingeblendet.
  - Button `⭐ TOP-DOWNLOADS / FAVORITEN LADEN` ruft `/api/pulsecast/search?q=!topdl&source=moviegods` ab.
  - LCARS-Tabelle mit Spalten: `GETS` (z.B. 265x), `DATEINAME`, `GRÖSSE`, `AKTION` (`🔍 SUCHEN`).
  - Klick auf einen Top-Download übernimmt den Dateinamen sofort in das Suchfeld, setzt die Quelle auf Movie Gods und führt die Suche nach Bots & Packs sofort aus.
- **Parametrisierung & Download**:
  - Übergabe von `source=moviegods` an `/api/pulsecast/search`.
  - Beim Download via `/api/pulsecast/download/xdcc` wird automatisch der Channel `#moviegods` gesetzt.

---

# Architektur- & Implementierungsplan: Zentrale User-Verwaltung & Cloudflare-Zugangsabsicherung (*.pimmel.site)

## 1. Zielsetzung & Geltungsbereich

Dieser Architektur- und Umsetzungsplan definiert das Sicherheitsmodell, den Authentifizierungs-Flow, die Benutzeroberfläche und die technische Infrastruktur für eine zentrale Benutzerverwaltung sowie die Absicherung aller externen Zugriffe über den Cloudflare Named Tunnel (`*.pimmel.site`).

### 1.1 Geltungsbereich (Scope-Matrix)
| Dienst / Subdomain | Lokaler Port | Status / Schutzmaßnahme | Begründung / Verhalten |
|---|---|---|---|
| **ha.pimmel.site** | 8123 | **Direkter Durchgriff (Ungeschützt)** | Besitzt bereits ein eigenes, vollwertiges Authentifizierungssystem (Home Assistant Auth). Bleibt direkt in `config.yml` auf Port 8123 geroutet. |
| **ai.pimmel.site** | 20128 | **Direkter Durchgriff (Ungeschützt)** | Besitzt eigenes Authentifizierungssystem (9Router Master Key / Auth). Bleibt direkt auf Port 20128 geroutet. |
| **pimmel.site** / **dash.pimmel.site** | 5000 | **Dashboard & Login-Zentrale** | Enthält den öffentlichen LCARS Login-Bildschirm (`/login`), die zentralen Auth-APIs sowie die Admin-Oberfläche hinter Command-Code `0901`. |
| **cast.pimmel.site** | 3000 | **GESCHÜTZT via Auth-Proxy** | PulseCast besitzt kein eigenes Multi-User-Login. Zugriff erfordert gültige LCARS-Session mit Berechtigung `pulsecast`. |
| **tele.pimmel.site** | 8000 | **GESCHÜTZT via Auth-Proxy** | TelemetryVault ACC Telemetriedienst besitzt kein Login. Zugriff erfordert LCARS-Session mit Berechtigung `telemetryvault`. |
| **mat.pimmel.site** | 5580 | **GESCHÜTZT via Auth-Proxy** | Matter Server Web-UI / WebSocket besitzt kein eigenes Login. Zugriff erfordert LCARS-Session mit Berechtigung `matter`. |
| **head.pimmel.site** | 8787 | **GESCHÜTZT via Auth-Proxy** | Headroom AI Service besitzt kein Login. Zugriff erfordert LCARS-Session mit Berechtigung `headroom`. |
| **port.pimmel.site** | 631 | **GESCHÜTZT via Auth-Proxy** | CUPS Druckerdienst Web-UI besitzt kein Internet-Login. Zugriff erfordert LCARS-Session mit Berechtigung `cups`. |
| *Künftige Webdienste* | dynamisch | **GESCHÜTZT (Default)** | Automatisch erkannte Webdienste werden per Default über den Auth-Proxy abgesichert (Secure by Default). |

### 1.2 Netzwerk-Verhalten: LAN vs. WAN (Zero-Interference-Prinzip)
- **Lokaler Netzwerk-Zugriff (LAN / `192.168.x.x` & `localhost`)**:
  - Lokale Geräte im Heimnetzwerk (Smart TVs, lokale Browser, ACC Telemetrie-Clients, HA-Integrationen, Drucker) verbinden sich direkt mit den lokalen IP-Adressen und Ports (z.B. `http://192.168.31.169:3000` oder `http://localhost:8000`).
  - Diese Verbindungen laufen physisch nicht über Cloudflare und berühren den Auth-Proxy nicht.
  - **Ergebnis**: Absolut freier, latenzfreier und unauthentifizierter Zugriff im gesamten lokalen Netzwerk, exakt wie gefordert.
- **Externer Zugriff (WAN / `*.pimmel.site`)**:
  - Alle externen Zugriffe treffen am Cloudflare Edge ein und werden über den Named Tunnel `pimmel-tunnel` (`cloudflared`) an den Host weitergeleitet.
  - Bei geschützten Subdomains leitet `cloudflared` den Traffic an den lokalen Auth-Reverse-Proxy (`127.0.0.1:5050`) weiter.

---

## 2. Systemarchitektur & Request-Flow

```mermaid
flowchart TD
    subgraph WAN["Externer WAN-Zugriff"]
        Client["Externer Browser / Client"]
    end

    subgraph CloudflareEdge["Cloudflare Edge (*.pimmel.site)"]
        CF["Cloudflare DNS & TLS Edge"]
    end

    subgraph Host["Host System (BiggerPimmel)"]
        CFTunnel["cloudflared pimmel-tunnel\n(Ingress Router)"]
        
        subgraph AuthComponents["LCARS Auth Subsystem"]
            AuthProxy["LCARS Auth-Proxy\n(127.0.0.1:5050 / asyncio)"]
            UserDB[("users.db (SQLite)\nUsers, Sessions, Audit")]
            AgyDash["agydashboard (Port 5000)\n• /login (LCARS UI)\n• /api/auth/*\n• /api/users/* (Code 0901)"]
        end

        subgraph DirectEndpoints["Direkte Dienste (Eigener Auth)"]
            HA["Home Assistant (Port 8123)"]
            AI["9Router (Port 20128)"]
        end

        subgraph ProtectedEndpoints["Geschützte Dienste"]
            PulseCast["PulseCast (Port 3000)"]
            Telemetry["TelemetryVault (Port 8000)"]
            Matter["Matter Server (Port 5580)"]
            Headroom["Headroom (Port 8787)"]
            CUPS["CUPS (Port 631)"]
        end
    end

    subgraph LAN["Lokales Heimnetzwerk (192.168.31.x)"]
        LANClient["Lokaler Client / Smart-TV / ACC"]
    end

    Client -->|HTTPS Request| CF
    CF -->|Tunnel Stream| CFTunnel

    %% Ingress Routing
    CFTunnel -->|ha.pimmel.site:8123| HA
    CFTunnel -->|ai.pimmel.site:20128| AI
    CFTunnel -->|dash.pimmel.site:5000| AgyDash
    CFTunnel -->|pimmel.site:5000| AgyDash

    CFTunnel -->|cast, tele, mat, head, port| AuthProxy

    %% Auth Proxy Validation
    AuthProxy <-->|Session & Rights Check| UserDB
    AuthProxy -- "Nicht eingeloggt (Browser GET)" -->|302 Redirect| AgyDash
    AuthProxy -- "Nicht eingeloggt (API/WS)" -->|401 Unauthorized| Client
    AuthProxy -- "Eingeloggt aber fehlendes Recht" -->|403 LCARS Access Denied| Client
    AuthProxy -- "Autorisiert" -->|HTTP / WS / Range Streaming| ProtectedEndpoints

    %% LAN Direct
    LANClient -.->|Direktzugriff ohne Auth| ProtectedEndpoints
    LANClient -.->|Direktzugriff| DirectEndpoints
    LANClient -.->|Direktzugriff| AgyDash
```

---

## 3. Technische Spezifikation des Auth-Reverse-Proxys (`auth_proxy.py` / Port 5050)

### 3.1 Technologie-Wahl & Performance
- **Engine**: Asynchroner Server basierend auf Standardbibliothek `asyncio` (`asyncio.start_server`).
- **Zero-External-Dependencies**: Läuft out-of-the-box mit Python 3.14 Standardbibliothek.
- **Vorteil gegenüber reinem WSGI/Flask**:
  - Echtes, transparentes **Full-Duplex WebSocket Proxying** (`Upgrade: websocket` Handshake + bidirektionales Socket-Piping).
  - Latenzfreies **HTTP Range Request Streaming** (Video/Audio) ohne RAM-Pufferung durch direkte TCP-Chunk-Weiterleitung.
  - Geringster Ressourcenverbrauch (unter 15 MB RAM, 0% CPU im Leerlauf).

### 3.2 Routing- und Subdomain-Auflösung
Der Proxy ermittelt den Zielport anhand des eingehenden HTTP `Host`-Headers:
```python
SUBDOMAIN_PORT_MAP = {
    "cast": {"port": 3000, "service": "pulsecast"},
    "tele": {"port": 8000, "service": "telemetryvault"},
    "mat": {"port": 5580, "service": "matter"},
    "head": {"port": 8787, "service": "headroom"},
    "port": {"port": 631, "service": "cups"},
}
```
- Neue, dynamisch gefundene Webdienste werden über `CloudflaredNamedTunnelManager` zur Laufzeit in das Mapping synchronisiert.

### 3.3 Authentifizierungs- & Autorisierungs-Algorithmus
1. **Header-Analyse**:
   - Extrahiere Cookie `lcars_session`.
   - Extrahiere Client-IP aus `Cf-Connecting-Ip` (Cloudflare-Header) oder `X-Forwarded-For`.
2. **Session-Validierung**:
   - Session-Lookup in Cache / `users.db`.
   - Prüfen: `expires_at > CURRENT_TIMESTAMP`.
   - Prüfen: `user.is_active == 1`.
3. **Fall 1: Keine oder abgelaufene Session**:
   - Handelt es sich um eine HTML-Browser-Anfrage (`Accept: text/html` oder GET auf Web-Ressource):
     ```http
     HTTP/1.1 302 Found
     Location: https://dash.pimmel.site/login?return_to=https%3A%2F%2Fcast.pimmel.site%2Faktueller%2Fpfad
     Cache-Control: no-store, no-cache, must-revalidate
     ```
   - Handelt es sich um eine API/Fetch-Anfrage oder WebSocket-Handshake:
     ```http
     HTTP/1.1 401 Unauthorized
     Content-Type: application/json

     {"error": "Unauthorized", "login_url": "https://dash.pimmel.site/login"}
     ```
4. **Fall 2: Gültige Session, aber fehlendes Service-Recht**:
   - User hat z.B. nur Berechtigung für `["pulsecast"]`, ruft aber `mat.pimmel.site` (Matter) auf:
   - Rückgabe von **HTTP 403 Forbidden** mit stilsicherer LCARS-Fehlerseite:
     ```html
     LCARS SICHERHEITSPROTOKOLL // ZUGRIFF VERWEIGERT
     STATUS: 403 FORBIDDEN // BENUTZER: {username}
     BERECHTIGUNG FÜR DIENST '{service}' NICHT VORHANDEN.
     ```
5. **Fall 3: Voll autorisiert**:
   - Transparentes Durchreichen an den lokalen Zielport (`127.0.0.1:<target_port>`).
   - Anreicherung nützlicher Upstream-Header:
     - `X-Forwarded-User: {username}`
     - `X-Forwarded-Host: {host}`
     - `X-Forwarded-Proto: https`

---

## 4. Datenmodell & Sicherheit (`users.db`)

### 4.1 SQLite Schema
Gespeichert unter `/home/cb/Projects/agydashboard/users.db` mit aktivierter WAL (`PRAGMA journal_mode=WAL;`).

```sql
-- 1. Benutzerverwaltung
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    hash_algo TEXT NOT NULL DEFAULT 'pbkdf2_sha256',
    display_name TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    allowed_services TEXT NOT NULL DEFAULT '[]', -- JSON-Array, z.B. ["pulsecast", "telemetryvault"] oder ["*"]
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP,
    notes TEXT
);

-- 2. Session-Store
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ip_address TEXT,
    user_agent TEXT
);

-- 3. Audit & Security Log
CREATE TABLE IF NOT EXISTS auth_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    username TEXT,
    event TEXT NOT NULL, -- LOGIN_SUCCESS, LOGIN_FAILED, LOGOUT, ACCESS_DENIED, USER_CREATED, USER_DELETED, PASSWORD_CHANGED
    target_service TEXT,
    ip TEXT,
    user_agent TEXT,
    details TEXT
);
```

### 4.2 Passwort-Sicherheit & Hashing-Algorithmus
- **Algorithmus**: `PBKDF2-HMAC-SHA256` mit 600.000 Iterationen (entspricht den aktuellen OWASP Password Storage Guidelines).
- **Salt**: 32 Bytes kryptografischer Zufall via `secrets.token_bytes(32)`.
- **Vergleich**: Konstanter Zeitvergleich via `hmac.compare_digest(calculated_hash, stored_hash)` zur Verhinderung von Side-Channel- / Timing-Angriffen.
- **Modularität**: Automatischer Upgrade-Pfad auf `Argon2id` oder `Bcrypt`, falls diese Bibliotheken im Python-Umfeld nachinstalliert werden.

### 4.3 Session-Management & Cookie-Sicherheit
- **Token-Erzeugung**: 256-Bit Entropie via `secrets.token_urlsafe(32)`.
- **Cookie-Attribute**:
  - `Name`: `lcars_session`
  - `Domain`: `.pimmel.site` (Führender Punkt garantiert Subdomain-weites Senden an `cast.pimmel.site`, `tele.pimmel.site`, etc.)
  - `Path`: `/`
  - `Secure`: `True` (Übertragung ausschließlich via HTTPS über Cloudflare)
  - `HttpOnly`: `True` (Nicht über clientseitiges JavaScript auslesbar, Schutz vor XSS)
  - `SameSite`: `Lax` (Ermöglicht automatische Übertragung bei Weiterleitungen von externen Links)
  - `Max-Age`: Konfigurierbar:
    - Standard: 86.400 Sekunden (24 Stunden).
    - Bei Option "Eingeloggt bleiben" (`remember=true`): 2.592.000 Sekunden (30 Tage).

---

## 5. Authentifizierungs-Flow & LCARS Login-Interface

### 5.1 Ablaufdiagramm (Auth-Flow)

```mermaid
sequenceDiagram
    autonumber
    actor User as Benutzer
    participant CF as Cloudflare Edge
    participant Proxy as Auth-Proxy (Port 5050)
    participant Dash as agydashboard (Port 5000)
    participant DB as users.db (SQLite)
    participant App as Ziel-Dienst (z.B. Port 3000)

    User->>CF: Aufruf https://cast.pimmel.site/downloads
    CF->>Proxy: Ingress HTTP GET (Host: cast.pimmel.site, kein Cookie)
    Proxy->>Proxy: Prüfe Cookie 'lcars_session' -> FEHLT
    Proxy-->>CF: 302 Redirect zu https://dash.pimmel.site/login?return_to=https://cast.pimmel.site/downloads
    CF-->>User: 302 Found
    User->>Dash: GET /login?return_to=https://cast.pimmel.site/downloads
    Dash-->>User: Rendert LCARS Login Terminal
    User->>Dash: POST /api/auth/login {user, pass, remember: true, return_to}
    Dash->>DB: Validiere Hash & erstelle Session
    DB-->>Dash: Session OK (Token: abc...)
    Dash-->>User: Set-Cookie: lcars_session=abc...; Domain=.pimmel.site; Secure; HttpOnly<br>JSON {redirect_url: "https://cast.pimmel.site/downloads"}
    User->>CF: Aufruf https://cast.pimmel.site/downloads (inkl. Cookie!)
    CF->>Proxy: Ingress HTTP GET (Cookie: lcars_session=abc...)
    Proxy->>DB: Validiere Token & prüfe Rechte für 'pulsecast'
    DB-->>Proxy: Valide & Berechtigt!
    Proxy->>App: Forward Request an 127.0.0.1:3000
    App-->>Proxy: Response Data (HTML/Media/JSON)
    Proxy-->>CF: Forward Response Data
    CF-->>User: Darstellung von PulseCast
```

### 5.2 Open-Redirect-Prävention
Der Parameter `return_to` wird vor dem Setzen des Weiterleitungs-Ziels streng validiert:
- Erlaubt sind ausschließlich URLs mit Hostname endend auf `.pimmel.site` (z.B. `https://cast.pimmel.site/*`) oder relative Pfade (z.B. `/`).
- Alle anderen Domains (z.B. `evil.com`) werden strikt verworfen und durch `https://dash.pimmel.site/` ersetzt.

---

## 6. LCARS Administrations-Oberfläche "USER-VERWALTUNG"

### 6.1 Autorisierungskonzept
- **Kein separater Master-Admin-Nutzer**: Die Administration erfolgt direkt über das existierende Sicherheitsmodell mit dem **Command Code `0901`**.
- Nach Eingabe des Command Codes im Dashboard (Bereich `CONFIG` unter `LCARS SICHERHEITSPROTOKOLL`) wird die Sektion `LCARS BENUTZER- & ZUGRIFFSVERWALTUNG` freigeschaltet.

### 6.2 UI-Komponenten in `app.py`
1. **Benutzerliste (LCARS Data Table)**:
   - Spalten:
     - `STATUS`: LCARS Badge (`AKTIV` grün / `GESPERRT` rot).
     - `BENUTZER`: Username und optionaler Anzeigename.
     - `BERECHTIGUNGEN`: Pills für zugeordnete Dienste (`PULSECAST`, `TELEMETRY`, `MATTER`, `HEADROOM`, `CUPS`, `ALLE`).
     - `LETZTER LOGIN`: Datum, Uhrzeit und Herkunfts-IP.
     - `AKTIONEN`:
       - `✏️ EDITIEREN`: Rechte und Details ändern.
       - `🔑 PASSWORT`: Direktes Zurücksetzen/Ändern des Passworts.
       - `🔄 TOGGLE`: Sofortiges Deaktivieren / Aktivieren.
       - `🗑️ LÖSCHEN`: Löschen mit LCARS Bestätigungs-Prompt.
2. **Modal "NEUER BENUTZER"**:
   - Benutzername (mind. 3 Zeichen, nur `a-z0-9_-`).
   - Initial-Passwort (mind. 6 Zeichen mit Sichtbarkeits-Toggle).
   - Berechtigungs-Matrix (Checkboxen):
     - `[ ] Alle Dienste (*)`
     - `[ ] PulseCast (cast.pimmel.site - Media & Downloads)`
     - `[ ] TelemetryVault (tele.pimmel.site - ACC Telemetrie)`
     - `[ ] Matter Server (mat.pimmel.site - Smart Home)`
     - `[ ] Headroom AI (head.pimmel.site - Context Engine)`
     - `[ ] CUPS Druckerdienst (port.pimmel.site - Druckerverwaltung)`
   - Notizfeld (z.B. "Familienmitglied", "Kollege").
3. **LCARS Audit-Log Terminal**:
   - Live-Stream der letzten 25 Authentifizierungs- und Zugriffsereignisse inkl. Farbcodierung (Erfolg grün, Fehlversuche rot, Sperren gelb).

---

## 7. REST-API-Schnittstellenspezifikation

### 7.1 Öffentliche Authentifizierungs-Endpunkte
| Methode | Endpunkt | Beschreibung | Request Body | Response |
|---|---|---|---|---|
| `GET` | `/login` | Rendert LCARS Login-Oberfläche (oder Redirect falls eingeloggt) | - | HTML |
| `POST` | `/api/auth/login` | Führt Login aus & setzt `.pimmel.site` Session-Cookie | `{"username", "password", "remember", "return_to"}` | `{"success": true, "redirect_url": "..."}` |
| `POST` | `/api/auth/logout` | Löscht Session in DB & entfernt Cookie | - | `{"success": true}` |
| `GET` | `/api/auth/me` | Gibt Profil und erlaubte Services des eingeloggten Nutzers zurück | Cookie | `{"authenticated": true, "user": {...}}` |

### 7.2 Administrative Benutzerverwaltung (Erfordert Command Code 0901)
*Header: `X-Command-Code: 0901` oder verifizierte Dashboard-Session.*

| Methode | Endpunkt | Beschreibung | Request Body |
|---|---|---|---|
| `GET` | `/api/users` | Liste aller angelegten Benutzer | - |
| `POST` | `/api/users` | Neuen Benutzer anlegen | `{"username", "password", "allowed_services", "notes"}` |
| `PUT` | `/api/users/<id>` | Benutzerdaten & Rechte bearbeiten | `{"allowed_services", "is_active", "notes"}` |
| `POST` | `/api/users/<id>/password` | Passwort eines Benutzers ändern | `{"new_password"}` |
| `DELETE` | `/api/users/<id>` | Benutzer unwiderruflich löschen | - |
| `GET` | `/api/users/audit-log` | Audit-Events und letzte Anmeldungen | - |

---

## 8. Konfigurationsanpassung: `config.yml` & `CloudflaredNamedTunnelManager`

### 8.1 Neue Ingress-Struktur in `/home/cb/.cloudflared/config.yml`
```yaml
tunnel: b5c7fa60-f43e-487e-bad6-70975ca93823
credentials-file: /home/cb/.cloudflared/b5c7fa60-f43e-487e-bad6-70975ca93823.json

ingress:
  # 1. Zentrale Dashboards & Login
  - hostname: pimmel.site
    service: http://localhost:5000
  - hostname: dash.pimmel.site
    service: http://localhost:5000

  # 2. Direkter Durchgriff (Dienste mit eigenem Login)
  - hostname: ha.pimmel.site
    service: http://localhost:8123
  - hostname: ai.pimmel.site
    service: http://localhost:20128

  # 3. GESCHÜTZTE DIENSTE (Routing über zentralen LCARS Auth-Proxy auf Port 5050)
  - hostname: cast.pimmel.site
    service: http://localhost:5050
  - hostname: tele.pimmel.site
    service: http://localhost:5050
  - hostname: mat.pimmel.site
    service: http://localhost:5050
  - hostname: head.pimmel.site
    service: http://localhost:5050
  - hostname: port.pimmel.site
    service: http://localhost:5050

  # 4. Catch-All Fallback
  - service: http_status:404
```

### 8.2 Anpassung im `CloudflaredNamedTunnelManager` (`app.py`)
- Beim automatischen Entdecken eines neuen Webdienstes wird geprüft, ob er in einer Whitelist für eigene Authentifizierung liegt.
- Alle ungeschützten Dienste werden in `config.yml` mit `service: http://localhost:5050` angelegt, während das interne Subdomain-zu-Port-Mapping dynamisch im Auth-Proxy registriert wird.

---

## 9. Risiken, Edge-Cases & Sicherheitsmaßnahmen

1. **Cookie-Kollisionen mit Subdomains**:
   - Das Cookie `lcars_session` gilt für `.pimmel.site`. Da Home Assistant (`ha`) und 9Router (`ai`) eigene Session-Cookies (`auth_token`, etc.) verwenden, stört das zusätzliche Cookie den Betrieb nicht.
2. **WebSocket Keep-Alive & Timeouts**:
   - Matter Server (`mat`) und PulseCast nutzen WebSockets.
   - Nach Validierung des initialen HTTP-Handshakes schaltet der Proxy auf reines, transparentes TCP-Socket-Streaming um (`asyncio.gather(pipe(r1, w2), pipe(r2, w1))`), sodass keine vorzeitigen HTTP-Timeouts Verbindungen abbrechen.
3. **HTTP Range Requests & Media Streaming**:
   - PulseCast überträgt Filme und Videos über Range Requests. Der Proxy liest und schreibt Datenströme in Chunks (64 KB) direkt durch, ohne den Gesamtrecord im Arbeitsspeicher zu halten.
4. **Timing Attacks**:
   - Alle Passwortvergleiche und Tokenprüfungen verwenden `hmac.compare_digest`.
5. **Cloudflare Header Trust**:
   - Client-IPs werden aus `Cf-Connecting-Ip` bezogen, da der Zugriff über den offiziellen Named Tunnel erfolgt.
6. **Graceful Fallback bei Teilausfall**:
   - Da Home Assistant (`ha`) und 9Router (`ai`) direkt in `config.yml` konfiguriert sind, bleiben sie selbst bei Wartungsarbeiten oder Neustarts des Auth-Proxys unterbrechungsfrei erreichbar.

---

## 10. Detaillierter Umsetzungs- und Verifikationsplan

### Phase 1: Datenhaltung & User-Service (`user_service.py`)
- Implementierung der SQLite-Datenbank `users.db` mit Tabellen `users`, `sessions`, `auth_audit_log`.
- PBKDF2-HMAC-SHA256 mit 600.000 Iterationen und 32-Byte Salting.
- Unit-Tests für Password-Hashing, Session-Generierung und Rechteabfrage.

### Phase 2: Asynchroner Auth Reverse Proxy (`auth_proxy.py`)
- Implementierung des Proxy-Servers auf `127.0.0.1:5050` mit `asyncio`.
- Parsing von HTTP Request-Headern (`Host`, `Cookie`, `Upgrade`).
- Session- und Rechte-Validierung.
- Weiterleitung von HTTP, Streaming & WebSockets an die Ziel-Ports (3000, 8000, 5580, 8787, 631).
- Automatisierter Test mit simulierten Requests (unauthentifiziert -> 302 / 401; authentifiziert -> 200).

### Phase 3: Login-Flow & API-Endpunkte in `app.py`
- Integration von `/login` mit LCARS-Benutzeroberfläche.
- Endpunkte `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`.
- Setzen des sicheren Cookies für `.pimmel.site`.

### Phase 4: LCARS User-Verwaltung in `app.py` (Command Code 0901)
- Freischaltung der User-Verwaltung im Bereich `CONFIG` nach Eingabe von `0901`.
- Endpunkte `/api/users` (GET, POST, PUT, DELETE) und `/api/users/audit-log`.
- Interaktive UI: User anlegen, Rechte pro Dienst zuweisen, Passwort ändern, löschen.

### Phase 5: Tunnel-Umstellung & End-to-End Verifikation
- Aktualisierung von `/home/cb/.cloudflared/config.yml` (Geschützte Dienste auf Port 5050).
- Validierung via `/usr/bin/cloudflared tunnel --config /home/cb/.cloudflared/config.yml ingress validate`.
- Neustart via `systemctl --user restart cloudflared`.
- End-to-End-Prüfung aller Subdomains im Browser.

---

# Architektur- & Implementierungsplan: PulseCast LCARS "LOKAL"-Medienarchiv (`app.py`)

## 1. Zielsetzung & Kontext

Erweiterung der **PulseCast**-Sektion im LCARS-Dashboard (`app.py`) um einen eigenständigen Reiter **"LOKAL"** (Icon 📁), in dem ausschließlich lokal im Filesystem vorhandene, heruntergeladene Mediendateien (Filme, Serienepisoden, Audio) übersichtlich und performant dargestellt werden.

### Ausgangssituation
- Das LCARS-Dashboard bietet derzeit in `#pulsecastActiveContent` drei Reiter:
  1. `DOWNLOADS`: Aktive und abgeschlossene Transfers / Warteschlange.
  2. `KATALOG-BROWSER`: Remote-Kataloge (Filme / Serien aus Subraum-Relays wie Xtream/PulseCast).
  3. `XDCC-SUCHE`: IRC- und XDCC-Paketsuche.
- Die PulseCast-Backend-API (`xdcc-load-cast` auf Port 3000) scannt und indiziert das lokale Download-Verzeichnis automatisch und stellt die lokalen Medien über `/api/media-library?category=Lokal` (sowie `Lokal_Filme` und `Lokal_Serien`) bereit.
- Lokale Mediendateien können über den bestehenden nativen HTTP-Range-Stream-Endpunkt `/api/pulsecast/media/stream/<filename>` oder Transcode-Endpunkt `/api/pulsecast/media/transcode/<filename>` gestreamt werden.
- Es fehlt im Frontend bisher ein dedizierter LCARS-Reiter zur Verwaltung, Durchsuchung, Filterung und Wiedergabe der lokal vorhandenen Dateien.

---

## 2. System- & Komponentenarchitektur

```mermaid
flowchart TD
    User([LCARS Benutzer]) --> Subnav[PulseCast Subnav-Pills]
    Subnav -->|Klick '📁 LOKAL'| SwitchTab[switchPulsecastSubtab('local')]
    
    SwitchTab --> LoadData[loadPulsecastLocal(page)]
    LoadData --> API["GET /api/pulsecast/media-library\n?category=Lokal(_Filme|_Serien)&search=...&page=...&limit=40"]
    
    API --> BackendProxy["Flask / Python Handler (_pulsecast_proxy in app.py)"]
    BackendProxy --> NodeService["xdcc-load-cast Service (Port 3000)"]
    NodeService --> LocalFS["Lokales Dateisystem (/downloads/...)"]
    
    NodeService -->|JSON Payload mit items, counts, totalPages| LoadData
    
    LoadData --> UpdateBadges["Counts & Badges aktualisieren\n(Tab-Badge, Filter-Pills, Pagination)"]
    LoadData --> ViewCheck{"pulsecastLocalViewMode?"}
    
    ViewCheck -->|'grid'| RenderGrid["renderPulsecastLocalGrid(items)\n(Poster, Titel, Jahr, Größe, ▶ PLAYER / 📋 EPISODEN)"]
    ViewCheck -->|'list'| RenderList["renderPulsecastLocalList(items)\n(Tabelle: Typ, Dateiname, Format, Größe, Datum, ▶ ÖFFNEN)"]
    
    RenderGrid --> PlayerModal["openPulsecastPlayerModal(filename, title)"]
    RenderList --> PlayerModal
    RenderGrid --> SeriesModal["openPulsecastLocalSeriesModal(group)"]
    SeriesModal --> PlayerModal
```

---

## 3. DOM-Struktur in `DASHBOARD_HTML`

### 3.1 Subnav-Button
In `#pulsecastActiveContent` wird der Reiter-Button an zweiter Position (direkt nach `DOWNLOADS`) eingefügt:

```html
<button type="button" class="lcars-subnav-pill" id="pulsecast-tab-btn-local" onclick="switchPulsecastSubtab('local')">
  <span>📁</span> <span>LOKAL</span>
  <span id="pulsecastLocalCountBadge" style="background:rgba(0,0,0,0.5); padding:2px 8px; border-radius:12px; font-size:0.75rem; margin-left:4px;">0</span>
</button>
```

### 3.2 Subview-Container `#pulsecast-subview-local`
Wird als `<div id="pulsecast-subview-local" class="pulsecast-subview" style="display:none;">` zwischen Downloads und Katalog eingebettet:

```html
<!-- SUBVIEW 1b: LOKALE MEDIEN -->
<div id="pulsecast-subview-local" class="pulsecast-subview" style="display:none;">
  <div class="lcars-card" style="margin-bottom:1.25rem; padding:1rem; border-top:3px solid var(--c-butterscotch);">
    
    <!-- Filter & Toolbar -->
    <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:0.75rem; margin-bottom:1rem; background:rgba(0,0,0,0.3); padding:0.75rem; border-radius:6px; border:1px solid rgba(255,255,255,0.06);">
      
      <!-- Typ-Filter (ALLE / FILME / SERIEN) -->
      <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
        <button type="button" class="lcars-pill-btn active" id="local-pill-all" onclick="setPulsecastLocalCategory('Lokal')" style="height:38px; padding:0 1.2rem; font-size:0.85rem; background:var(--c-butterscotch); color:#000; font-weight:700;">
          📁 ALLE <span id="pulsecastLocalCountAll" style="margin-left:4px; font-size:0.75rem; opacity:0.85;"></span>
        </button>
        <button type="button" class="lcars-pill-btn" id="local-pill-Filme" onclick="setPulsecastLocalCategory('Lokal_Filme')" style="height:38px; padding:0 1.2rem; font-size:0.85rem; background:rgba(0,0,0,0.5); color:var(--c-primary); border:1px solid var(--c-primary);">
          🎬 FILME <span id="pulsecastLocalCountFilme" style="margin-left:4px; font-size:0.75rem; opacity:0.85;"></span>
        </button>
        <button type="button" class="lcars-pill-btn" id="local-pill-Serien" onclick="setPulsecastLocalCategory('Lokal_Serien')" style="height:38px; padding:0 1.2rem; font-size:0.85rem; background:rgba(0,0,0,0.5); color:var(--c-secondary); border:1px solid var(--c-secondary);">
          📺 SERIEN <span id="pulsecastLocalCountSerien" style="margin-left:4px; font-size:0.75rem; opacity:0.85;"></span>
        </button>
      </div>

      <!-- Ansichts-Umschalter (Raster vs. Liste) -->
      <div style="display:flex; gap:0.4rem; align-items:center;">
        <button type="button" class="lcars-pill-btn active" id="pulsecastLocalViewGridBtn" onclick="setPulsecastLocalViewMode('grid')" style="height:38px; padding:0 0.9rem; font-size:0.82rem; background:var(--c-gold); color:#000; font-weight:700;" title="Kachel-Rasteransicht">
          <span>⊞</span> <span>RASTER</span>
        </button>
        <button type="button" class="lcars-pill-btn" id="pulsecastLocalViewListBtn" onclick="setPulsecastLocalViewMode('list')" style="height:38px; padding:0 0.9rem; font-size:0.82rem; background:rgba(0,0,0,0.5); color:#aaa; border:1px solid rgba(255,255,255,0.2);" title="Kompakte Tabellen-/Listenansicht">
          <span>☰</span> <span>LISTE</span>
        </button>
      </div>

      <!-- Schnellsuche mit Enter & Reset -->
      <div style="display:flex; align-items:center; gap:0.4rem; min-width:240px; flex:1; max-width:380px;">
        <input type="text" id="pulsecastLocalSearchInput" placeholder="Lokale Medien suchen..." class="lcars-input" style="height:38px; font-size:0.85rem; flex:1;" onkeydown="if(event.key==='Enter') pulsecastLocalSearchTrigger();">
        <button type="button" class="left-action-btn" onclick="pulsecastLocalSearchTrigger()" style="height:38px; padding:0 0.9rem; font-size:0.82rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch);" title="Suche ausführen">
          <span>🔍</span>
        </button>
        <button type="button" class="left-action-btn" onclick="pulsecastLocalSearchClear()" style="height:38px; padding:0 0.6rem; font-size:0.82rem; border-color:#888; color:#888;" title="Filter zurücksetzen">
          ✕
        </button>
      </div>
    </div>

    <!-- LCARS Loading State -->
    <div id="pulsecastLocalLoading" style="text-align:center; padding:3rem 1rem; display:none;">
      <div style="font-family:var(--font-family); font-size:1.1rem; color:var(--c-butterscotch); letter-spacing:0.06em; margin-bottom:0.5rem;">
        DATENKASKADE WIRD GELADEN...
      </div>
      <div style="font-family:var(--mono-family); font-size:0.85rem; color:#888;">
        Lokales Medienarchiv wird synchronisiert
      </div>
    </div>

    <!-- Ansicht 1: Kachel-Raster -->
    <div id="pulsecastLocalGrid" class="pulsecast-catalog-grid" style="min-height:280px; display:grid;">
      <!-- Dynamisch befüllte Kacheln -->
    </div>

    <!-- Ansicht 2: Kompakte Listenansicht (Tabelle) -->
    <div id="pulsecastLocalListContainer" style="overflow-x:auto; display:none;">
      <table class="services-table" id="pulsecastLocalTable" style="width:100%;">
        <thead>
          <tr style="position:sticky; top:0; background:#111; z-index:2;">
            <th style="width:55px; text-align:center;">TYP</th>
            <th>DATEINAME / TITEL</th>
            <th style="width:90px;">FORMAT</th>
            <th style="width:110px; color:#44dd88;">GRÖSSE</th>
            <th style="width:145px; color:var(--c-gold);">ÄNDERUNG</th>
            <th style="width:140px; text-align:right;">AKTION</th>
          </tr>
        </thead>
        <tbody id="pulsecastLocalTableBody">
          <!-- Dynamisch befüllte Zeilen -->
        </tbody>
      </table>
    </div>

    <!-- Empty State -->
    <div id="pulsecastLocalEmpty" style="text-align:center; padding:3rem 1rem; color:#888; font-family:var(--mono-family); display:none;">
      KEINE LOKALEN MEDIEN FÜR DIESE FILTERUNG VORHANDEN
    </div>

    <!-- Paginierungs-Leiste -->
    <div id="pulsecastLocalPaginationBar" style="display:flex; justify-content:center; align-items:center; gap:0.75rem; margin-top:1.5rem; flex-wrap:wrap;">
      <button type="button" class="left-action-btn" id="pulsecastLocalPrevPageBtn" onclick="pulsecastLocalChangePage(-1)" style="padding:0.4rem 1.1rem; font-size:0.85rem;">
        ◀ VORHERIGE
      </button>
      <span id="pulsecastLocalPageIndicator" style="font-family:var(--mono-family); font-size:0.9rem; color:var(--c-gold); padding:0 0.5rem;">
        SEITE 1 VON 1 (0 EINTRÄGE)
      </span>
      <button type="button" class="left-action-btn" id="pulsecastLocalNextPageBtn" onclick="pulsecastLocalChangePage(1)" style="padding:0.4rem 1.1rem; font-size:0.85rem;">
        NÄCHSTE ▶
      </button>
    </div>
  </div>
</div>
```

---

## 4. Frontend State & JavaScript-Funktionen

### 4.1 State-Variablen
```javascript
let pulsecastLocalCategory = 'Lokal';          // 'Lokal' (Alle) | 'Lokal_Filme' | 'Lokal_Serien'
let pulsecastLocalViewMode = 'grid';           // 'grid' | 'list'
let pulsecastLocalSearchQuery = '';
let pulsecastLocalPage = 1;
let pulsecastLocalTotalPages = 1;
let pulsecastLocalItemsCache = [];
```

### 4.2 Funktionsspezifikation

| Funktion | Parameter | Zweck |
|---|---|---|
| `switchPulsecastSubtab(subtab)` | `subtab: string` | Umschalten auf Subtab `'local'`. Ruft `loadPulsecastLocal(pulsecastLocalPage)` auf. |
| `setPulsecastLocalCategory(cat)` | `cat: string` | Setzt Filter (`'Lokal'`, `'Lokal_Filme'`, `'Lokal_Serien'`). Passt Button-Styling an. Setzt `page = 1` und triggert Ladevorgang. |
| `setPulsecastLocalViewMode(mode)` | `mode: 'grid' \| 'list'` | Schaltet zwischen Raster und Liste um, passt Styling der Umschaltbuttons an, blendet Container ein/aus und rendert den Cache neu. |
| `pulsecastLocalSearchTrigger()` | – | Liest Wert aus `#pulsecastLocalSearchInput`, setzt `page = 1`, führt `loadPulsecastLocal(1)` aus. |
| `pulsecastLocalSearchClear()` | – | Leert Input und Query, lädt Seite 1 neu. |
| `pulsecastLocalChangePage(delta)` | `delta: number` | Paginierung mit Bounds-Prüfung (`target >= 1 && target <= totalPages`). |
| `loadPulsecastLocal(page)` | `page: number` | Asynchroner Fetch gegen `/api/pulsecast/media-library` mit Fehlerbehandlung, Spinner-Steuerung, Badge-Update und Delegation ans Rendering. |
| `renderPulsecastLocalGrid(items)` | `items: Array` | Kachel-Rendering: Cover/Poster, Fallback-Icons, Jahr, Titel, Dateigröße/Episoden-Anzahl, "▶ IN PLAYER ÖFFNEN" oder "📋 EPISODEN". |
| `renderPulsecastLocalList(items)` | `items: Array` | Tabellen-Rendering: Typ-Icon, Dateiname / Pfad, Dateiendung/Format, lesbare Größe (`formatBytes`), formatiertes Datum, Aktions-Button. |
| `openPulsecastLocalGroupModal(idx)` | `idx: number` | Öffnet das Episoden-Modal für lokale Serien-Gruppen (`isGroup: true`), in dem alle Folgen direkt mit "▶ IN PLAYER ÖFFNEN" abspielbar sind. |
| `updatePulsecastLocalCounts(counts)` | `counts: Object` | Aktualisiert den Tab-Badge `#pulsecastLocalCountBadge` sowie die Filter-Zähler (`counts.Lokal`, `counts.Lokal_Filme`, `counts.Lokal_Serien`). |

---

## 5. Backend & API-Integration

### 5.1 Endpunkt-Routing in `app.py`
Die bestehende Flask-Route in Zeile 15528 leitet alle Parameter transparent an `xdcc-load-cast` weiter:
```python
@app.route("/api/pulsecast/media-library", methods=["GET"])
def api_pulsecast_media_library():
    params = {
        "category": request.args.get("category", "Filme"),
        "subcategory": request.args.get("subcategory", "all"),
        "search": request.args.get("search", ""),
        "page": request.args.get("page", 1),
        "limit": request.args.get("limit", 40)
    }
    return _pulsecast_proxy("GET", "/api/media-library", params=params, timeout=20)
```
Ebenso unterstützt die Fallback-Route (`BaseHTTPRequestHandler` Zeile 16130) transparente GET-Weiterleitungen an `http://127.0.0.1:3000/api/media-library?...`.

### 5.2 Datenstruktur der API-Antwort
```json
{
  "items": [
    {
      "filename": "Filme/Mayday (2026) NEU.mkv",
      "sizeBytes": 4294967295,
      "mtime": 1789899068000,
      "metadata": {
        "title": "Mayday",
        "category": "Lokal",
        "year": 2026,
        "isSeries": false,
        "posterUrl": "https://...",
        "subcategory": "Filme"
      },
      "isXtream": false
    },
    {
      "isGroup": true,
      "isXtream": false,
      "title": "Stuart Fails to Save the Universe",
      "posterUrl": "https://...",
      "year": 2026,
      "category": "Lokal",
      "subcategory": "Serien",
      "files": [
        {
          "filename": "Serien/Stuart Fails.../S01E06.mkv",
          "sizeBytes": 1757902664,
          "mtime": 1788459328000,
          "metadata": {
            "title": "Stuart Fails...",
            "seasonEpisode": "S01E06"
          }
        }
      ]
    }
  ],
  "totalItems": 467,
  "totalPages": 94,
  "currentPage": 1,
  "counts": {
    "all": 46927,
    "Lokal": 567,
    "Lokal_Filme": 174,
    "Lokal_Serien": 134,
    "Filme": 37200,
    "Serien": 5835
  }
}
```

---

## 6. UI/UX-Konventionen & LCARS-Design

1. **Farbschema**:
   - Primäre Akzentfarbe für LOKAL: `var(--c-butterscotch)` (`#eb943a`) und `var(--c-gold)` (`#e8b030`).
   - Filme-Akzent: `var(--c-primary)` (`#ff9900`).
   - Serien-Akzent: `var(--c-secondary)` (`#b464ff`).
   - Dateigrößen & Status: `#44dd88` (Grün) bzw. `var(--c-gold)`.
2. **Audio-Feedback**:
   - Aufruf von `playLcarsBeep(frequency, duration)` bei Interaktionen (Subtab-Wechsel, Filterwechsel, Paginierung).
3. **Escaping-Regeln für Multiline-Strings (`DASHBOARD_HTML`)**:
   - `DASHBOARD_HTML` ist in Python als `"""..."""` definiert.
   - **Strikte Regel**: Keine unescaped `\/` in JavaScript RegExp oder Strings verwenden! Für Pfad-Ersetzungen `replace(/^[\\/]+/g, '')` oder `startsWith('/')` nutzen.
   - Strings in HTML-Attributen und Onclick-Handlern mit `escapeHtml()` und `escapeJsString()` absichern.

---

## 7. Risiken, Edge-Cases & Absicherungen

| Risiko / Randfall | Ursache | Vermeidungsstrategie |
|---|---|---|
| **Sonderzeichen / Quotes in Dateinamen** | Dateinamen mit einfachen/doppelten Anführungszeichen, Umlauten oder Klammern brechen JS-Funktionsaufrufe | Verwendung der bestehenden Hilfsfunktion `escapeJsString()` und `escapeHtml()` für alle HTML-Attribute und Onclick-Handler. |
| **Python Multiline String Escaping** | `invalid escape sequence '\/'` Warnung/Syntaxfehler in Python 3.12+ | Absolute Vermeidung von `\/` in RegExp innerhalb von `DASHBOARD_HTML`. |
| **Große Bibliotheken / Ladezeit** | Hunderte lokale Dateien führen bei fehlender Paginierung zu DOM-Überlastung | Feste Limitierung auf `limit=40` pro Seite mit Server-Paginierung über den existierenden Endpunkt. |
| **Serien vs. Einzelfilme** | Serien liegen als Gruppen (`isGroup: true`) mit eingebetteten `files` vor | Kacheln zeigen für Serien "X Episoden" mit Button "📋 EPISODEN", der die Einzelfolgen mit separaten Player-Buttons öffnet. |
| **Leere Treffermenge / Offline-Relay** | Suchbegriff liefert keine Treffer oder Backend nicht erreichbar | LCARS Empty-State `#pulsecastLocalEmpty` bzw. automatische Status-Prüfung via `checkPulsecastStatus()`. |

---

## 8. Verifikations- und Testplan

### Phase 1: Statische Code- und Syntaxprüfung
- Python-Syntaxprüfung: `python3 -m py_compile app.py` (muss fehlerfrei ohne Warnings kompilieren).
- Regex-Escaping-Audit: Sicherstellen, dass kein `\/` in `app.py` neu eingeführt wurde.

### Phase 2: Backend-Endpunktprüfung
- Aufruf von `/api/pulsecast/media-library?category=Lokal&limit=5` via `curl`.
- Aufruf von `/api/pulsecast/media-library?category=Lokal_Filme&limit=5`.
- Aufruf von `/api/pulsecast/media-library?category=Lokal_Serien&limit=5`.
- Verifikation von `data.counts.Lokal` und `data.items`.

### Phase 3: Frontend- und Interaktionsprüfung
1. Dashboard im Browser öffnen: Tab "📁 LOKAL" muss neben DOWNLOADS sichtbar sein und den Zähler `(567)` tragen.
2. Klick auf "📁 LOKAL":
   - Subview `#pulsecast-subview-local` öffnet sich.
   - Filter "📁 ALLE (567)", "🎬 FILME (174)", "📺 SERIEN (134)" sind aktiv und klickbar.
3. Kachelansicht:
   - Cover, Titel, Jahr, Dateigröße werden gerendert.
   - Klick auf "▶ IN PLAYER ÖFFNEN" öffnet das LCARS Player-Modal mit funktionierender M3U/Stream-URL.
4. Listenansicht:
   - Klick auf "☰ LISTE" wechselt unterbrechungsfrei in die tabellarische Übersicht mit Dateinamen, Format, Größe, Datum und Öffnen-Button.
5. Suche & Filter:
   - Eingabe eines Begriffs (z.B. "Mayday") filtert die lokalen Medien korrekt.
   - Klick auf "✕" setzt Filter zurück.
6. Paginierung:
   - Seitenwechsel ◀ Vorherige / Nächste ▶ funktioniert mit korrekter Anzeige "SEITE X VON Y".

---

# Architektur- & Implementierungsplan: Google Gemini 3.8 Live Integration (SST: Schnittstelle + LCARS UI)

## 1. Zielsetzung & Übersicht

Integration des multimodalen Echtzeit-Sprachmodells **Google Gemini 3.8 Live** in das LCARS System Dashboard (`agydashboard`). Das System ermöglicht eine bidirektionale, latenzarme Sprachkommunikation (Speech-to-Speech / SST: Speech-to-Text & Text-to-Speech Relay) direkt aus dem Browser im authentischen Star Trek LCARS Bordcomputer-Design ("SUBRAUM COMM").

### 1.1 Kernanforderungen & Spezifikationen
| Parameter | Wert / Spezifikation |
|---|---|
| **Schnittstellen-Typ** | Bidirektionales Audio- und Text-Streaming via WebSockets |
| **Backend-Endpunkt** | `ws(s)://<host>:5000/api/gemini-live/ws` (via `flask-sock` / WebSocket Handler) |
| **Google Live API URL** | `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key=<api_key>` |
| **Primäres Modell** | `gemini-3.8-live` (Ressource: `models/gemini-3.8-live`) |
| **Modell-Fallback** | **Kein Fallback** (Strikte Bindung an `gemini-3.8-live`) |
| **Audio-Eingabe (Browser -> Gemini)** | PCM 16-Bit Mono, 16.000 Hz Little-Endian (`audio/pcm;rate=16000`) |
| **Audio-Ausgabe (Gemini -> Browser)** | PCM 16-Bit Mono, 24.000 Hz Little-Endian (`audio/pcm;rate=24000`) |
| **Sicherheitskonfiguration** | Registrierung als `gemini_live` in `VALID_SECTIONS` (`permissions_service.py`) |
| **Zugangsschutz** | Standardmäßig in `locked_sections` (`config.json`), Freigabe nur via Command-Code `0901` |
| **Interaktionsmodi** | Push-to-Talk (PTT via Button / Leertaste) & Toggle-Live-Modus (Dauerhafte Duplex-Verbindung) |
| **LCARS UI-Elemente** | Nav-Button `SUBRAUM COMM`, Gate-View, Dual Audio-Visualizer (Pegelanzeige), Live-Transkriptionsterminal |

---

## 2. System- & Komponentenarchitektur

### 2.1 Datenfluss- und Streaming-Architektur

```mermaid
flowchart TD
    subgraph Frontend["LCARS Frontend (Browser)"]
        MIC[Mikrofon MediaStream] --> RESAMP[Web Audio Downsampler 16kHz PCM]
        RESAMP --> WS_CLIENT[WebSocket Client /api/gemini-live/ws]
        WS_CLIENT --> VIS_IN[Input Visualizer Pegel]
        
        WS_CLIENT --> AUDIO_QUEUE[Audio Queue 24kHz PCM]
        AUDIO_QUEUE --> AUDIO_CTX[AudioContext Destination Speaker]
        AUDIO_QUEUE --> VIS_OUT[Output Visualizer Pegel]
        WS_CLIENT --> TRANSCRIPT[Live Transkriptionsterminal]
    end

    subgraph Dashboard["agydashboard Server (Port 5000)"]
        AUTH_GATE{Command-Code 0901 autorisiert?}
        CF_IN[Cloudflare Named Tunnel / LAN] --> AUTH_GATE
        AUTH_GATE -- Nein --> WS_REJECT[HTTP 403 / Close Frame]
        AUTH_GATE -- Ja --> FLASK_SOCK[Flask-Sock / Relay Handler]
        
        FLASK_SOCK <--> RELAY_CORE[Bi-Directional Relay Engine]
        CFG[(config.json: gemini_live)] -.-> RELAY_CORE
    end

    subgraph GoogleCloud["Google Gemini Live API"]
        BIDI_EP[wss://generativelanguage.googleapis.com/.../BidiGenerateContent]
        GEMINI_MODEL["Modell: gemini-3.8-live"]
        BIDI_EP <--> GEMINI_MODEL
    end

    RELAY_CORE <-->|WSS + Setup + Audio/Text Frames| BIDI_EP
```

### 2.2 Sequenzdiagramm: Verbindungsaufbau, Session & Interruption

```mermaid
sequenceDiagram
    autonumber
    participant UI as LCARS Frontend
    participant Relay as Dashboard Backend (/api/gemini-live/ws)
    participant Perm as permissions_service
    participant Google as Gemini Live API (BidiGenerateContent)

    UI->>Relay: WS Connect /api/gemini-live/ws?code=0901
    Relay->>Perm: verify_code(0901) & is_locked("gemini_live")
    alt Code ungültig oder gesperrt
        Relay-->>UI: Close Frame / Error 4403 (Zugriff verweigert)
    else Autorisierung erfolgreich
        Relay->>Google: WSS Connect (?key=<API_KEY>)
        Relay->>Google: Setup Payload {"setup": {"model": "models/gemini-3.8-live", "generationConfig": {"responseModalities": ["AUDIO"]}}}
        Google-->>Relay: {"setupComplete": {}}
        Relay-->>UI: {"type": "setup_complete", "status": "ready"}
    end

    Note over UI,Google: Sprachübertragung (Push-to-Talk oder Continuous Live)
    loop Audio Streaming (Upstream)
        UI->>Relay: {"type": "audio", "data": "<base64 PCM 16kHz>"}
        Relay->>Google: {"realtimeInput": {"mediaChunks": [{"mimeType": "audio/pcm;rate=16000", "data": "..."}]}}
    end

    loop Modell-Antwort (Downstream)
        Google-->>Relay: {"serverContent": {"modelTurn": {"parts": [{"inlineData": {"data": "...", "mimeType": "audio/pcm;rate=24000"}}, {"text": "Transkript..."}]}}}
        Relay-->>UI: {"type": "model_turn", "audio": "<base64>", "rate": 24000, "text": "Transkript..."}
        UI->>UI: AudioBuffer 24kHz abspielen + Visualizer + Transkript anzeigen
    end

    Note over UI,Google: User unterbricht das Modell (Barge-In)
    UI->>Relay: Weiteres Audio senden (User spricht dazwischen)
    Google-->>Relay: {"serverContent": {"interrupted": true}}
    Relay-->>UI: {"type": "interrupted"}
    UI->>UI: Sofortige Stummschaltung aller laufenden/gequeuten AudioBuffer
```

---

## 3. Tech-Stack & Abhängigkeiten

### 3.1 Backend
- **Python-Laufzeitumgebung**: Python 3.14 (Arch Linux / Omarchy System-Python).
- **Web-Framework**: `Flask` 3.1.3 (bereits aktiv in `app.py`).
- **WebSocket-Unterstützung**: 
  - `flask-sock` (integriert sich direkt in die bestehende Werkzeug WSGI-Laufzeit von `app.run(threaded=True)` via Socket-Hijacking).
  - `simple-websocket` (Transport- und Frame-Engine für `flask-sock`).
  - `websockets` (Version 16+ / 17+, synchrone oder asynchrone Client-Engine `websockets.sync.client` für die ausgehende Verbindung zur Google Live API).
- **Installation der Pakete**:
  - `uv pip install --python /usr/bin/python3 --break-system-packages flask-sock websockets` (oder entsprechende Systempakete via `pacman -S python-simple-websocket python-websockets`).

### 3.2 Frontend
- **Web Audio API**:
  - `navigator.mediaDevices.getUserMedia`: Erfassung des Audio-Input-Streams mit Rauschunterdrückung (`noiseSuppression`), Echokompensation (`echoCancellation`) und automatischer Pegelanpassung (`autoGainControl`).
  - `AudioContext`: Generierung und Taktung des Audio-Graphs.
  - `AudioWorkletNode` / Inline Resampling: Konvertierung beliebiger Hardware-Abtastraten (44.1kHz, 48kHz) in striktes 16kHz PCM (Int16Array).
  - `AnalyserNode`: Frequenz- und RMS-Pegel-Extraktion für den LCARS Dual-Visualizer.
  - `AudioBufferSourceNode`: Jitter-freies Queuing von empfangenem 24kHz PCM Audio.

---

## 4. Konfigurations- & Zugriffsschutz-Konzept

### 4.1 Erweiterung von `config.json`
Ein neuer Konfigurationsabschnitt `"gemini_live"` wird auf oberster Ebene integriert:

```json
{
  "gemini_live": {
    "api_key": "<GEMINI_LIVE_API_KEY>",
    "model": "gemini-3.8-live",
    "voice": "Puck",
    "system_instruction": "Du bist der LCARS Bordcomputer der USS Antigravity. Du interagierst direkt mit dem Commander über Subraum-Audio. Antworte stets präzise, professionell, hilfsbereit und im authentischen Ton eines Starfleet Computer-Terminals auf Deutsch.",
    "temperature": 0.6
  },
  "permissions": {
    "command_code": "0901",
    "locked_sections": [
      "agents",
      "ai-info",
      "config",
      "fantasy",
      "homeassistant",
      "cycle",
      "pulsecast",
      "gemini_live"
    ]
  }
}
```

### 4.2 Integration in `permissions_service.py`
1. **Erweiterung von `VALID_SECTIONS`**:
   ```python
   VALID_SECTIONS = [
       "system",
       "services",
       "agents",
       "ai-info",
       "config",
       "fantasy",
       "solar",
       "homeassistant",
       "cycle",
       "pulsecast",
       "gemini_live",  # Neu für Subraum Comm
   ]
   ```
2. **Erweiterung von `DEFAULT_LOCKED_SECTIONS`**:
   `gemini_live` wird standardmäßig als geschützte Sektion hinterlegt.
3. **Validierungsmethode in `app.py`**:
   ```python
   def _gemini_live_authorized():
       if not permissions_service:
           return True
       with permissions_service.lock:
           if "gemini_live" not in permissions_service.locked_sections:
               return True
       code = (
           request.headers.get("X-Command-Code")
           or request.headers.get("X-Auth-Code")
           or request.args.get("code")
       )
       if not code and request.is_json:
           b = request.get_json(silent=True) or {}
           code = b.get("code")
       if not code:
           auth_hdr = request.headers.get("Authorization", "")
           if auth_hdr.startswith("Bearer "):
               code = auth_hdr.split(" ", 1)[1].strip()
       return permissions_service.verify_code(code)
   ```

---

## 5. Backend-Schnittstellenspezifikation (`app.py`)

### 5.1 Endpunkt-Definition
- **Route**: `@sock.route("/api/gemini-live/ws")`
- **Protokoll**: WebSocket (`ws://` bzw. `wss://`)
- **Query-Parameter**: `?code=<command_code>` (z.B. `0901`)

### 5.2 Handshake & Autorisierung
1. Überprüfung des übergebenen Command-Codes via `permissions_service.verify_code(code)`.
2. Bei Sperre und ungültigem Code: Senden eines JSON-Fehlers `{"type": "error", "error": "LCARS Zugriff verweigert: Ungültiger Command Code", "locked": true}` und sofortiges Schließen des WebSockets mit Code `4403`.
3. Auslesen von `api_key`, `model` (`gemini-3.8-live`), `voice` und `system_instruction` aus `config.json`.

### 5.3 Google Live API Verbindungsaufbau
- **WebSocket-Verbindungsziel**:
  `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key=<api_key>`
- **Initiales Setup-Paket**:
  ```json
  {
    "setup": {
      "model": "models/gemini-3.8-live",
      "generationConfig": {
        "responseModalities": ["AUDIO"],
        "speechConfig": {
          "voiceConfig": {
            "prebuiltVoiceConfig": {
              "voiceName": "Puck"
            }
          }
        },
        "temperature": 0.6
      },
      "systemInstruction": {
        "parts": [
          {
            "text": "Du bist der LCARS Bordcomputer der USS Antigravity. Du interagierst direkt mit dem Commander über Subraum-Audio. Antworte stets präzise, professionell, hilfsbereit und im authentischen Ton eines Starfleet Computer-Terminals auf Deutsch."
          }
        ]
      }
    }
  }
  ```

### 5.4 Datenpakete & Übertragungsprotokoll

#### A. Client -> Backend -> Google (Audio-Input)
- **Client sendet**:
  ```json
  {
    "type": "audio",
    "data": "<Base64-kodierte 16-Bit PCM 16kHz Audiodaten>"
  }
  ```
- **Backend leitet weiter an Google**:
  ```json
  {
    "realtimeInput": {
      "mediaChunks": [
        {
          "mimeType": "audio/pcm;rate=16000",
          "data": "<Base64-kodierte PCM Chunks>"
        }
      ]
    }
  }
  ```

#### B. Google -> Backend -> Client (Audio-Output & Transkript)
- **Google liefert**:
  ```json
  {
    "serverContent": {
      "modelTurn": {
        "parts": [
          {
            "inlineData": {
              "mimeType": "audio/pcm;rate=24000",
              "data": "<Base64-kodierte 24kHz PCM Chunks>"
            }
          },
          {
            "text": "Bordcomputer bereit. Wie kann ich behilflich sein, Commander?"
          }
        ]
      },
      "interrupted": false,
      "turnComplete": true
    }
  }
  ```
- **Backend liefert an Client**:
  ```json
  {
    "type": "model_turn",
    "audio": "<Base64-kodierte 24kHz PCM Chunks>",
    "rate": 24000,
    "text": "Bordcomputer bereit. Wie kann ich behilflich sein, Commander?",
    "interrupted": false,
    "turnComplete": true
  }
  ```

#### C. Unterbrechung (Barge-In)
- Wenn Google `"interrupted": true` meldet, sendet das Backend an den Client:
  ```json
  {
    "type": "interrupted"
  }
  ```
- Das Frontend stoppt unverzüglich alle abgespielten und in der Warteschlange befindlichen Audio-Chunks.

---

## 6. LCARS Frontend UI-Spezifikation

### 6.1 LCARS Navigation & Banner
1. **Nav-Button im linken Frame**:
   ```html
   <button class="lcars-pill-btn pill-gemini-live" onclick="switchCategory('gemini_live')" id="btn-cat-gemini_live" style="display: none;">
     SUBRAUM COMM
   </button>
   ```
2. **Farbgebung**:
   - Akzentfarbe: `var(--c-secondary)` (Violett / African Violet `#baa4e5`) oder `var(--c-blue)` (`#8899ff`) mit Kontrast-Schriftzug `#000000`.
3. **Banner-Titel**:
   - `CATEGORY_NAMES['gemini_live'] = 'LCARS SUBRAUM-KOMMUNIKATION // GEMINI 3.8 LIVE';`

### 6.2 LCARS Command-Code Gate View
Befindet sich die Sektion `gemini_live` im Status "Gesperrt" und ist keine valide Session vorhanden, wird das Gate gerendert:
- `#geminiLiveGateView`:
  - Titel: `ZUGRIFF AUF SUBRAUM COMM VERWEIGERT`
  - Erklärung: `Die direkte Audioverbindung zur neuralen Schnittstelle Gemini 3.8 Live erfordert Autorisierung mit dem LCARS Command Code.`
  - Passwort-/Code-Feld mit "AUTORISIEREN" Button.

### 6.3 Aktiver Arbeitsbereich (`#geminiLiveActiveContent`)

Das Interface gliedert sich in drei LCARS-Bedienbereiche:

```
+-----------------------------------------------------------------------------------+
| LCARS SUBRAUM-KOMMUNIKATION // GEMINI 3.8 LIVE                    [ONLINE // PUCK]|
+-----------------------------------------------------------------------------------+
|  [ MODUS: PUSH-TO-TALK ]  [ MODUS: DAUERHAFT LIVE ]  [ 🎤 MUTE ]  [ ⏹ TRENNEN ]    |
+------------------------------------+----------------------------------------------+
| 1. AUDIO VISUALIZER & PEGELEINHEIT | 2. TERMINAL LOG & LIVE-TRANSKRIPTION        |
|                                    |                                              |
| EINGANG (MIKROFON 16 kHz):         | [01:54:10] SYSTEM: Subraum-Kanal aktiv.     |
| [ |||||||||||||||||||||||||| ] 42% | [01:54:12] COMMANDER: Statusbericht Warp-    |
|                                    |            antrieb anfordern.                |
| AUSGANG (GEMINI 3.8 LIVE 24 kHz):  | [01:54:14] COMPUTER: Warp-Kern arbeitet mit |
| [ |||||||||||||||||||||||||| ] 78% |            98,4 Prozent Effizienz. Keine     |
|                                    |            Anomalien detektiert.             |
| STATUS: TRANSMITTING AUDIO...      |                                              |
|                                    | [ Textnachricht eingeben... ] [ SENDEN ]     |
+------------------------------------+----------------------------------------------+
| [ 🎙 SPRECHEN (LEERTASTE GEDRÜCKT HALTEN / BUTTON HALTEN) ]                       |
+-----------------------------------------------------------------------------------+
```

### 6.4 Web Audio Erfassungs- & Abspiel-Engine

#### Mikrofon-Erfassung & 16kHz Downsampling
```javascript
// Web Audio Downsampler für Gemini 16kHz PCM
function initAudioRecording(stream) {
  const audioCtx = getAudioCtx();
  const sourceNode = audioCtx.createMediaStreamSource(stream);
  
  // Analyser für Input-Visualizer
  const inputAnalyser = audioCtx.createAnalyser();
  inputAnalyser.fftSize = 64;
  sourceNode.connect(inputAnalyser);

  // ScriptProcessor / AudioWorklet für PCM Chunks
  const bufferSize = 2048;
  const scriptNode = audioCtx.createScriptProcessor(bufferSize, 1, 1);
  
  scriptNode.onaudioprocess = (audioEvent) => {
    if (!isRecordingActive) return;
    const inputData = audioEvent.inputBuffer.getChannelData(0);
    
    // Resampling von Hardware SampleRate auf 16000 Hz
    const pcm16 = downsampleTo16k(inputData, audioCtx.sampleRate);
    const base64Audio = int16ToBase64(pcm16);
    
    if (geminiLiveWs && geminiLiveWs.readyState === WebSocket.OPEN) {
      geminiLiveWs.send(JSON.stringify({
        type: 'audio',
        data: base64Audio
      }));
    }
  };

  sourceNode.connect(scriptNode);
  scriptNode.connect(audioCtx.destination);
}
```

#### Audio-Wiedergabe & Jitter Buffer
```javascript
let nextPlaybackTime = 0;
let activeAudioSources = [];

function playGeminiAudioChunk(base64Audio, sampleRate = 24000) {
  const audioCtx = getAudioCtx();
  if (audioCtx.state === 'suspended') audioCtx.resume();

  const pcmBytes = base64ToInt16(base64Audio);
  const float32 = new Float32Array(pcmBytes.length);
  for (let i = 0; i < pcmBytes.length; i++) {
    float32[i] = pcmBytes[i] / 32768.0;
  }

  const audioBuffer = audioCtx.createBuffer(1, float32.length, sampleRate);
  audioBuffer.copyToChannel(float32, 0);

  const source = audioCtx.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(outputAnalyserNode);
  outputAnalyserNode.connect(audioCtx.destination);

  const now = audioCtx.currentTime;
  const startTime = Math.max(now, nextPlaybackTime);
  source.start(startTime);
  nextPlaybackTime = startTime + audioBuffer.duration;

  activeAudioSources.push(source);
  source.onended = () => {
    const idx = activeAudioSources.indexOf(source);
    if (idx !== -1) activeAudioSources.splice(idx, 1);
  };
}

function stopAllGeminiAudio() {
  activeAudioSources.forEach(s => {
    try { s.stop(); } catch(e) {}
  });
  activeAudioSources = [];
  const audioCtx = getAudioCtx();
  if (audioCtx) nextPlaybackTime = audioCtx.currentTime;
}
```

---

## 7. Risiken, Edge-Cases & Absicherungen

| Risiko / Randfall | Ursache | Vermeidungsstrategie |
|---|---|---|
| **Fehlende WebSocket-Bibliotheken** | `flask-sock` oder `websockets` nicht in `/usr/bin/python3` installiert | Automatischer Check und sichere Bereitstellung via `uv pip install --python /usr/bin/python3 --break-system-packages flask-sock websockets`. |
| **API-Key Schutz** | API-Key könnte im Frontend exponiert werden | Der API-Key verbleibt **ausschließlich** serverseitig in `config.json`. Der Browser verbindet sich nur zum lokalen Dashboard-Endpunkt `/api/gemini-live/ws`. |
| **Kein Fallback auf andere Modelle** | Gemini 3.8 Live meldet Überlastung oder Quota-Fehler | Anforderung strikt umgesetzt: Kein Modell-Fallback. Fehler wird transparent mit LCARS-Alarm im Terminal gemeldet. |
| **Mikrofon-Berechtigung im Browser** | `getUserMedia` erfordert HTTPS oder localhost | Graceful Error Handling: Prüfung auf `window.isSecureContext`. Wenn über IP im LAN aufgerufen, LCARS-Hinweis auf `https://dash.pimmel.site` einblenden. |
| **Audio-Jitter & Knackser** | Netzwerk-Latenz führt zu ungleichmäßigen Chunks | Zeitgesteuertes AudioBuffer-Scheduling (`nextPlaybackTime = Math.max(now, nextPlaybackTime) + chunkDuration`) garantiert unterbrechungsfreie Wiedergabe. |
| **Barge-In (User unterbricht KI)** | User spricht während das Modell noch Audio generiert | Sofortiges Abfangen des `interrupted: true` Events -> Aufruf von `stopAllGeminiAudio()` und Leeren der Wiedergabewarteschlange. |
| **Python Multiline String Escaping** | `DASHBOARD_HTML` in `app.py` stolpert über `\/` in Javascript-Regex | Strikte Einhaltung der Escaping-Regeln: Keine unescaped `\/` in Strings oder Regex innerhalb von `app.py`. |

---

## 8. Detaillierter Implementierungs- und Verifikationsplan

### Phase 1: Abhängigkeiten & Systemumgebung
- Installation der erforderlichen Python-Module (`flask-sock`, `websockets`, `simple-websocket`) in die Zielumgebung `/usr/bin/python3`.
- Verifikation via Import-Test: `python3 -c "import flask_sock; from websockets.sync.client import connect; print('OK')"`.

### Phase 2: Konfiguration & Zugriffsschutz
- Eintrag des neuen Blocks `"gemini_live"` in `config.json` mit Model `gemini-3.8-live` und API-Key (in `config.local.json` bzw. Umgebungsvariable).
- Hinzufügen von `"gemini_live"` zu `VALID_SECTIONS` in `permissions_service.py`.
- Ergänzung von `"gemini_live"` in `permissions.locked_sections` in `config.json`.
- Syntax- und Funktionstest von `permissions_service.py`.

### Phase 3: Backend WebSocket-Relay (`app.py`)
- Initialisierung von `Sock(app)` in `app.py`.
- Implementierung der Autorisierungsfunktion `_gemini_live_authorized()` für WebSocket- und HTTP-Anfragen.
- Erstellung der WebSocket-Route `@sock.route("/api/gemini-live/ws")`:
  - Handshake-Validierung (Command-Code Prüfung).
  - Outbound-Verbindung zu `wss://generativelanguage.googleapis.com/.../BidiGenerateContent`.
  - Senden des Setup-Frames mit Modell `models/gemini-3.8-live`.
  - Zwei Worker-Threads/Loops für simultanes Upstream- und Downstream-Relay.
  - Sauberes Schließen und Freigeben der Ressourcen bei Verbindungsabbruch.

### Phase 4: LCARS Frontend DOM & UI-Elemente
- Hinzufügen des Nav-Buttons `btn-cat-gemini_live` ("SUBRAUM COMM") in die LCARS-Sidebar.
- Einbindung von `gemini_live` in `CATEGORY_NAMES` und `applyPermissionsVisibility()`.
- Hinzufügen der Checkbox für `gemini_live` in die Berechtigungsverwaltung der CONFIG-Sektion.
- Implementierung der Section `#section-gemini_live` mit Header, Status-Badges, `#geminiLiveGateView` und `#geminiLiveActiveContent`.
- Gestaltung des Control-Panels (Push-to-Talk vs. Live-Modus, Mute, Disconnect).

### Phase 5: Web Audio API, Visualizer & Transkription
- Implementierung der Mikrofon-Pipeline (16kHz Downsampler, Int16 PCM Base64 Encoder).
- Implementierung der Audio-Ausgabe-Pipeline (24kHz PCM Decoder, Queue-Scheduler, Barge-In Handler).
- Implementierung des LCARS Dual-Visualizers (Echtzeit-Pegel für Mikrofon und Gemini-Stimme über Canvas/Segment-Bars).
- Implementierung des Transkriptions-Terminals mit Scrolling, Rollenfarben und Statusanzeige.
- Implementierung der PTT-Steuerung (Maus-Hold und Leertaste Event Listener).

### Phase 6: Verifikation & Systemtests
1. **Syntax- & Kompilierungsprüfung**:
   - `python3 -m py_compile app.py permissions_service.py` fehlerfrei.
2. **Rechteprüfung**:
   - Dashboard aufrufen: `SUBRAUM COMM` Button ist bei gesperrtem Zustand unsichtbar oder führt zum Command-Code Modal.
   - Eingabe von `0901` schaltet den Bereich frei.
   - WebSocket-Aufruf ohne Code oder mit falschem Code wird mit 4403 abgewiesen.
3. **Audio-Streaming-Prüfung**:
   - Klick auf "SUBRAUM-KANAL ÖFFNEN" stellt Verbindung zu Gemini Live her (Status wechselt auf BEREIT).
   - Spracheingabe (PTT oder Continuous): Pegel schlägt im Input-Visualizer aus.
   - Modell antwortet: Live-Transkript erscheint und Stimme wird über AudioContext sauber abgespielt.
   - Visualizer zeigt den Ausgangspegel synchron zur Sprachausgabe an.
   - Barge-In Test: Dazwischensprechen bricht die laufende Sprachausgabe sofort ab.
4. **Service-Stabilität**:
   - Neustart von `agydashboard.service` via `systemctl --user restart agydashboard.service`.
   - Prüfung von `journalctl --user -u agydashboard.service -n 50` auf sauberen Start.

---

## 9. Gemini Live Turn-Management & VAD-Implementierung (Fix 2026-09-21)

### 9.1 Problemursachen
1. **Fehlendes TurnComplete-Signal**: Google Gemini 3.8 Live (BidiGenerateContent API) erfordert nach dem Senden von Audiochunks im `realtimeInput` ein explizites Abschluss-Signal (`clientContent: { turnComplete: true }`), um den User-Turn abzuschließen und die Modell-Antwort zu generieren. Sowohl im PTT- als auch im Dauerhaft-Live-Modus fehlte dieses Signal, wodurch Google unendlich auf weitere Audiodaten wartete.
2. **WebSocket-Timeout-Abbruch**: Bei `flask-sock` / `simple-websocket` liefert `ws.receive(timeout=1.0)` bei Timeout `None` zurück, während die Verbindung noch offen ist. Ein fehlerhaftes `break` beendete die Session fälschlicherweise während des Wartens auf das Modell.

### 9.2 Backend-Erweiterungen (`app.py` - `api_gemini_live_ws`)
- **Weiterleitung von Turn-Events**: Beim Empfang von `{"type": "end_of_turn"}` oder `{"type": "turn_complete"}` vom Dashboard-Client wird sofort an Google Gemini weitergeleitet:
  ```python
  gemini_ws.send(json.dumps({
      "clientContent": {
          "turnComplete": True
      }
  }))
  ```
- **Strukturierte Protokollierung**:
  - `[GEMINI LIVE] Session setup complete (model: gemini-3.8-live, voice: Puck)`
  - `[GEMINI LIVE] User turn complete, waiting for model response`
  - `[GEMINI LIVE] Model turn complete`
  - `[GEMINI LIVE] Model output interrupted by user`
- **Robuste Timeout-Behandlung**: Bei `client_raw is None` wird geprüft, ob `ws.connected` noch aktiv ist, bevor die Schleife verlassen wird.

### 9.3 Frontend-Erweiterungen (`DASHBOARD_HTML`)
- **Push-to-Talk (`stopPtt`)**:
  - Tracking von gesendeten Audiodaten via `pttAudioSent`.
  - Beim Loslassen der Taste / des Buttons: Sofortiges Absenden von `end_of_turn`, Wechsel der LCARS-Statusanzeige auf `"BORDCOMPUTER DENKT..."` und Statusbadge auf `"DENKT..."`.
- **Dauerhaft-Live-Modus (Clientseitige VAD)**:
  - RMS-Berechnung im `onaudioprocess`-Audio-Loop (`VAD_THRESHOLD = 0.018`).
  - Wenn Sprachpegel die Schwelle überschreitet: `isSpeaking = true`, Anzeige `"COMMANDER SPRICHT..."`.
  - Wenn danach für >= 750ms Stille herrscht: Automatisches Senden von `end_of_turn`, Status `"BORDCOMPUTER DENKT..."` und `isSpeaking = false`.
  - Akustische Entkopplung: Während der Bordcomputer spricht (`isModelSpeaking`), wird die VAD pausiert, um Feedback-Schleifen von Lautsprechern zu verhindern.
- **Audio Playback & TypedArray Alignment**:
  - `base64ToInt16`: Exakte Ausrichtung via `new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2))`.
  - Statusanzeigen:
    - Bei Eintreffen von `model_audio`: Status `"BORDCOMPUTER SPRICHT..."` (Badge `"SPRICHT..."`).
    - Bei `turn_complete`: Status zurück auf `"BEREIT // ZUHÖREN"` (Badge `"BEREIT // PUCK"`).
- **Initialstatus & LCARS UI**:
  - Initialer Indikator auf `"BEREIT // ZUHÖREN"`.

### 9.4 Qualität & Verifikation
- **Skript-Syntaxprüfung**: `node --check` auf allen aus dem gerenderten Dashboard-HTML extrahierten Skripten (8.452 Zeilen JavaScript fehlerfrei, 0 Syntaxfehler).
- **End-to-End WebSocket Test**: Python-Integrationstest verifiziert:
  1. Setup-Frame Empfang (`gemini-3.8-live`, `Puck`)
  2. Audio-Übertragung (16 kHz PCM)
  3. `end_of_turn` Signal
  4. 25-27 Chunks 24 kHz PCM Audio erfolgreich empfangen (364+ kB)
  5. `turn_complete` Signal erfolgreich empfangen
- **Systemd Service**: `systemctl --user restart agydashboard.service` fehlerfrei aktiv.

---

# Architektur- & Implementierungsplan: Remote Node "PIMMEL" (Raspberry Pi Telemetrie & 9Router Hub)

## 1. Zielsetzung & Kontext

### 1.1 Ausgangssituation & Topologie
Das System-Dashboard `agydashboard` läuft aktuell auf dem lokalen Host (`BiggerPimmel`, User `cb`) unter Systemd (`agydashboard.service`) und visualisiert lokale ODN-Sensordaten, KI-Agenten und 9Router-Statistiken aus der lokalen Datenbank `~/.9router/db/data.sqlite`.

Im Heimnetzwerk / Tailscale-Mesh existiert ein dedizierter Remote Node:
- **Hostname**: `PiMMEL` (Raspberry Pi 4 / ARM64, Debian 12)
- **Netzwerk**: IP `100.88.215.98` (Tailscale) bzw. `192.168.178.84` (lokales LAN)
- **SSH-Zugang**: Bereits schlüsselloser Zugang via `ssh pimmel` (User `yash`, SSH-Key `~/.ssh/id_ed25519`) eingerichtet und verifiziert (Ping/Latenz < 25ms, SSH-Befehlsausführung < 0.9s).
- **Aktive Kerndienste auf PiMMEL**:
  - `9router`: Node.js Proxy/Router, gemanagt via PM2 (`pm2 jlist`, Instanzname `9router`, Port 20128, PID, Memory ~160 MB).
  - `hermes-gateway`: Systemd User-Service (`systemctl --user status hermes-gateway`, Telegram/Matrix Bot-Gateway, User `yash`).
  - `data.sqlite`: Vollständige Routing- und Verbrauchsdatenbank unter `/home/yash/.9router/db/data.sqlite` (>2.100 Requests, `usageDaily`, `usageHistory`, `providerConnections`).
  - `9router-out.log`: Fortlaufende Ausgabelogs unter `/home/yash/.pm2/logs/9router-out.log` mit detaillierten Tokens-, Cache- und Latenzinformationen.

### 1.2 Zieldefinition
Implementierung einer eigenständigen, vollwertigen LCARS-Kategorie und Dashboard-Sektion **"PIMMEL"** im `agydashboard`.

Der Funktionsumfang gliedert sich in vier Kernbereiche:
1. **Pimmel Host-Vitals**: Echtzeit-Monitoring von CPU-Last, Load Average (1m, 5m, 15m), RAM, Root-Disk, SoC-Temperatur, Uptime sowie dem Dienststatus von `hermes-gateway` (systemd) und `9router` (PM2).
2. **9Router Telemetrie & Verbrauchs-Kennzahlen**: Automatisierte Synchronisation der Remote-SQLite-Datenbank in einen lokalen Cache, Berechnung von Token-Volumina (Prompt, Completion, Cached), Prompt-Caching-Ersparnis (Quote & $), Provider- und Modell-Verteilungen sowie tabellarische Übersicht der letzten Transaktionen aus `usageHistory`.
3. **PM2 Live-Logstream**: LCARS-Terminal zur Einsicht der letzten Zeilen von `9router-out.log` mit farblichem Highlighting für Transaktionen, Tokens, Cache-Hits und Fehler.
4. **Historie der System- & 9Router-Nutzung**:
   - Rolling 24h-Sensorhistorie (CPU, RAM, Temperatur) von PiMMEL über Zeit (10m, 30m, 1h, 12h, 24h).
   - Historischer Token- und Request-Verlauf über Tage hinweg (aggregiert aus `usageDaily`).

---

## 2. Analyse der bestehenden Dashboard-Architektur (`app.py`)

### 2.1 Bestehendes `get_9router_stats()`
In `app.py` (Zeilen 1522–1795) existiert die Funktion `get_9router_stats()`. Diese liest derzeit hardcodiert `~/.9router/db/data.sqlite` des lokalen Hosts aus:
- **Queries**: Aggregiert `usageDaily` (JSON-Payloads pro Tag für Requests, Tokens, Provider, Modelle), `usageHistory` (letzte 15 Zeilen), `providerConnections` (aktive Verbindungen) und berechnet über SQL-Heuristiken die Prompt-Caching-Ersparnis.
- **Limitation**: Fest auf den lokalen Pfad gebunden, keine Mehrmandantenfähigkeit (Local vs. Remote Node).
- **Lösungsansatz**: Modularisierung / Refactoring in eine wiederverwendbare Funktion bzw. einen Service `parse_9router_sqlite(db_path)`, der sowohl von der lokalen KI-Info-Sektion als auch vom neuen `PimmelService` für `/home/cb/Projects/agydashboard/data_cache/pimmel_9router.sqlite` verwendet werden kann.

### 2.2 Navigation & LCARS-Pillar
- In `app.py` (Zeilen 4868–4905) steuert die Navigationssäule (`.nav-pillar`) das Wechseln der Kategorien via `switchCategory(catId)`.
- Die Kategorien sind in `CATEGORY_NAMES` (Zeilen 8329–8341) definiert.
- Beim Kategoriewechsel blendet `switchCategory` alle `.lcars-section` aus, aktiviert die Ziel-Sektion (`#section-<catId>`) und führt ggf. Resize- und Re-Render-Routinen für Canvas-Charts aus (`historyChart.resize()`, `initHistoryChart()`).
- Das Berechtigungssystem (`permissions_service.py`) definiert `VALID_SECTIONS` und sperrt nicht freigegebene Bereiche mit dem Command Code `0901`.

### 2.3 Chart-Engine & UI-Konventionen
- Das Dashboard nutzt eine hybride Chart-Engine: Bevorzugt `Chart.js` (über `ensureChart()`), gekoppelt mit nativen LCARS-Canvas-Renderern als Ausfallsicherung.
- Bereits etablierte Chart-Typen:
  - Line-Charts für Sensor-Historien (CPU, RAM, Temp).
  - Doughnut-Charts für Modell-Tokenverteilung (`nineRouterModelChart`, `hermesChart`).
  - Bar/Line-Kombinations-Charts für Transaktionen und Tokenflüsse (`nineRouterTimelineChart`).
- Visuelle LCARS-Klassen:
  - `.lcars-card`, `.card-violet`, `.card-blue`, `.card-almond`, `.card-red`.
  - `.readout-grid`, `.card-metric`, `.lcars-bar-track`, `.badge-status`.

---

## 3. System- & Komponentenarchitektur

### 3.1 Architekturübersicht

```mermaid
flowchart TD
    subgraph RemoteNode ["Remote Node: PiMMEL (100.88.215.98)"]
        R_SYS["/proc, /sys/thermal, os.getloadavg()"]
        R_SYSTEMD["systemctl --user is-active hermes-gateway"]
        R_PM2["PM2 Daemon: 9router status & logs"]
        R_SQLITE["~/.9router/db/data.sqlite (WAL Mode)"]
    end

    subgraph ServiceLayer ["agydashboard: Backend (BiggerPimmel)"]
        PS["pimmel_service.py (PimmelService Singleton)"]
        WORKER_FAST["Worker Thread: Telemetrie & Logs (5s - 10s)"]
        WORKER_SYNC["Worker Thread: SQLite Rsync (30s / on-demand)"]
        RING_BUF["In-Memory Rolling History Buffer (24h)"]
        CACHE_DB["data_cache/pimmel_9router.sqlite"]
        SQL_ENGINE["9Router Analytics & Aggregation Engine"]
    end

    subgraph FlaskEndpoints ["Flask App (app.py)"]
        EP_STATUS["/api/pimmel/status"]
        EP_HIST["/api/pimmel/history?range=..."]
        EP_9R["/api/pimmel/9router"]
        EP_LOGS["/api/pimmel/logs"]
        EP_SYNC["/api/pimmel/sync"]
    end

    subgraph LCARS_UI ["LCARS Frontend (DASHBOARD_HTML)"]
        PILL_NAV["Nav-Pill: PIMMEL"]
        SEC_HOST["Panel 1: Host Vitals (CPU, RAM, Temp, Services)"]
        SEC_CHARTS["Panel 2: Sensor-History & 9Router Charts"]
        SEC_TABLE["Panel 3: 9Router Transmissions-Tabelle"]
        SEC_LOGS["Panel 4: PM2 9Router Live Console Logs"]
    end

    %% Datenflüsse
    WORKER_FAST -- "SSH BatchMode (Python One-Shot via Stdin)" --> R_SYS
    WORKER_FAST -- "SSH BatchMode" --> R_SYSTEMD
    WORKER_FAST -- "SSH BatchMode" --> R_PM2
    WORKER_FAST --> RING_BUF

    WORKER_SYNC -- "rsync -az (Delta Transfer)" --> R_SQLITE
    WORKER_SYNC --> CACHE_DB
    CACHE_DB --> SQL_ENGINE

    PS --> WORKER_FAST
    PS --> WORKER_SYNC
    PS --> RING_BUF
    PS --> SQL_ENGINE

    FlaskEndpoints --> PS
    LCARS_UI -- "fetch('/api/pimmel/...')" --> FlaskEndpoints
```

### 3.2 Modulare Kapselung: `pimmel_service.py`
Zur Vermeidung einer weiteren Aufblähung von `app.py` wird die gesamte Node-Logik analog zu `ha_service.py` und `cycle_service.py` in einer separaten Datei `pimmel_service.py` gekapselt:
- **`PimmelService`**:
  - Verwaltet Konfiguration (Host, IP, Intervalle, Pfade).
  - Hält Thread-Locks (`threading.RLock`) für thread-sicheren Zustand.
  - Initialisiert und steuert die Background-Worker.
  - Stellt saubere Abfragemethoden für Controller/Routen bereit:
    - `get_status()`
    - `get_history(range_seconds)`
    - `get_9router_data()`
    - `get_logs(lines)`
    - `trigger_sync()`

---

## 4. Datenbeschaffung & Caching-Strategie (SSH Polling / Sync)

### 4.1 Schnelles Host-Telemetrie-Polling (5s–10s Intervall)
Das Polling der Host-Metriken muss extrem schlank, nicht-blockierend und resistent gegen Verbindungsabbrüche sein.
Statt mehrfach separate SSH-Befehle abzusetzen, wird ein einzelnes, hochoptimiertes Python-Script über `ssh -o ConnectTimeout=3 -o BatchMode=yes pimmel "python3 -"` via Standardeingabe gestreamt.

#### Remote One-Shot Script Payload:
```python
import datetime, json, os, subprocess, sys

# 1. CPU & Load
load1, load5, load15 = os.getloadavg()
cores = os.cpu_count() or 1
cpu_pct = round(min(100.0, (load1 / cores) * 100), 1)

# 2. Uptime
uptime_sec = 0.0
with open("/proc/uptime") as f:
  uptime_sec = float(f.read().split()[0])

# 3. Memory
mem_total, mem_avail = 0, 0
with open("/proc/meminfo") as f:
  for line in f:
    if line.startswith("MemTotal:"):
      mem_total = int(line.split()[1]) * 1024
    elif line.startswith("MemAvailable:"):
      mem_avail = int(line.split()[1]) * 1024
mem_used = max(0, mem_total - mem_avail)
mem_pct = round((mem_used / mem_total) * 100, 1) if mem_total else 0.0

# 4. Root Disk
st = os.statvfs("/")
disk_total = st.f_blocks * st.f_frsize
disk_free = st.f_bavail * st.f_frsize
disk_used = disk_total - disk_free
disk_pct = round((disk_used / disk_total) * 100, 1) if disk_total else 0.0

# 5. SoC Temperatur
temp_c = 0.0
try:
  with open("/sys/class/thermal/thermal_zone0/temp") as f:
    temp_c = round(float(f.read().strip()) / 1000.0, 1)
except Exception:
  pass

# 6. Hermes Gateway (User Systemd)
try:
  hermes_active = subprocess.check_output(
      ["systemctl", "--user", "is-active", "hermes-gateway"], text=True
  ).strip()
except Exception:
  hermes_active = "inactive"

# 7. PM2 9Router Prozess
pm2_9router = {"status": "offline"}
try:
  raw = subprocess.check_output(["pm2", "jlist"], text=True)
  for p in json.loads(raw):
    if p.get("name") == "9router":
      monit = p.get("monit", {})
      env = p.get("pm2_env", {})
      pm2_9router = {
          "status": env.get("status", "unknown"),
          "pid": p.get("pid"),
          "pm_id": p.get("pm_id"),
          "uptime_ms": env.get("pm_uptime"),
          "restarts": env.get("restart_time", 0),
          "cpu": monit.get("cpu", 0),
          "memory": monit.get("memory", 0),
          "version": env.get("version", ""),
      }
      break
except Exception as e:
  pm2_9router = {"status": "error", "error": str(e)}

# 8. PM2 9Router Logs (letzte 40 Zeilen)
logs = ""
try:
  logs = subprocess.check_output(
      ["tail", "-n", "40", "/home/yash/.pm2/logs/9router-out.log"], text=True
  )
except Exception as e:
  logs = f"[WARN] Fehler beim Lesen der Logs: {e}"

print("__JSON_START__")
print(
    json.dumps({
        "hostname": "PiMMEL",
        "ip": "100.88.215.98",
        "timestamp": datetime.datetime.now().isoformat(),
        "load": {
            "1m": round(load1, 2),
            "5m": round(load5, 2),
            "15m": round(load15, 2),
            "cores": cores,
        },
        "cpu": {"percent": cpu_pct, "cores": cores},
        "ram": {
            "total": mem_total,
            "used": mem_used,
            "free": mem_avail,
            "percent": mem_pct,
        },
        "disk": {
            "total": disk_total,
            "used": disk_used,
            "free": disk_free,
            "percent": disk_pct,
        },
        "temp": temp_c,
        "uptime_sec": int(uptime_sec),
        "hermes": {"status": hermes_active},
        "pm2_9router": pm2_9router,
        "logs": logs,
    })
)
```
*Performance-Ergebnis*: Der gesamte Aufruf benötigt im Testbetrieb via Tailscale lediglich **~0.90 Sekunden** und liefert ein vollständiges Telemetrie-Paket.

### 4.2 SQLite Datenbank-Synchronisation (30s–60s Intervall)
Die SQLite-Datei `~/.9router/db/data.sqlite` auf PiMMEL ist rund 4.7 MB groß. Um Remote-Locks oder Lese-Konflikte mit dem laufenden 9Router-Prozess zu vermeiden, wird sie in ein lokales Cache-Verzeichnis gespiegelt:
- **Lokaler Cache-Pfad**: `/home/cb/Projects/agydashboard/data_cache/pimmel_9router.sqlite`
- **Sync-Mechanismus**: `rsync -az --timeout=10 pimmel:/home/yash/.9router/db/data.sqlite /home/cb/Projects/agydashboard/data_cache/pimmel_9router.sqlite`
- **Vorteile**:
  1. `rsync` überträgt nur veränderte Blöcke (Delta-Transfer), Laufzeit < 0.5s bei bestehender Verbindung.
  2. Lokale SQLite-Queries blockieren weder den Remote-Knoten noch das Netzwerk.
  3. Vollständige Offline-Fähigkeit: Selbst wenn PiMMEL kurzzeitig neu startet, stehen die Verbrauchsdaten und Modellstatistiken nahtlos zur Verfügung.
  4. Die SQLite-Verbindung im Python-Backend öffnet die Datei immer mit `uri=True, mode=ro` (`file:...data.sqlite?mode=ro`).

### 4.3 In-Memory 24h Sensor-Ringbuffer
- Ein dedizierter `deque(maxlen=8760)` speichert alle 10 Sekunden einen Snapshot von PiMMEL (`ts, cpu, ram, temp, disk, load1`).
- Bereitstellung für Zeiträume `10m`, `30m`, `1h`, `12h`, `24h` analog zum bestehenden lokalen `history_store`.

---

## 5. Backend REST-API Schnittstellenspezifikation (`app.py`)

Die folgenden Endpunkte werden in `app.py` registriert und greifen auf die `pimmel_service`-Instanz zu:

| Endpunkt | Methode | Parameter | Beschreibung |
|---|---|---|---|
| `/api/pimmel/status` | `GET` | Keine | Gibt aktuellen Status des Nodes, Host-Vitals, Service-Status und 9Router-Summen zurück. |
| `/api/pimmel/history` | `GET` | `range` (`10m`, `30m`, `1h`, `12h`, `24h`) | Liefert Zeitreihen-Array der Sensordaten von PiMMEL für den LCARS-Sensorchart. |
| `/api/pimmel/9router` | `GET` | Keine | Liefert detaillierte 9Router-Daten: `totals`, `by_provider`, `by_model`, `daily_timeline`, `recent_history`, `connections`. |
| `/api/pimmel/logs` | `GET` | `lines` (optional, default 50, max 200) | Gibt die letzten PM2-Ausgabelogs von 9Router mit Zeilenanzahl und Metadaten zurück. |
| `/api/pimmel/sync` | `POST` | Keine | Triggert eine sofortige asynchrone Re-Synchronisation der SQLite-Datenbank und Telemetrie. |

### 5.1 Beispielhafte Antwortstruktur `/api/pimmel/status`:
```json
{
  "node": {
    "hostname": "PiMMEL",
    "ip": "100.88.215.98",
    "online": true,
    "last_sync": "2026-09-21T18:34:45+02:00",
    "last_error": null
  },
  "host_metrics": {
    "cpu": { "percent": 3.8, "cores": 4 },
    "load": { "1m": 0.15, "5m": 0.10, "15m": 0.05, "cores": 4 },
    "ram": { "total": 8243306496, "used": 1788481536, "free": 6454824960, "percent": 21.7, "used_gb": "1.66 GB", "total_gb": "7.68 GB" },
    "disk": { "total": 61840048128, "used": 41226698752, "free": 20613349376, "percent": 66.7, "used_gb": "38.39 GB", "total_gb": "57.59 GB" },
    "temp": { "value": 41.9, "display": "41.9 °C" },
    "uptime": { "seconds": 11520, "display": "3 Std 12 Min", "boot_time": "2026-09-21 15:22:00" },
    "services": {
      "hermes_gateway": { "status": "active", "display": "ONLINE // RUNNING", "type": "systemd-user" },
      "pm2_9router": { "status": "online", "pid": 6748, "pm_id": 0, "restarts": 2, "cpu": 0.9, "memory_mb": 158.1, "version": "0.5.75" }
    }
  },
  "nine_router_summary": {
    "total_requests": 2154,
    "total_tokens_formatted": "34.8M",
    "prompt_tokens_formatted": "32.1M",
    "completion_tokens_formatted": "2.7M",
    "cached_tokens_formatted": "28.4M",
    "cache_hit_rate_formatted": "88.5%",
    "cost_formatted": "$6.4215",
    "saved_cost_formatted": "$14.8200",
    "saved_cost_pct_formatted": "69.7%"
  }
}
```

---

## 6. LCARS Frontend UI-Spezifikation (`DASHBOARD_HTML`)

### 6.1 Navigation & Pillar-Integration
- **Nav-Pill-Button**:
  ```html
  <button class="lcars-pill-btn pill-pimmel" onclick="switchCategory('pimmel')" id="btn-cat-pimmel">
    PIMMEL
  </button>
  ```
- **CSS-Klasse**: `.pill-pimmel` erhält einen charakteristischen LCARS-Farbton (z. B. `--c-secondary` / `#baa4e5` oder `--c-blue` / `#8899ff`) mit passendem Hover- und Aktivzustand.
- **Titel-Banner**: `CATEGORY_NAMES['pimmel'] = 'REMOTE NODE // PIMMEL ODN-HUB (100.88.215.98)'`.

### 6.2 Struktur der Sektion `#section-pimmel`
Die Sektion wird in logische LCARS-Bereiche unterteilt:

```
+-----------------------------------------------------------------------------------------+
| [HEADER] REMOTE NODE // PIMMEL (100.88.215.98)   [STATUS: ONLINE] [⟳ REFRESH] [SYNC DB]|
+-----------------------------------------------------------------------------------------+
| [READOUT GRID 1: PIMMEL HOST VITALS]                                                    |
|  [ CPU 3.8% ] [ RAM 21.7% ] [ TEMP 41.9°C ] [ DISK 66.7% ] [ UPTIME ] [ HERMES ] [ 9R ]|
+-----------------------------------------------------------------------------------------+
| [READOUT GRID 2: 9ROUTER TELEMETRIE TILES]                                              |
|  [ REQUESTS ] [ TOKEN VOLUMEN ] [ CACHE ERSPARNIS ] [ GESAMTKOSTEN ] [ PROVIDER AKTIV ] |
+-----------------------------------------------------------------------------------------+
| [GRAFISCHE ANALYSE: DUAL CHARTS]                                                        |
|  +---------------------------------------+  +-----------------------------------------+ |
|  | PIMMEL 24H SENSOR HISTORIE            |  | 9ROUTER MODELL-TOKEN VERTEILUNG         | |
|  | [10m][30m][1h][12h][24h] [CPU][RAM][T]|  | (Doughnut Chart: GPT, Claude, Codex)    | |
|  | (Chart.js Line Canvas)                |  |                                         | |
|  +---------------------------------------+  +-----------------------------------------+ |
+-----------------------------------------------------------------------------------------+
| [9ROUTER TRANSMISSIONS-HISTORIE & TOKEN-FLOW]                                           |
|  (Stacked Bar/Line Chart: Täglicher Verlauf Requests, Prompt/Cached/Compl, Kosten)     |
+-----------------------------------------------------------------------------------------+
| [9ROUTER TRANSMISSIONS-LOG // LETZTE REQUESTS TABELLE]                                  |
|  ID | ZEITPUNKT | PROVIDER | MODELL | PROMPT | COMPL | CACHED | TOTAL | KOSTEN | STATUS|
+-----------------------------------------------------------------------------------------+
| [PM2 9ROUTER LIVE CONSOLE LOGS (~/.pm2/logs/9router-out.log)]                           |
|  [AUTO-SCROLL ON/OFF] [ZEILEN: 30/60/100]                                               |
|  > [16:49:15] 🔴 ▶ POST cx/gpt-5.6-luna → codex/gpt-5.6-luna ...                        |
|  > [16:49:20] 🔴 📊 DONE 4605ms · TTFT 1579ms · IN 16261 (CACHE ↻15872) · OUT 159       |
+-----------------------------------------------------------------------------------------+
```

### 6.3 Details zu den UI-Komponenten

#### 1. Host-Vitals Cards
- **CPU & Load Average**: Zeigt prozentuale Last, Kerne und Tooltip mit Load-Average (1m/5m/15m).
- **RAM**: Füllstandsbalken mit genauer Angabe `1.66 GB / 7.68 GB`.
- **SoC Temperatur**: Visuelle LCARS-Farbcodierung (<50°C Normal/Grün, 50-70°C Gelb/Orange, >70°C Alarm/Rot).
- **Disk Root**: Füllstandsanzeige der SD-Karte / NVMe-Storage.
- **Hermes Gateway Badge**: Statusbadge grün bei `ACTIVE (RUNNING)` oder rot bei `INACTIVE / FAILED`.
- **PM2 9Router Badge**: PM2-Instanzstatus, Memory-Verbrauch, Restarts und Version.

#### 2. 9Router Telemetrie
- Aggregiert aus der synchronisierten SQLite-Datenbank:
  - Total Requests
  - Total Tokens (unterteilt in Prompt, Completion und Cached)
  - Cache-Hit-Quote (z. B. `88.5%`) und berechnete Dollar-Ersparnis
  - Gesamt-Routingkosten im Vergleich zu den ungecachten Basiskosten
  - Aktive Provider-Verbindungen

#### 3. Interaktiver PM2 Log Viewer
- Styled im LCARS Terminal-Look (Monospace-Schriftart `Share Tech Mono`, dunkler Hintergrund, LCARS-Orange/Cyan Akzentrahmen).
- Parser hebt spezifische 9Router-Logmuster hervor:
  - `POST ...` -> Akzentuiertes Cyan / Gold.
  - `DONE ...ms` -> Grüner Status.
  - `CACHE ↻...` -> Leuchtendes Blau.
  - Fehler / Timeouts -> Rote Warnmarkierung.
- Buttons für `Auto-Scroll Umschaltung` und `Manuelles Neuladen`.

---

## 7. Client-seitige JavaScript-Engine

### 7.1 Lifecycle & Polling
- In `fetchLiveStats()`: Wenn `currentCategory === 'pimmel'`, wird automatisch `fetchPimmelStats(false)` aufgerufen.
- Beim Tab-Wechsel in `switchCategory(catId)`:
  ```javascript
  if (catId === 'pimmel') {
    fetchPimmelStats(true);
    setTimeout(() => {
      if (pimmelHistoryChart) pimmelHistoryChart.resize();
      if (pimmelModelChart) pimmelModelChart.resize();
      if (pimmelTimelineChart) pimmelTimelineChart.resize();
      initPimmelCharts();
    }, 60);
  }
  ```

### 7.2 Chart-Initialisierung & Fallback
- `initPimmelCharts()`:
  - `initPimmelHistoryChart(range)`: Dual Chart.js + nativer LCARS-Canvas für Sensordaten.
  - `initPimmelModelChart(data)`: Doughnut-Chart der Modell-Verteilung auf PiMMEL.
  - `initPimmelTimelineChart(data)`: Stacked Bar Chart mit sekundärer Y-Achse für Kosten.

---

## 8. Konfiguration & Berechtigungs-Management

### 8.1 Erweiterung in `config.json`
```json
{
  "pimmel_node": {
    "enabled": true,
    "ssh_host": "pimmel",
    "ip": "100.88.215.98",
    "poll_interval_seconds": 10,
    "sync_interval_seconds": 30,
    "remote_db_path": "/home/yash/.9router/db/data.sqlite",
    "remote_log_path": "/home/yash/.pm2/logs/9router-out.log",
    "cache_db_path": "data_cache/pimmel_9router.sqlite"
  }
}
```

### 8.2 Anpassung in `permissions_service.py`
- Hinzufügen von `"pimmel"` zu `VALID_SECTIONS`.
- Das Dashboard kann die Sektion bei Bedarf analog zu `pulsecast` oder `gemini_live` über den Command Code `0901` absichern.

---

## 9. Risiken, Edge-Cases & Sicherheitsmaßnahmen

| Risiko / Randfall | Ursache | Vermeidungsstrategie |
|---|---|---|
| **SSH-Timeout / Nicht-Erreichbarkeit** | PiMMEL ist im Standby, Tailscale unterbrochen oder Node rebootet | SSH-Befehle werden mit `-o ConnectTimeout=3 -o BatchMode=yes` und striktem Timeout ausgeführt. Im Fehlerfall meldet der Service `status: "offline"`, ohne den Flask-Webserver oder das Dashboard zu blockieren. Die UI zeigt ein amber/rotes LCARS-Verbindungswarnbanner. |
| **SQLite Lock-Konflikte (Concurrency)** | 9Router auf PiMMEL schreibt während rsync synchronisiert | Remote-Datenbank läuft im WAL-Modus (`data.sqlite-wal`). Die lokale Kopie wird read-only geöffnet (`mode=ro`). Bei Lese-Exceptions greift ein Retry mit kurzem Backoff. |
| **CPU-Last auf dem Raspberry Pi** | Zu häufige SSH-Sessions erzeugen CPU-Spitzen auf PiMMEL | Konsolidierung aller Systemdaten, PM2-Status und Logs in einen einzigen Python-Aufruf alle 10 Sekunden; rsync nur alle 30–60 Sekunden. |
| **Gleichzeitiges Polling / Race Conditions** | Mehrere Browser-Clients rufen gleichzeitig `/api/pimmel/status` auf | `PimmelService` pollt im Hintergrund unabhängig von HTTP-Requests. HTTP-Endpunkte lesen ausschließlich aus dem In-Memory-Cache des Singletons (`cache_ttl = 3.0s`). |
| **Log-Datei Überlauf** | `9router-out.log` wächst über Zeit stark an | Es wird per `tail -n 60` immer nur das Dateiende gestreamt, keine Übertragung des gesamten Logfiles. |

---

## 10. Detaillierter Implementierungs- und Verifikationsplan

### Phase 1: Service-Modul `pimmel_service.py`
1. Erstellung des Service-Moduls `pimmel_service.py` mit:
   - Hintergrund-Worker `_poller_worker` (SSH-Metriken & Logs).
   - Hintergrund-Worker `_sync_worker` (rsync der SQLite-Datenbank in `data_cache/`).
   - SQLite-Analyse-Funktion für `usageDaily`, `usageHistory`, `providerConnections` und Ersparnisberechnung.
   - Ringbuffer für 24h-Sensorhistorie.
2. Erstellung eines Test- und Verifikationsskripts `test_pimmel_service.py`:
   - Prüfung von SSH-Zugang, Datenabruf, SQLite-Caching und JSON-Serialisierung.

### Phase 2: Konfiguration & Zugriffsschutz
1. Integration von `"pimmel"` in `permissions_service.py` (`VALID_SECTIONS`).
2. Konfigurationseintrag `"pimmel_node"` in `config.json`.
3. Validierung via `python3 -c "import permissions_service; print(permissions_service.VALID_SECTIONS)"`.

### Phase 3: Flask Backend API-Routen in `app.py`
1. Import von `pimmel_service` in `app.py`.
2. Registrierung der Routen:
   - `/api/pimmel/status`
   - `/api/pimmel/history`
   - `/api/pimmel/9router`
   - `/api/pimmel/logs`
   - `/api/pimmel/sync`
3. Abdeckung im HTTP-Discovery / Scanner falls gewünscht.

### Phase 4: LCARS UI & DOM in `DASHBOARD_HTML`
1. Hinzufügen des Nav-Pills `btn-cat-pimmel` in `.nav-pillar`.
2. Eintrag in `CATEGORY_NAMES`.
3. Implementierung des Sektions-Containers `<section class="lcars-section" id="section-pimmel">`:
   - Header Bar & Status-Indikatoren.
   - Readout-Grid für Pimmel-Host-Vitals.
   - Readout-Grid für 9Router-Telemetrie.
   - Canvas-Container für Sensor-History und Modell-Doughnut.
   - 9Router-Transmissions-Historie Chart.
   - Letzte Transaktionen Tabelle aus `usageHistory`.
   - Live PM2 9Router Console Log Terminal.

### Phase 5: Client-seitiges JavaScript & Chart-Rendering
1. Implementierung von `fetchPimmelStats(playSound)`.
2. Integration in `switchCategory('pimmel')` und automatische Chart-Größenanpassung.
3. Implementierung der Chart-Initialisierungs- und Update-Funktionen (`initPimmelHistoryChart`, `initPimmelModelChart`, `initPimmelTimelineChart`).
4. Implementierung des Terminal-Log-Renderers mit LCARS-Farbformatierung.

### Phase 6: Verifikation & Systemtests
1. **Statische Code- & Syntaxprüfung**:
   - `python3 -m py_compile app.py pimmel_service.py permissions_service.py` (0 Syntaxfehler).
   - Extraktion und Prüfung aller JavaScript-Blöcke via `node --check` (0 Syntaxfehler).
2. **API-Endpunkttests**:
   - Direkter Aufruf aller 5 Endpunkte (`/api/pimmel/status`, `/history`, `/9router`, `/logs`, `/sync`) mit Validierung der Rückgabewerte.
3. **UI-Interaktionstests**:
   - Navigation auf die PIMMEL-Sektion: Verifikation der Sichtbarkeit, LCARS-Beep-Sounds und korrekter Tabellen- und Chart-Renderings.
   - Umschaltung der Sensor-Zeitbereiche (10m, 30m, 1h, 12h, 24h).
   - Prüfung des Log-Streamers und der Auto-Scroll-Funktion.
4. **Stresstest & Resilienz**:
   - Simulation eines temporären Verbindungsverlusts (z. B. erzwungener SSH-Timeout).
   - Verifikation, dass das Dashboard stabil bleibt und den Offline-Status anzeigt.
5. **Systemd-Service Verifikation**:
   - Neustart des Dienstes: `systemctl --user restart agydashboard.service`.
   - Prüfung von `systemctl --user status agydashboard.service` und `journalctl --user -u agydashboard.service -n 50`.

---

# Architektur- und Implementierungsplan: ESPN Fantasy KI-Manager

## 1. Zielsetzung & Funktionsumfang

Das LCARS Dashboard (`agydashboard`) verfügt über eine aktive Live-Anbindung an die private ESPN Fantasy Football API v3 für die Liga **"Incomplete Pass"** (16 Teams, Liga-ID `378649793`, Team-ID `17`, Saison `2026`). Das bestehende System liest Matchup-, Roster-, Punkte- und Tabellendaten aus und steuert bei Score-Erhöhungen ein Home-Assistant-Lichtsignal (`light.esstisch`).

Dieses Feature transformiert die ESPN Fantasy Integration von einem rein passiven Display zu einem intelligenten, proaktiven **KI-Manager** mit einem **3-Stufen-Modus-Schalter**:

```
[ MANUAL ] ──▶ [ SEMI-AI ] ──▶ [ FULL-AI ]
```

### Die drei Betriebsmodi im Detail:
1. **`manual` (Passiv / Monitor)**:
   - Reines Monitoring von Live-Scores, Kader und Tabellen.
   - Keine automatischen Analysen oder Transaktionen.
   - Kein Schreibzugriff auf die ESPN API.
2. **`semi` (Co-Pilot / Vorschlagsmodus)**:
   - Die KI analysiert periodisch vor Spieltagen (Dienstag nach Waiver, Donnerstag vor TNF, Sonntag vor Kickoffs, Montag vor MNF) das eigene Team, den Gegner, Verletzungsberichte und freie Spieler.
   - Bei erkannten Handlungsbedarfen (z.B. Starter verletzt, deutliche Matchup-Vorteile auf der Bank) generiert die KI konkrete Start/Sit- oder Waiver-Vorschläge inklusive Begründung und Konfidenz-Score.
   - Der Vorschlag wird per Telegram gemeldet und im LCARS Dashboard als auffälliges Interaktions-Banner dargestellt.
   - Keine automatische Ausführung: Die Transaktion wird erst nach expliziter Bestätigung durch den Benutzer (`[ FREIGEBEN & AUSFÜHREN ]`) an ESPN übermittelt.
3. **`full` (Autonomer Agent)**:
   - Die KI überwacht selbstständig die Kickoff-Zeitfenster aller aktiven Spieler (T-60 Min, T-30 Min, T-10 Min).
   - Bei verletzten Spielern (`OUT`, `INJURY_RESERVE`, `DOUBTFUL`) oder taktischen Vorteilen tauscht die KI autonom den Starter gegen den optimalen gesunden Bankspieler aus.
   - Jeder Move durchläuft vor der Übermittlung an `lm-api-writes` eine strikte Safeguard-Validierungsmatrix (Lock-Status, Slot-Eligibility, IR-Regeln).
   - Nach erfolgreicher Ausführung sendet der Manager eine Erfolgs- und Statusmeldung mit Begründung via Telegram und protokolliert die Aktion im LCARS Audit-Trail.

---

## 2. Systemarchitektur & Interaktionsfluss

### 2.1 Gesamtsystem-Übersicht

```mermaid
flowchart TD
    subgraph LCARS ["LCARS Dashboard (Frontend)"]
        UI_Switch["3-Stufen Modus-Schalter\n[MANUAL | SEMI | FULL]"]
        UI_Banner["Vorschlags-Banner (Semi-Modus)\n[Freigeben / Verwerfen]"]
        UI_Card["ESPN Fantasy Card\n(Matchup, Roster, KI-Status)"]
        UI_Audit["KI-Audit & History Drawer"]
    end

    subgraph Backend ["agydashboard Backend (Flask & Daemon)"]
        API_Mode["/api/espn/mode\n(GET / POST)"]
        API_Proposals["/api/espn/proposals\n(GET / POST apply/dismiss)"]
        API_Analyze["/api/espn/analyze\n(POST On-Demand)"]
        ConfigMgr["Config Manager\n(config.json)"]
        Scheduler["Hintergrund-Scheduler\n(EspnAiScheduler in espn_service.py)"]
        DecisionEngine["KI-Entscheidungs-Engine\n(Gemini 3.8 Flash via 9Router)"]
        Safeguards["Safeguard- & Validierungsmatrix"]
        WriteClient["ESPN Transaction Client\n(lm-api-writes)"]
    end

    subgraph AI ["9Router & LLM Hub (Port 20128)"]
        NineRouter["9Router Proxy (http://127.0.0.1:20128)"]
        GeminiFlash["ag/gemini-3.8-flash-high"]
    end

    subgraph External ["Externe APIs & Kommunikationskanäle"]
        ESPN_R["ESPN Read API\nlm-api-reads.fantasy.espn.com"]
        ESPN_W["ESPN Write API\nlm-api-writes.fantasy.espn.com"]
        TelegramGate["Telegram / Hermes-Gateway (PiMMEL)"]
        HA["Home Assistant\n(light.esstisch Flash)"]
    end

    UI_Switch -->|POST /api/espn/mode| API_Mode
    API_Mode --> ConfigMgr
    Scheduler -->|Kickoff-Timer / Intervall| DecisionEngine
    DecisionEngine -->|Lese aktuellen Kader & Gegner| ESPN_R
    DecisionEngine -->|Structured Prompt| NineRouter
    NineRouter --> GeminiFlash
    GeminiFlash -->|JSON Recommendation| DecisionEngine
    DecisionEngine --> Safeguards
    
    Safeguards -->|Modus SEMI: Vorschlag speichern| API_Proposals
    API_Proposals --> UI_Banner
    Safeguards -->|Modus SEMI: Benachrichtigung| TelegramGate
    UI_Banner -->|Klick: Freigeben| API_Proposals
    API_Proposals --> WriteClient

    Safeguards -->|Modus FULL: Autonom ausführen| WriteClient
    WriteClient -->|POST ROSTER Transaction| ESPN_W
    WriteClient -->|Aktionsbericht| TelegramGate
    WriteClient --> UI_Audit
```

### 2.2 Sequenzdiagramm: Semi-Modus (Vorschlag & Freigabe)

```mermaid
sequenceDiagram
    autonumber
    actor User as Commander (User)
    participant LCARS as LCARS Dashboard
    participant Backend as Flask API & Service
    participant 9Router as 9Router (Port 20128)
    participant ESPN as ESPN API
    participant Telegram as Telegram Bot / Hermes

    Backend->>Backend: Scheduler erkennt bevorstehenden Spieltag (z.B. So 18:00)
    Backend->>ESPN: Lese Kader, Gegner & Verletzungsstatus
    ESPN-->>Backend: Roster-Daten (Nico Collins = OUT)
    Backend->>9Router: Prompt mit Kader, Matchup & Projektionen
    9Router-->>Backend: JSON: Empfehle Swap Nico Collins -> Jameson Williams
    Backend->>Backend: Safeguards prüfen (Slots, Lock-Zeiten, IR): VALID
    Backend->>Backend: Speichere Proposal in espn_proposals.json
    Backend->>Telegram: Sende Vorschlag mit Details & LCARS-Link
    Telegram-->>User: Push-Nachricht erhalten
    User->>LCARS: Öffnet Sektion FANTASY
    LCARS->>Backend: GET /api/espn/proposals
    Backend-->>LCARS: Aktiver Vorschlag
    LCARS->>User: Zeigt goldenes Vorschlags-Banner
    User->>LCARS: Klick auf [ FREIGEBEN & AUSFÜHREN ]
    LCARS->>Backend: POST /api/espn/proposals/<id>/apply
    Backend->>ESPN: POST /transactions/ (executionType: EXECUTE)
    ESPN-->>Backend: HTTP 200 OK (Transaktion bestätigt)
    Backend->>LCARS: Status: ERFOLGREICH
    Backend->>Telegram: "✅ Move erfolgreich ausgeführt"
```

### 2.3 Sequenzdiagramm: Full-Modus (Autonomer Kickoff-Schutz)

```mermaid
sequenceDiagram
    autonumber
    participant Scheduler as Kickoff-Scheduler
    participant Engine as Safeguard- & KI-Engine
    participant 9Router as 9Router (Gemini 3.8 Flash)
    participant ESPN_W as ESPN lm-api-writes
    participant Telegram as Telegram Benachrichtigung

    Scheduler->>Engine: Kickoff-Check T-15 Minuten vor NFL-Spielen
    Engine->>Engine: Prüfe Starter auf Verletzungen (OUT / IR / DOUBTFUL)
    alt Starter ist verletzt (z.B. RB2 Chuba Hubbard OUT)
        Engine->>9Router: Ermittle besten Ersatzspieler auf der Bank
        9Router-->>Engine: Vorschlag: Ty Johnson (RB) starten
        Engine->>Engine: Safeguards: Ty Johnson nicht gelockt? Slot berechtigt?
        Engine->>ESPN_W: Dry-Run (executionType: VALIDATE)
        ESPN_W-->>Engine: HTTP 200 OK
        Engine->>ESPN_W: Live-Ausführung (executionType: EXECUTE)
        ESPN_W-->>Engine: Transaktion verarbeitet
        Engine->>Engine: Schreibe Audit-Log in espn_decision_log.json
        Engine->>Telegram: "🏈 [FULL-AUTONOM] Kickoff-Optimierung: Chuba Hubbard (OUT) durch Ty Johnson ersetzt."
    else Alle Starter fit & aktiv
        Engine->>Engine: Keine Aktion erforderlich, Lineup optimal
    end
```

---

## 3. Konfiguration & Datenmodell

### 3.1 Erweiterung in `config.json`
Die Sektion `espn_fantasy` in `/home/cb/Projects/agydashboard/config.json` wird um die Modus- und KI-Steuerungsparameter erweitert:

```json
{
  "espn_fantasy": {
    "enabled": true,
    "league_id": 378649793,
    "team_id": 17,
    "season_year": 2026,
    "swid": "{553C1E20-D00A-4967-8DEB-7B50CB7C5914}",
    "espn_s2": "AEAgQbeHCIrgaePAfzf1jGiVgIXZHv1%2Fb%2FMKQqE5y3o%2BikszTjFrlNubnn6zhkwFO44Mmgeyp0I67iLVPFdi8rMZ35Ybn791r%2Bme9n7RhB4xwWMFeRGYA8aY7cBwOK%2FluIRBBkatk98g9Jr2GKcumt8l%2F0EfaY9woFIjTVciAlkduD6992NYqolAklw6xbENwSj66vG563%2FOqer82hZ%2BcnrnjlVNNSbwp0dfGVqCPjSSiR68ALYIqWju2lWVkC0jCh1oIasi2lO1B6biDYDQ9IUv8nQdGhmhpJgikUEkidht1ZRV4xKkZSLfnVqkSNXULY6aNzYwDRe414TgfTBOdYsa",
    "flash_light": "light.esstisch",
    "flash_duration": 1.2,
    "flash_enabled": true,
    "poll_interval": 35,
    "mode": "manual",
    "ai_model": "ag/gemini-3.8-flash-high",
    "kickoff_check_buffer_minutes": 15,
    "telegram_notifications": true,
    "telegram_bot_token": "",
    "telegram_chat_id": ""
  }
}
```

### 3.2 Persistente Zustandsdateien
1. **`espn_proposals.json`**:
   Speichert aktive, noch nicht bearbeitete Vorschläge im `semi`-Modus:
   ```json
   {
     "active_proposals": [
       {
         "id": "prop_20260921_w2_01",
         "created_at": 1790025600,
         "week": 2,
         "status": "pending",
         "reason": "Nico Collins ist offiziell OUT. Jameson Williams bietet das höchste Upside auf WR/FLEX.",
         "confidence": 0.94,
         "moves": [
           {
             "player_in_id": 4426515,
             "player_in_name": "Jameson Williams",
             "from_slot": 20,
             "to_slot": 4
           },
           {
             "player_out_id": 4430878,
             "player_out_name": "Nico Collins",
             "from_slot": 4,
             "to_slot": 20
           }
         ]
       }
     ]
   }
   ```
2. **`espn_decision_log.json`**:
   Audit-Trail aller analysierten und ausgeführten Aktionen für Transparenz im Dashboard.

---

## 4. ESPN API Interaktion: Roster Moves & Lineup Updates

### 4.1 Endpunkt & Autorisierung
Für schreibende Transaktionen nutzt die ESPN Fantasy Plattform die dedizierte Write-Domain:
- **URL**: `https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}/transactions/`
- **Methode**: `POST`
- **Header**:
  ```http
  Content-Type: application/json
  Accept: application/json
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
  Cookie: SWID={swid}; espn_s2={espn_s2};
  ```

### 4.2 Transaktions-Payload für Start/Sit Lineup-Swaps
Ein Spielerwechsel zwischen Startaufstellung und Bank erfordert immer **zwei Items** (eingehender und ausgehender Spieler):

```json
{
  "isLeagueManager": false,
  "teamId": 17,
  "type": "ROSTER",
  "scoringPeriodId": 2,
  "executionType": "EXECUTE",
  "items": [
    {
      "playerId": 4426515,
      "type": "LINEUP",
      "fromLineupSlotId": 20,
      "toLineupSlotId": 4
    },
    {
      "playerId": 4430878,
      "type": "LINEUP",
      "fromLineupSlotId": 4,
      "toLineupSlotId": 20
    }
  ]
}
```

### 4.3 Slot-ID Referenztabelle (ESPN Fantasy Football)
| Slot ID | Label | Berechtigte Positionen / Bedeutung |
|---|---|---|
| `0` | **QB** | Quarterback (`POS 1`) |
| `1` | **TQB** | Team Quarterback |
| `2` | **RB** | Running Back (`POS 2`) |
| `3` | **RB/WR** | Running Back oder Wide Receiver |
| `4` | **WR** | Wide Receiver (`POS 3`) |
| `5` | **WR/TE** | Wide Receiver oder Tight End |
| `6` | **TE** | Tight End (`POS 4`) |
| `7` | **OP** | Offensive Player / Superflex (QB/RB/WR/TE) |
| `16` | **D/ST** | Defense / Special Teams (`POS 16`) |
| `17` | **K** | Kicker (`POS 5`) |
| `20` | **BENCH** | Bank (inaktiv für Punktwertung) |
| `21` | **IR** | Injured Reserve (nur für `OUT` / `INJURY_RESERVE`) |
| `23` | **FLEX** | Flex-Position (RB/WR/TE) |

### 4.4 Dry-Run Prüfung via `VALIDATE`
Vor jeder echten Ausführung kann der Payload mit `"executionType": "VALIDATE"` gesendet werden. ESPN führt eine serverseitige Konsistenzprüfung durch (z.B. Gültigkeit der Slots, Sperrstatus) und liefert Status 200 zurück, ohne die Aufstellung tatsächlich zu verändern.

---

## 5. KI-Entscheidungs-Engine (9Router / Gemini 3.8 Flash)

### 5.1 Infrastruktur & Modell
- **Gateway**: Lokaler 9Router Proxy auf Port `20128` (`http://127.0.0.1:20128/v1/chat/completions`).
- **Primärmodell**: `ag/gemini-3.8-flash-high` (hohe analytische Tiefe, minimale Latenz, exzellente Befolgung von JSON-Schemas).
- **Fallback**: Bei Unerreichbarkeit des 9Routers greift ein regelbasierter Notfall-Optimizer (Heuristik).

### 5.2 Prompt-Konstruktion & Kontext
Der Prompt übergibt dem LLM das vollständige Bild:
1. Eigene Aufstellung gegliedert nach Startern, Bank und IR mit:
   - `name`, `pro_team`, `slot`, `injuryStatus` (`ACTIVE`, `QUESTIONABLE`, `DOUBTFUL`, `OUT`, `INJURY_RESERVE`)
   - `actualPoints`, `projectedPoints`, `eligibleSlots`, `lineupLocked`
2. Matchup-Status: Eigener Score & Projektion vs. Gegner Score & Projektion, Siegchance.
3. Kickoff-Zeiten der NFL-Partien aus `proGamesByScoringPeriod`.

### 5.3 System Prompt & Structured Output Schema
```json
{
  "system_instruction": "Du bist der Starfleet LCARS Fantasy Football Taktik-Offizier der USS Antigravity. Analysiere das Kader nach strengen sportwissenschaftlichen und mathematischen Kriterien. Ein verletzter Starter mit Status OUT oder INJURY_RESERVE MUSS IMMER gegen einen fitten Bankspieler ausgetauscht werden. Nutze für Moves nur Spieler, die noch nicht gelockt sind (lineupLocked == false) und deren eligibleSlots mit dem Ziel-Slot übereinstimmen. Antworte ausschließlich mit einem validen JSON-Objekt.",
  "response_format": {
    "type": "json_object"
  }
}
```

**JSON Output Schema**:
```json
{
  "assessment": "Strategische Kurzzusammenfassung der Lage",
  "win_probability_impact": "+4.2%",
  "recommended_moves": [
    {
      "action": "SWAP",
      "player_in_id": 4426515,
      "player_in_name": "Jameson Williams",
      "from_slot_in": 20,
      "to_slot_in": 4,
      "player_out_id": 4430878,
      "player_out_name": "Nico Collins",
      "from_slot_out": 4,
      "to_slot_out": 20,
      "rationale": "Nico Collins ist offiziell OUT. Williams hat ein vorteilhaftes Matchup.",
      "projected_gain": 11.8
    }
  ],
  "waiver_targets": [],
  "confidence": 0.95
}
```

### 5.4 Regelbasierter Notfall-Fallback
Sollte der 9Router oder die KI temporär ausfallen (z.B. Offline-Netzwerk, HTTP 502), wird der `full`-Modus nicht blockiert:
- **Heuristische Regel**:
  Wenn ein Starter den Status `OUT` oder `INJURY_RESERVE` hat, sucht die Heuristik auf der Bank nach dem fitten Spieler (`ACTIVE` oder `QUESTIONABLE`), der den passenden `eligibleSlot` besitzt und die **höchste offizielle ESPN-Projektion** aufweist. Dieser wird als Notfall-Wechsel eingereicht.

---

## 6. Risiken, Safeguards & Validierungs-Engine

Um fatale Fehlentscheidungen, ungültige Roster-Zustände oder Disqualifikationen in der Liga auszuschließen, ist der Ausführung eine mehrstufige Safeguard-Engine vorgeschaltet:

```mermaid
flowchart TD
    Start[Vorgeschlagener Roster Move] --> C1{Kickoff erfolgt oder\nlineupLocked == true?}
    C1 -- Ja --> E1[ABBRUCH: Spieler ist gelockt\nKeine Transaktion möglich]
    C1 -- Nein --> C2{toLineupSlotId in\nplayer.eligibleSlots?}
    C2 -- Nein --> E2[ABBRUCH: Ungültiger Slot\nPositions-Inkompatibilität]
    C2 -- Ja --> C3{Ist Ziel-Slot == 21 IR?}
    C3 -- Ja --> C4{Hat Spieler injuryStatus\nOUT oder INJURY_RESERVE?}
    C4 -- Nein --> E3[ABBRUCH: Gesunder Spieler\ndarf nicht auf IR gesetzt werden]
    C4 -- Ja --> C5
    C3 -- Nein --> C5{Prüfung Gegenpart:\nWird verletzter Starter ersetzt?}
    C5 --> C6{Pre-Flight Dry Run:\nexecutionType == VALIDATE}
    C6 -- ESPN meldet Fehler (z.B. 400/401) --> E4[ABBRUCH & ALERT: Transaktion abgelehnt]
    C6 -- ESPN meldet 200 OK --> OK[FREIGABE: Ausführung an ESPN lm-api-writes]
```

### 6.1 Die Kern-Safeguards im Detail:
1. **Lineup Lock Safeguard (Gestartete Spiele)**:
   - Die ESPN API liefert in `playerPoolEntry` das native Flag `lineupLocked: true/false`.
   - Zusätzlich wird der Kickoff-Timestamp des NFL-Teams (`proGamesByScoringPeriod.date`) geprüft. Liegt dieser in der Vergangenheit oder weniger als 60 Sekunden in der Zukunft, wird der Move strikt verweigert.
2. **Positions- & Slot-Eligibility Safeguard**:
   - Die Liste `player.eligibleSlots` definiert verbindlich, welche Positionen ein Spieler bekleiden kann. Ein Move auf einen Slot, der nicht in diesem Array enthalten ist, wird im Vorfeld blockiert.
3. **IR-Slot Schutz**:
   - Ein gesunder Spieler auf einem IR-Slot (Slot 21) markiert das Roster in ESPN als "Ineligible Roster" und blockiert alle nachfolgenden Transaktionen. Daher darf ein Spieler nur dann auf IR geschoben werden, wenn `injuryStatus in ["OUT", "INJURY_RESERVE"]` zutrifft.
4. **Rate Limiting & Cooldown**:
   - Zwischen zwei Transaktionen wird ein Mindestabstand von 10 Sekunden erzwungen.
   - Pro Spieltag sind maximal 5 autonome Swaps im `full`-Modus erlaubt, um Endlos-Schleifen bei Scoring-Schwankungen zu unterbinden.
5. **Cookie-Validierung & Expiry Protection**:
   - Alle 6 Stunden sowie vor jeder Transaktion erfolgt ein Lese-Check (`mMatchup`).
   - Meldet ESPN HTTP 401 oder 403, wird der Modus automatisch auf `manual` zurückgesetzt, alle Automatismen gestoppt und ein rotes Warnbanner im Dashboard aktiviert: *"ESPN Authentifizierung abgelaufen – bitte SWID / espn_s2 in config.json aktualisieren"*.

---

## 7. Backend REST-API Spezifikation (`app.py`)

Folgende Endpunkte werden in `app.py` implementiert:

| Endpunkt | Methode | Beschreibung | Payload / Rückgabe |
|---|---|---|---|
| `/api/espn/mode` | `GET` | Liefert aktuellen Betriebsmodus & Status | `{"status": "ok", "mode": "manual"\|"semi"\|"full", "updated_at": "..."}` |
| `/api/espn/mode` | `POST` | Ändert Betriebsmodus & speichert in `config.json` | Request: `{"mode": "semi"}`<br>Response: `{"success": true, "mode": "semi"}` |
| `/api/espn/analyze` | `POST` | Startet manuelle KI-Analyse des aktuellen Rosters | Request: `{}`<br>Response: `{"status": "ok", "recommendation": {...}}` |
| `/api/espn/proposals` | `GET` | Ruft offene Vorschläge für den Semi-Modus ab | `{"proposals": [...]}` |
| `/api/espn/proposals/<id>/apply` | `POST` | Genehmigt Vorschlag und führt ihn via ESPN API aus | `{"success": true, "message": "Lineup aktualisiert"}` |
| `/api/espn/proposals/<id>/dismiss` | `POST` | Verwirft einen Vorschlag | `{"success": true, "dismissed": "<id>"}` |
| `/api/espn/lineup/move` | `POST` | Direkter Roster-Move mit optionalem Dry-Run | Request: `{"items": [...], "dry_run": false}` |
| `/api/espn/history` | `GET` | Audit-Trail der letzten KI-Entscheidungen & Moves | `{"history": [...]}` |

---

## 8. LCARS Frontend Integration (Dashboard UI)

### 8.1 Drei-Stufen Modus-Schalter im Card-Header
In der ESPN Fantasy Card (`<section class="lcars-section" id="section-fantasy">`) wird im Header ein dreiteiliger LCARS Pill-Schalter platziert:

```html
<div class="espn-mode-switcher-container">
  <span class="lcars-pill-tag" style="margin-right:0.5rem;">KI-MODUS:</span>
  <div class="lcars-btn-group">
    <button type="button" class="lcars-subnav-pill mode-btn active" id="btn-espn-mode-manual" onclick="setEspnMode('manual')">
      MANUAL
    </button>
    <button type="button" class="lcars-subnav-pill mode-btn" id="btn-espn-mode-semi" onclick="setEspnMode('semi')">
      SEMI-AI
    </button>
    <button type="button" class="lcars-subnav-pill mode-btn" id="btn-espn-mode-full" onclick="setEspnMode('full')">
      FULL-AI
    </button>
  </div>
  <span id="espnModeBadge" class="espn-mode-indicator badge-manual">
    ● PASSIV // REINES MONITORING
  </span>
</div>
```

### 8.2 Vorschlags-Banner im Semi-Modus
Wenn im `semi`-Modus ein aktiver Vorschlag vorliegt, blendet das Dashboard oberhalb der Aufstellung ein goldenes LCARS-Aktions-Banner ein:

```html
<div id="espnProposalBanner" class="lcars-card proposal-alert" style="display:none;">
  <div style="display:flex; justify-content:space-between; align-items:flex-start;">
    <div>
      <div style="font-size:0.75rem; color:var(--c-gold); font-weight:700; letter-spacing:0.08em;">
        ⚠️ KI-EMPFEHLUNG // FREIGABE ERFORDERLICH
      </div>
      <div id="proposalReasonText" style="font-size:1.05rem; color:#fff; margin-top:0.3rem; font-weight:600;">
        Nico Collins ist OUT. Tausche gegen Jameson Williams (WR).
      </div>
      <div id="proposalGainText" style="font-size:0.85rem; color:var(--c-blue); margin-top:0.2rem;">
        Erwarteter Punktgewinn: +11.8 PTS
      </div>
    </div>
    <div style="display:flex; gap:0.5rem;">
      <button class="lcars-pill-btn pill-green" onclick="applyEspnProposal()" style="font-size:0.8rem; padding:0.4rem 0.9rem;">
        ⚡ FREIGEBEN
      </button>
      <button class="lcars-pill-btn pill-red" onclick="dismissEspnProposal()" style="font-size:0.8rem; padding:0.4rem 0.9rem;">
        ✕ VERWERFEN
      </button>
    </div>
  </div>
</div>
```

---

## 9. Benachrichtigungsintegration (Telegram & Home Assistant)

### 9.1 Versandwege
- **Primär**: Direkter Telegram Bot API Call (`https://api.telegram.org/bot<token>/sendMessage`) oder über den lokalen User-Service `hermes-gateway` auf PiMMEL.
- **Sekundär**: Home Assistant Service `notify.telegram` bzw. Persistent Notification.

### 9.2 Nachrichten-Templates
- **Semi-Modus Proposal**:
  ```text
  🏈 [LCARS ESPN KI-MANAGER // SEMI]
  Spieltag 2: Aufstellungs-Optimierung empfohlen!

  🔄 Move: Jameson Williams (Bench ➔ WR)
     für: Nico Collins (WR ➔ Bench [OUT])
  
  💡 Begründung: Collins fällt wegen Oberschenkelverletzung aus. Williams projiziert 11.8 PTS gegen schwache Pass-Defense.
  
  👉 Freigeben im Dashboard: https://dash.pimmel.site#fantasy
  ```
- **Full-Modus Autonomer Vollzug**:
  ```text
  ⚡ [LCARS ESPN KI-MANAGER // FULL-AUTONOM]
  Kickoff-Schutz ausgeführt (T-15 Min):
  
  ✅ Nico Collins (OUT) auf die Bank verschoben.
  ✅ Jameson Williams als Starter auf WR aufgestellt.
  
  Status: Transaktion von ESPN bestätigt.
  ```

---

## 10. Detaillierter Implementierungs- und Testplan

### Phase 1: Datenmodell & Konfiguration in `espn_service.py`
1. Erweiterung der Klasse `EspnFantasyClient`:
   - `get_mode()` und `set_mode(mode)`.
   - Speichern von `espn_fantasy.mode` in `config.json`.
   - Persistenzmethoden für `espn_proposals.json` und `espn_decision_log.json`.

### Phase 2: ESPN Write-Client & Transaktions-Engine
1. Implementierung der Methode `execute_roster_transaction(items, execution_type="EXECUTE")`:
   - POST Request an `lm-api-writes.fantasy.espn.com`.
   - Beachtung von `swid` und `espn_s2` Headern.
   - Fehler-Parsing und Mapping von ESPN HTTP Statuscodes.

### Phase 3: Safeguard- & Validierungsmodul
1. Implementierung von `validate_roster_move(items, roster_entries)`:
   - Abgleich gegen `eligibleSlots`.
   - Prüfung von `lineupLocked` und Kickoff-Timestamps.
   - Validierung der IR-Slot-Sonderregeln.

### Phase 4: KI-Entscheidungs-Engine (Gemini 3.8 Flash)
1. Implementierung von `analyze_roster_with_ai(roster_data, matchup_data)`:
   - JSON-Prompt-Aufbereitung.
   - Aufruf von `forward_chat_completion` via 9Router (Port 20128) mit Modell `ag/gemini-3.8-flash-high`.
   - Parsing und Validierung des LLM-Outputs.
   - Fallback-Heuristik bei Ausfall des KI-Proxys.

### Phase 5: Hintergrund-Scheduler
1. Erweiterung des Daemon-Threads in `espn_service.py`:
   - Überwachung von NFL-Spielplänen und Kickoff-Timestamps.
   - Triggering der Safeguards & Moves im `full`-Modus.
   - Triggering der Proposals im `semi`-Modus.

### Phase 6: Flask API-Routen in `app.py`
1. Registrierung aller neuen Endpunkte (`/api/espn/mode`, `/api/espn/analyze`, `/api/espn/proposals`, etc.).
2. Absicherung über das Berechtigungssystem (`permissions_service`).

### Phase 7: LCARS Dashboard Frontend
1. HTML-Markup für den 3-Stufen Schalter und das Proposals-Banner in `section-fantasy`.
2. CSS-Styling für Modus-Pills (`manual`, `semi`, `full`) und Alert-Banner.
3. JavaScript-Logik in `app.py` für State-Synchronisation, Modus-Wechsel und Banner-Aktionen.

### Phase 8: Verifikation & Systemtests
1. **Statische Code-Prüfung**: `python3 -m py_compile app.py espn_service.py`.
2. **Unit-Tests**:
   - Validierung aller Safeguard-Regeln mit synthetischen Roster-Objekten.
   - Test des Modus-Wechsels und Persistenz in `config.json`.
3. **API-Endpunkttests**: Curl-Aufrufe aller neuen Routen.
4. **Live-Dry-Run**: Durchführung einer Test-Validierung gegen die echte ESPN API mit `executionType: "VALIDATE"`.
5. **Systemd-Service Verifikation**: Neustart von `agydashboard.service` und Log-Kontrolle.

