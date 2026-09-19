#!/usr/bin/env python3
"""
LCARS Asynchronous Auth Reverse Proxy for Cloudflare Named Tunnel (*.pimmel.site).
Runs on 127.0.0.1:5050. Intercepts incoming external traffic from cloudflared for protected services,
verifies lcars_session cookie against user_service (users.db), checks role permissions,
redirects unauthenticated requests to https://dash.pimmel.site/login,
and forwards authorized requests (HTTP, Range Streaming, WebSockets) to local service ports.
"""

import asyncio
import os
import re
import sys
import threading
import urllib.parse
from datetime import datetime

try:
    from user_service import user_service
except ImportError:
    from .user_service import user_service

DEFAULT_PORT = 5050
DASHBOARD_LOGIN_URL = "https://dash.pimmel.site/login"

# Standard-Zuordnungen: Subdomain -> {port, service_key}
DEFAULT_SUBDOMAIN_MAP = {
    "cast": {"port": 3000, "service": "pulsecast", "name": "PulseCast"},
    "tele": {"port": 8000, "service": "telemetryvault", "name": "TelemetryVault"},
    "mat": {"port": 5580, "service": "matter", "name": "Matter Server"},
    "head": {"port": 8787, "service": "headroom", "name": "Headroom AI"},
    "port": {"port": 631, "service": "cups", "name": "CUPS Druckerdienst"},
}


class LcarsAuthProxy:
    def __init__(self, host="127.0.0.1", port=DEFAULT_PORT, login_url=DASHBOARD_LOGIN_URL, user_svc=None):
        self.host = host
        self.port = port
        self.login_url = login_url
        self.user_service = user_svc or user_service
        self.subdomain_map = dict(DEFAULT_SUBDOMAIN_MAP)
        self.server = None
        self._running = False

    def register_subdomain(self, subdomain: str, target_port: int, service_key: str = None, name: str = None):
        subdomain = subdomain.strip().lower()
        self.subdomain_map[subdomain] = {
            "port": target_port,
            "service": service_key or subdomain,
            "name": name or subdomain.upper(),
        }

    def resolve_subdomain(self, host_header: str) -> tuple[str, dict] | tuple[None, None]:
        if not host_header:
            return None, None
        host_only = host_header.split(":")[0].strip().lower()
        sub = host_only.split(".")[0]
        if sub in self.subdomain_map:
            return sub, self.subdomain_map[sub]
        return None, None

    @staticmethod
    def parse_cookie_header(cookie_header: str) -> dict:
        cookies = {}
        if not cookie_header:
            return cookies
        for part in cookie_header.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()
        return cookies

    async def handle_client(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
        client_addr = client_writer.get_extra_info("peername")
        try:
            # 1. Lies Request-Line und Header bis \r\n\r\n
            header_bytes = b""
            while b"\r\n\r\n" not in header_bytes:
                chunk = await client_reader.read(4096)
                if not chunk:
                    client_writer.close()
                    await client_writer.wait_closed()
                    return
                header_bytes += chunk
                if len(header_bytes) > 65536:  # Max 64KB Header
                    self._send_error_response(client_writer, 431, "Request Header Fields Too Large")
                    return

            headers_part, remainder = header_bytes.split(b"\r\n\r\n", 1)
            header_lines = headers_part.decode("utf-8", errors="replace").split("\r\n")
            if not header_lines or not header_lines[0]:
                client_writer.close()
                return

            req_line = header_lines[0]
            req_parts = req_line.split(" ")
            if len(req_parts) < 2:
                client_writer.close()
                return

            method, path = req_parts[0], req_parts[1]
            headers = {}
            for line in header_lines[1:]:
                if ": " in line:
                    k, v = line.split(": ", 1)
                    headers[k.strip().lower()] = v.strip()

            host_header = headers.get("host", "")
            subdomain, service_info = self.resolve_subdomain(host_header)

            if not service_info:
                # Nicht zugeordneter Host
                self._send_not_found(client_writer, host_header)
                return

            target_port = service_info["port"]
            service_key = service_info["service"]

            # 2. Authentifizierung via lcars_session Cookie
            cookie_header = headers.get("cookie", "")
            cookies = self.parse_cookie_header(cookie_header)
            session_id = cookies.get("lcars_session", "")

            # Client-IP (Cloudflare Edge leitet echte Client-IP im Header weiter)
            client_ip = headers.get("cf-connecting-ip") or headers.get("x-forwarded-for", "").split(",")[0].strip() or (client_addr[0] if client_addr else "unknown")
            user_agent = headers.get("user-agent", "")

            user_session = self.user_service.validate_session(session_id) if session_id else None
            is_websocket = (headers.get("upgrade", "").lower() == "websocket")

            if not user_session:
                # Nicht authentifiziert
                self.user_service.log_audit(
                    username="ANONYMOUS",
                    event="ACCESS_DENIED_UNAUTH",
                    target_service=service_key,
                    ip=client_ip,
                    user_agent=user_agent,
                    details=f"Host: {host_header}, Path: {path}",
                )

                accept_header = headers.get("accept", "")
                is_html_req = ("text/html" in accept_header or method == "GET") and not path.startswith("/api/") and not is_websocket

                if is_html_req:
                    # 302 Redirect zur LCARS Login-Seite
                    target_full_url = f"https://{host_header}{path}"
                    encoded_return = urllib.parse.quote(target_full_url, safe="")
                    redirect_url = f"{self.login_url}?return_to={encoded_return}"

                    resp = (
                        f"HTTP/1.1 302 Found\r\n"
                        f"Location: {redirect_url}\r\n"
                        f"Cache-Control: no-store, no-cache, must-revalidate\r\n"
                        f"Content-Length: 0\r\n"
                        f"Connection: close\r\n\r\n"
                    )
                    client_writer.write(resp.encode("utf-8"))
                    await client_writer.drain()
                    client_writer.close()
                    return
                else:
                    # 401 Unauthorized für API / WebSockets
                    err_body = '{"error": "Unauthorized", "login_url": "' + self.login_url + '"}\n'
                    resp = (
                        f"HTTP/1.1 401 Unauthorized\r\n"
                        f"Content-Type: application/json\r\n"
                        f"Content-Length: {len(err_body)}\r\n"
                        f"Connection: close\r\n\r\n"
                        f"{err_body}"
                    )
                    client_writer.write(resp.encode("utf-8"))
                    await client_writer.drain()
                    client_writer.close()
                    return

            # 3. Autorisierungsprüfung (Darf dieser User den Service nutzen?)
            has_perm = self.user_service.check_service_permission(user_session, service_key)
            if not has_perm:
                self.user_service.log_audit(
                    username=user_session["username"],
                    event="ACCESS_DENIED_FORBIDDEN",
                    target_service=service_key,
                    ip=client_ip,
                    user_agent=user_agent,
                    details=f"User lacks permission for {service_key}",
                )
                self._send_forbidden(client_writer, user_session["username"], service_info["name"])
                return

            # 4. Verbindung zum Zielport aufbauen
            try:
                target_reader, target_writer = await asyncio.open_connection("127.0.0.1", target_port)
            except Exception as e:
                self._send_bad_gateway(client_writer, service_info["name"], target_port, str(e))
                return

            # 5. Header für Upstream anpassen
            # Wenn kein WebSocket: Verwende Connection: close für sauberes HTTP-Pipelining
            out_headers = []
            for line in header_lines[1:]:
                if ": " not in line:
                    continue
                k, v = line.split(": ", 1)
                lk = k.lower()
                if not is_websocket and lk == "connection":
                    continue
                out_headers.append(line)

            if not is_websocket:
                out_headers.append("Connection: close")

            # Hilfreiche Forward-Header injizieren
            out_headers.append(f"X-Forwarded-User: {user_session['username']}")
            out_headers.append(f"X-Forwarded-For: {client_ip}")
            out_headers.append("X-Forwarded-Proto: https")

            new_header_block = req_line + "\r\n" + "\r\n".join(out_headers) + "\r\n\r\n"
            target_writer.write(new_header_block.encode("utf-8"))

            if remainder:
                target_writer.write(remainder)
            await target_writer.drain()

            # 6. Bidirektionales Pipe-Streaming (HTTP Streaming & WebSockets)
            await asyncio.gather(
                self._pipe(client_reader, target_writer),
                self._pipe(target_reader, client_writer),
                return_exceptions=True,
            )

        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            print(f"[AUTH_PROXY_ERR] Fehler bei Request-Verarbeitung: {e}", file=sys.stderr)
        finally:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    def _send_not_found(self, writer: asyncio.StreamWriter, host: str):
        body = (
            f"<!DOCTYPE html><html><head><title>404 - Unknown Host</title></head>"
            f"<body style='background:#050505; color:#ff9900; font-family:monospace; text-align:center; padding:50px;'>"
            f"<h2>LCARS // 404 UNBEKANNTER SUBRAUM-HOST</h2>"
            f"<p>Hostname '{host}' ist nicht registriert.</p>"
            f"</body></html>"
        )
        resp = (
            f"HTTP/1.1 404 Not Found\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body.encode('utf-8'))}\r\n"
            f"Connection: close\r\n\r\n"
            f"{body}"
        )
        writer.write(resp.encode("utf-8"))

    def _send_forbidden(self, writer: asyncio.StreamWriter, username: str, service_name: str):
        body = (
            f"<!DOCTYPE html><html><head><title>403 - LCARS Access Denied</title></head>"
            f"<body style='background:#050505; color:#ff3333; font-family:monospace; text-align:center; padding:50px;'>"
            f"<h1 style='color:#ff9900; letter-spacing:2px;'>LCARS SICHERHEITSPROTOKOLL // 403 ACCESS DENIED</h1>"
            f"<p style='color:#ffffff; font-size:1.1rem;'>ZUGRIFF VERWEIGERT FÜR BENUTZER: <b style='color:#ffcc00;'>{username}</b></p>"
            f"<p style='color:#aaaaaa;'>Ihr Account verfügt nicht über die erforderliche Sicherheitsstufe für: <b style='color:#ffffff;'>{service_name}</b></p>"
            f"<p style='margin-top:30px;'><a href='https://dash.pimmel.site' style='color:#ff9900; text-decoration:none; border:1px solid #ff9900; padding:10px 20px; border-radius:4px;'>ZURÜCK ZUM LCARS DASHBOARD</a></p>"
            f"</body></html>"
        )
        resp = (
            f"HTTP/1.1 403 Forbidden\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body.encode('utf-8'))}\r\n"
            f"Connection: close\r\n\r\n"
            f"{body}"
        )
        writer.write(resp.encode("utf-8"))

    def _send_bad_gateway(self, writer: asyncio.StreamWriter, service_name: str, port: int, err_msg: str):
        body = (
            f"<!DOCTYPE html><html><head><title>502 - Bad Gateway</title></head>"
            f"<body style='background:#050505; color:#ff3333; font-family:monospace; text-align:center; padding:50px;'>"
            f"<h2>LCARS // 502 SUBRAUM-RELAY OFFLINE</h2>"
            f"<p>Der lokale Zieldienst <b>{service_name}</b> (Port {port}) antwortet nicht.</p>"
            f"<p style='color:#888;'>Details: {err_msg}</p>"
            f"</body></html>"
        )
        resp = (
            f"HTTP/1.1 502 Bad Gateway\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body.encode('utf-8'))}\r\n"
            f"Connection: close\r\n\r\n"
            f"{body}"
        )
        writer.write(resp.encode("utf-8"))

    def _send_error_response(self, writer: asyncio.StreamWriter, status_code: int, status_text: str):
        resp = f"HTTP/1.1 {status_code} {status_text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        writer.write(resp.encode("utf-8"))

    async def start(self):
        try:
            self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
            self._running = True
            addr = self.server.sockets[0].getsockname()
            print(f"[AUTH_PROXY] LCARS Auth Reverse Proxy aktiv auf http://{addr[0]}:{addr[1]}", flush=True)
        except OSError as e:
            print(f"[AUTH_PROXY] Hinweis: Port {self.port} bereits aktiv oder belegt ({e})", file=sys.stderr)

    async def serve_forever(self):
        await self.start()
        if self.server:
            async with self.server:
                await self.server.serve_forever()

    def start_background(self):
        if self._running:
            return None
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.serve_forever())
            except Exception as e:
                print(f"[AUTH_PROXY] Hintergrundserver beendet: {e}", file=sys.stderr)
        t = threading.Thread(target=_run, daemon=True, name="LcarsAuthProxyThread")
        t.start()
        return t

    def stop(self):
        if self.server:
            self.server.close()
            self._running = False


auth_proxy = LcarsAuthProxy()

if __name__ == "__main__":
    try:
        asyncio.run(auth_proxy.serve_forever())
    except KeyboardInterrupt:
        print("[AUTH_PROXY] Beendet.")
