"""Jarvis SIP/VoIP Client (pure Python, asyncio).

Diese Datei IST der SIP-Stack: sie implementiert das nötige SIP-Protokoll
(REGISTER mit Digest-Auth, INVITE, ACK, BYE, CANCEL, OPTIONS) selbst über
UDP-Sockets und asyncio – ohne ``pjsip``/``python-sipsimple`` als
Abhängigkeit.

Phase-1 Funktionalität:
- SIP-Registrierung beim Provider
- Ausgehender Anruf (INVITE → 1xx → 2xx → ACK)
- Anruf beenden (BYE) bzw. abbrechen (CANCEL)
- Anruf-Status (init/trying/ringing/connected/ended/failed)
- Kontakt-Auflösung mit Fuzzy-Matching aus ``config.contacts``

Phase-5 (RTP/Audio) ist bewusst nicht implementiert: das SDP annonciert
einen reservierten lokalen UDP-Port und die Codecs PCMU/PCMA, damit der
Anruf signalisierungstechnisch zustande kommt. Ein echter Audio-Pfad
muss separat dort angeschlossen werden.

Public API:
    SIPConfig.from_config(config)
    SIPClient(sip_config, on_history=callback)
    await client.start()
    await client.make_call(contact_name="Mama")
    await client.hangup()
    await client.stop()
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import socket
import time
import uuid
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


SIP_VERSION = "SIP/2.0"
DEFAULT_PORT = 5060
DEFAULT_TRANSPORT = "UDP"
DEFAULT_REGISTER_EXPIRES = 600
INVITE_TIMEOUT_SECONDS = 32.0
REGISTER_TIMEOUT_SECONDS = 8.0


# ── Datenklassen ──────────────────────────────────────────────────────────


@dataclass
class SIPConfig:
    """SIP-Konfiguration aus ``config.json``."""

    enabled: bool
    server: str
    port: int
    username: str
    password: str
    domain: str
    transport: str
    display_name: str
    user_agent: str
    register_expires: int
    contacts: Dict[str, str]

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "SIPConfig":
        sip = (config or {}).get("sip") or {}
        contacts_raw = (config or {}).get("contacts") or {}
        contacts: Dict[str, str] = {}
        if isinstance(contacts_raw, dict):
            for name, number in contacts_raw.items():
                if name and number:
                    contacts[str(name)] = str(number)
        transport = str(sip.get("transport", DEFAULT_TRANSPORT) or "UDP").upper()
        if transport not in {"UDP", "TCP"}:
            transport = "UDP"
        return cls(
            enabled=bool(sip.get("enabled", False)),
            server=str(sip.get("server", "")).strip(),
            port=int(sip.get("port", DEFAULT_PORT) or DEFAULT_PORT),
            username=str(sip.get("username", "")).strip(),
            password=str(sip.get("password", "") or ""),
            domain=str(sip.get("domain") or sip.get("server") or "").strip(),
            transport=transport,
            display_name=str(sip.get("display_name", "Jarvis")).strip() or "Jarvis",
            user_agent=str(sip.get("user_agent", "Jarvis-SIP/1.0")).strip()
            or "Jarvis-SIP/1.0",
            register_expires=int(
                sip.get("register_expires", DEFAULT_REGISTER_EXPIRES)
                or DEFAULT_REGISTER_EXPIRES
            ),
            contacts=contacts,
        )


@dataclass
class CallState:
    """Mutabler Zustand eines (ausgehenden) Anrufs."""

    call_id: str
    target: str
    contact_name: Optional[str]
    state: str  # init | trying | ringing | connected | ended | failed
    started_at: float
    ended_at: Optional[float] = None
    error: Optional[str] = None
    from_tag: str = ""
    to_tag: str = ""
    branch: str = ""
    cseq: int = 1
    remote_contact: Optional[str] = None


# ── SIP-Hilfsfunktionen (rein, gut testbar) ───────────────────────────────


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _gen_branch() -> str:
    return "z9hG4bK" + uuid.uuid4().hex[:16]


def _gen_tag() -> str:
    return uuid.uuid4().hex[:10]


def _gen_call_id(host: str) -> str:
    return f"{uuid.uuid4().hex}@{host or 'jarvis'}"


def _parse_status_line(line: str) -> Tuple[int, str]:
    """Parst ``SIP/2.0 200 OK`` → (200, "OK")."""

    parts = line.split(" ", 2)
    if len(parts) < 2:
        return 0, ""
    try:
        status = int(parts[1])
    except ValueError:
        return 0, ""
    reason = parts[2] if len(parts) > 2 else ""
    return status, reason


def parse_headers(raw: str) -> Tuple[Dict[str, str], str]:
    """Spaltet eine SIP-Nachricht in (lowercase-Headers, Body)."""

    head, _, body = raw.partition("\r\n\r\n")
    headers: Dict[str, str] = {}
    lines = head.split("\r\n")
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        k = key.strip().lower()
        v = value.strip()
        if k in headers:
            headers[k] = headers[k] + ", " + v
        else:
            headers[k] = v
    return headers, body


def parse_digest_challenge(www_authenticate: str) -> Dict[str, str]:
    """Parst ``Digest realm="x", nonce="y", ...`` zu einem Dict."""

    scheme, _, rest = www_authenticate.partition(" ")
    out: Dict[str, str] = {"scheme": scheme.strip()}
    for match in re.finditer(r'(\w+)\s*=\s*("([^"]*)"|([^,\s]+))', rest):
        key = match.group(1).lower()
        value = match.group(3) if match.group(3) is not None else match.group(4)
        out[key] = value
    return out


def build_digest_response(
    username: str,
    password: str,
    realm: str,
    nonce: str,
    method: str,
    uri: str,
    qop: Optional[str] = None,
    nc: str = "00000001",
    cnonce: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Berechnet die Digest ``response`` (RFC 2617, MD5)."""

    ha1 = _md5(f"{username}:{realm}:{password}")
    ha2 = _md5(f"{method}:{uri}")
    if qop:
        cnonce = cnonce or uuid.uuid4().hex[:16]
        response = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
        return response, cnonce
    return _md5(f"{ha1}:{nonce}:{ha2}"), None


def build_authorization_header(
    challenge: Dict[str, str],
    username: str,
    password: str,
    method: str,
    uri: str,
) -> str:
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    algorithm = challenge.get("algorithm", "MD5")
    qop: Optional[str] = None
    if "qop" in challenge:
        options = [q.strip() for q in challenge["qop"].split(",") if q.strip()]
        if "auth" in options:
            qop = "auth"
    nc = "00000001"
    response, cnonce = build_digest_response(
        username, password, realm, nonce, method, uri, qop=qop, nc=nc
    )
    parts = [
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        f'response="{response}"',
        f"algorithm={algorithm}",
    ]
    if "opaque" in challenge:
        parts.append(f'opaque="{challenge["opaque"]}"')
    if qop:
        parts.append(f"qop={qop}")
        parts.append(f"nc={nc}")
        parts.append(f'cnonce="{cnonce}"')
    return "Digest " + ", ".join(parts)


def normalize_number(value: str) -> str:
    """Trimmt und entfernt typische Formatierungszeichen aus einer Nummer."""

    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("sip:"):
        return raw
    return re.sub(r"[\s\-()\.]+", "", raw)


def lookup_contact(
    name: str, contacts: Dict[str, str]
) -> Optional[Tuple[str, str]]:
    """Sucht einen Kontakt case-insensitive mit Fuzzy-Fallback.

    Gibt ``(angezeigter_name, nummer)`` zurück oder ``None``.
    """

    if not name or not contacts:
        return None
    target = name.strip().lower()
    if not target:
        return None
    lower_map = {key.lower(): key for key in contacts.keys()}
    if target in lower_map:
        original = lower_map[target]
        return original, contacts[original]
    candidates = get_close_matches(target, list(lower_map.keys()), n=1, cutoff=0.7)
    if candidates:
        original = lower_map[candidates[0]]
        return original, contacts[original]
    # substring fallback (e.g. "mam" → "Mama")
    for lower_key, original in lower_map.items():
        if target in lower_key or lower_key in target:
            return original, contacts[original]
    return None


# ── Asyncio-Datagramm-Protokoll ───────────────────────────────────────────


class _SIPDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_message: Callable[[str, Tuple[str, int]], None]) -> None:
        self._on_message = on_message
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # type: ignore[override]
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            return
        try:
            self._on_message(text, addr)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[sip] Fehler beim Verarbeiten der Nachricht: {exc}", flush=True)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        print(f"[sip] Transportfehler: {exc}", flush=True)


# ── SIP-Client ────────────────────────────────────────────────────────────


HistoryCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class SIPClient:
    """Minimaler asynchroner SIP-User-Agent (UAC) für ausgehende Anrufe."""

    def __init__(
        self,
        config: SIPConfig,
        on_history: Optional[HistoryCallback] = None,
    ) -> None:
        self.config = config
        self._on_history = on_history
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[_SIPDatagramProtocol] = None
        self._local_host: str = ""
        self._local_port: int = 0
        self._media_port: int = 0
        self._media_sock: Optional[socket.socket] = None
        self._registered: bool = False
        self._call_lock = asyncio.Lock()
        self._current_call: Optional[CallState] = None
        self._response_queues: Dict[str, asyncio.Queue] = {}
        self._closed = False

    # Eigenschaften ────────────────────────────────────────────────────

    @property
    def registered(self) -> bool:
        return self._registered

    @property
    def current_call(self) -> Optional[CallState]:
        return self._current_call

    # Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self.config.enabled:
            return
        if not self.config.server or not self.config.username:
            raise RuntimeError(
                "SIP-Config unvollstaendig (server/username fehlen)."
            )
        if self.config.transport != "UDP":
            print(
                f"[sip] WARNUNG: transport={self.config.transport} nicht "
                "implementiert, faellt auf UDP zurueck.",
                flush=True,
            )
        loop = asyncio.get_running_loop()
        self._local_host = self._guess_local_ip()
        self._media_port = self._reserve_media_port()
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: _SIPDatagramProtocol(self._on_message),
            local_addr=(self._local_host, 0),
        )
        sockname = self._transport.get_extra_info("sockname")
        self._local_port = int(sockname[1]) if sockname else 0
        print(
            f"[sip] gestartet: local={self._local_host}:{self._local_port} "
            f"server={self.config.server}:{self.config.port} "
            f"user={self.config.username} media_port={self._media_port}",
            flush=True,
        )
        try:
            ok = await self.register()
            if ok:
                print(
                    f"[sip] REGISTER ok (expires={self.config.register_expires}s)",
                    flush=True,
                )
            else:
                print("[sip] REGISTER fehlgeschlagen (Anrufe ggf. trotzdem moeglich)", flush=True)
        except Exception as exc:
            print(f"[sip] REGISTER-Fehler: {exc}", flush=True)

    async def stop(self) -> None:
        self._closed = True
        try:
            if self._current_call and self._current_call.state in {
                "trying", "ringing", "connected",
            }:
                try:
                    await self.hangup()
                except Exception:
                    pass
            if self._registered:
                try:
                    await self.register(expires=0)
                except Exception:
                    pass
        finally:
            if self._transport is not None:
                try:
                    self._transport.close()
                except Exception:
                    pass
            self._transport = None
            self._protocol = None
            if self._media_sock is not None:
                try:
                    self._media_sock.close()
                except Exception:
                    pass
                self._media_sock = None

    # Public API ───────────────────────────────────────────────────────

    async def register(self, expires: Optional[int] = None) -> bool:
        if self._transport is None:
            raise RuntimeError("SIP-Transport nicht initialisiert.")
        exp = self.config.register_expires if expires is None else int(expires)
        domain = self.config.domain or self.config.server
        target_uri = f"sip:{domain}"
        call_id = _gen_call_id(self._local_host)
        from_tag = _gen_tag()
        branch = _gen_branch()
        cseq = 1
        msg = self._build_request(
            "REGISTER",
            target_uri,
            call_id=call_id,
            from_tag=from_tag,
            to_tag="",
            cseq=cseq,
            branch=branch,
            extra_headers=[f"Expires: {exp}"],
        )
        self._send(msg)
        resp = await self._await_response(
            call_id, "REGISTER", timeout=REGISTER_TIMEOUT_SECONDS
        )
        if not resp:
            return False
        if resp["status"] in (401, 407):
            header_name = (
                "www-authenticate" if resp["status"] == 401 else "proxy-authenticate"
            )
            challenge = parse_digest_challenge(resp["headers"].get(header_name, ""))
            authorization = build_authorization_header(
                challenge,
                self.config.username,
                self.config.password,
                "REGISTER",
                target_uri,
            )
            auth_field = (
                "Authorization" if resp["status"] == 401 else "Proxy-Authorization"
            )
            cseq += 1
            branch = _gen_branch()
            msg2 = self._build_request(
                "REGISTER",
                target_uri,
                call_id=call_id,
                from_tag=from_tag,
                to_tag="",
                cseq=cseq,
                branch=branch,
                extra_headers=[f"Expires: {exp}", f"{auth_field}: {authorization}"],
            )
            self._send(msg2)
            resp = await self._await_response(
                call_id, "REGISTER", timeout=REGISTER_TIMEOUT_SECONDS
            )
            if not resp:
                return False
        ok = 200 <= resp["status"] < 300
        self._registered = ok and exp > 0
        return ok

    async def make_call(
        self,
        contact_name: str = "",
        phone_number: str = "",
    ) -> Dict[str, Any]:
        """Baut einen ausgehenden Anruf auf.

        Mindestens ``contact_name`` (Lookup in ``config.contacts``) oder
        ``phone_number`` (direkte Nummer/SIP-URI) muss gesetzt sein.
        """

        async with self._call_lock:
            if self._current_call and self._current_call.state in {
                "init", "trying", "ringing", "connected",
            }:
                return {
                    "ok": False,
                    "error": (
                        f"Es laeuft bereits ein Anruf ({self._current_call.state})."
                    ),
                }

            resolved_name = ""
            number = normalize_number(phone_number)
            if contact_name and not number:
                hit = lookup_contact(contact_name, self.config.contacts)
                if hit:
                    resolved_name, number = hit[0], normalize_number(hit[1])
                else:
                    available = ", ".join(self.config.contacts.keys()) or "keine"
                    return {
                        "ok": False,
                        "error": (
                            f"Kontakt '{contact_name}' nicht gefunden. "
                            f"Verfuegbar: {available}."
                        ),
                    }
            elif contact_name:
                resolved_name = contact_name
            if not number:
                return {"ok": False, "error": "Keine Telefonnummer angegeben."}
            if self._transport is None:
                return {"ok": False, "error": "SIP nicht initialisiert."}

            domain = self.config.domain or self.config.server
            if number.lower().startswith("sip:"):
                target_uri = number
            else:
                target_uri = f"sip:{number}@{domain}"

            call_id = _gen_call_id(self._local_host)
            from_tag = _gen_tag()
            branch = _gen_branch()
            cseq = 1
            state = CallState(
                call_id=call_id,
                target=target_uri,
                contact_name=resolved_name or contact_name or None,
                state="init",
                started_at=time.time(),
                from_tag=from_tag,
                branch=branch,
                cseq=cseq,
            )
            self._current_call = state
            sdp = self._build_sdp()
            extra = ["Allow: INVITE, ACK, CANCEL, BYE, OPTIONS"]
            self._send(
                self._build_request(
                    "INVITE",
                    target_uri,
                    call_id=call_id,
                    from_tag=from_tag,
                    to_tag="",
                    cseq=cseq,
                    branch=branch,
                    extra_headers=extra,
                    body=sdp,
                    content_type="application/sdp",
                )
            )
            state.state = "trying"

            deadline = asyncio.get_event_loop().time() + INVITE_TIMEOUT_SECONDS
            final: Optional[Dict[str, Any]] = None
            while True:
                remaining = max(0.1, deadline - asyncio.get_event_loop().time())
                resp = await self._await_response(
                    call_id, "INVITE", timeout=remaining
                )
                if not resp:
                    state.state = "failed"
                    state.error = "Timeout beim Anruf-Aufbau."
                    state.ended_at = time.time()
                    await self._record_history(state)
                    self._current_call = None
                    return {"ok": False, "error": state.error}
                if resp["status"] in (401, 407):
                    header_name = (
                        "www-authenticate"
                        if resp["status"] == 401
                        else "proxy-authenticate"
                    )
                    challenge = parse_digest_challenge(
                        resp["headers"].get(header_name, "")
                    )
                    self._send_ack_for_failure(
                        target_uri, call_id, from_tag, resp["headers"], cseq, branch
                    )
                    auth_field = (
                        "Authorization"
                        if resp["status"] == 401
                        else "Proxy-Authorization"
                    )
                    authorization = build_authorization_header(
                        challenge,
                        self.config.username,
                        self.config.password,
                        "INVITE",
                        target_uri,
                    )
                    cseq += 1
                    branch = _gen_branch()
                    state.cseq = cseq
                    state.branch = branch
                    self._send(
                        self._build_request(
                            "INVITE",
                            target_uri,
                            call_id=call_id,
                            from_tag=from_tag,
                            to_tag="",
                            cseq=cseq,
                            branch=branch,
                            extra_headers=extra + [f"{auth_field}: {authorization}"],
                            body=sdp,
                            content_type="application/sdp",
                        )
                    )
                    continue
                if resp["status"] < 200:
                    continue  # provisional (100/180/183) – weiter warten
                final = resp
                break

            assert final is not None
            if 200 <= final["status"] < 300:
                to_header = final["headers"].get("to", "")
                match = re.search(r"tag=([^;>\s]+)", to_header)
                if match:
                    state.to_tag = match.group(1)
                state.remote_contact = final["headers"].get("contact", "")
                self._send_ack(
                    target_uri, call_id, from_tag, state.to_tag, cseq, _gen_branch()
                )
                state.state = "connected"
                return {
                    "ok": True,
                    "state": state.state,
                    "target": target_uri,
                    "contact_name": state.contact_name,
                    "call_id": call_id,
                }

            state.state = "failed"
            state.error = f"{final['status']} {final['reason']}"
            state.ended_at = time.time()
            self._send_ack_for_failure(
                target_uri, call_id, from_tag, final["headers"], cseq, branch
            )
            await self._record_history(state)
            self._current_call = None
            return {"ok": False, "error": state.error}

    async def hangup(self) -> Dict[str, Any]:
        state = self._current_call
        if state is None:
            return {"ok": False, "error": "Kein aktiver Anruf."}
        if state.state in {"ended", "failed"}:
            self._current_call = None
            return {"ok": False, "error": "Anruf bereits beendet."}
        if state.state in {"init", "trying", "ringing"}:
            self._send_cancel(state)
        else:
            self._send_bye(state)
        state.state = "ended"
        state.ended_at = time.time()
        await self._record_history(state)
        self._current_call = None
        return {"ok": True, "state": "ended", "call_id": state.call_id}

    def get_status(self) -> Dict[str, Any]:
        st = self._current_call
        return {
            "enabled": self.config.enabled,
            "registered": self._registered,
            "server": self.config.server,
            "active_call": (
                None
                if st is None
                else {
                    "call_id": st.call_id,
                    "target": st.target,
                    "contact_name": st.contact_name,
                    "state": st.state,
                    "started_at": st.started_at,
                }
            ),
            "contacts": list(self.config.contacts.keys()),
        }

    # Interne Helfer ───────────────────────────────────────────────────

    def _guess_local_ip(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((self.config.server, self.config.port))
            return sock.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            sock.close()

    def _reserve_media_port(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self._local_host or "0.0.0.0", 0))
        except Exception:
            sock.close()
            return 0
        self._media_sock = sock
        return sock.getsockname()[1]

    def _send(self, message: str) -> None:
        if self._transport is None:
            raise RuntimeError("SIP-Transport nicht initialisiert.")
        self._transport.sendto(
            message.encode("utf-8"), (self.config.server, self.config.port)
        )

    def _build_request(
        self,
        method: str,
        target_uri: str,
        *,
        call_id: str,
        from_tag: str,
        to_tag: str,
        cseq: int,
        branch: str,
        extra_headers: Optional[List[str]] = None,
        body: str = "",
        content_type: str = "",
    ) -> str:
        domain = self.config.domain or self.config.server
        username = self.config.username
        display = self.config.display_name
        from_uri = f"sip:{username}@{domain}"
        via = (
            f"SIP/2.0/{self.config.transport} "
            f"{self._local_host}:{self._local_port};rport;branch={branch}"
        )
        contact = (
            f"<sip:{username}@{self._local_host}:{self._local_port};"
            f"transport={self.config.transport.lower()}>"
        )
        to_tag_part = f";tag={to_tag}" if to_tag else ""
        lines = [
            f"{method} {target_uri} {SIP_VERSION}",
            f"Via: {via}",
            "Max-Forwards: 70",
            f'From: "{display}" <{from_uri}>;tag={from_tag}',
            f"To: <{target_uri}>{to_tag_part}",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} {method}",
            f"Contact: {contact}",
            f"User-Agent: {self.config.user_agent}",
        ]
        if extra_headers:
            lines.extend(extra_headers)
        if body:
            lines.append(f"Content-Type: {content_type or 'application/sdp'}")
            lines.append(f"Content-Length: {len(body.encode('utf-8'))}")
        else:
            lines.append("Content-Length: 0")
        return "\r\n".join(lines) + "\r\n\r\n" + body

    def _build_sdp(self) -> str:
        session_id = int(time.time())
        return (
            "v=0\r\n"
            f"o=- {session_id} {session_id} IN IP4 {self._local_host}\r\n"
            "s=Jarvis\r\n"
            f"c=IN IP4 {self._local_host}\r\n"
            "t=0 0\r\n"
            f"m=audio {self._media_port} RTP/AVP 0 8\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
            "a=sendrecv\r\n"
        )

    def _send_ack(
        self,
        target_uri: str,
        call_id: str,
        from_tag: str,
        to_tag: str,
        cseq: int,
        branch: str,
    ) -> None:
        domain = self.config.domain or self.config.server
        lines = [
            f"ACK {target_uri} {SIP_VERSION}",
            (
                f"Via: SIP/2.0/{self.config.transport} "
                f"{self._local_host}:{self._local_port};rport;branch={branch}"
            ),
            "Max-Forwards: 70",
            (
                f'From: "{self.config.display_name}" '
                f"<sip:{self.config.username}@{domain}>;tag={from_tag}"
            ),
            f"To: <{target_uri}>" + (f";tag={to_tag}" if to_tag else ""),
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} ACK",
            f"User-Agent: {self.config.user_agent}",
            "Content-Length: 0",
        ]
        try:
            self._send("\r\n".join(lines) + "\r\n\r\n")
        except Exception:
            pass

    def _send_ack_for_failure(
        self,
        target_uri: str,
        call_id: str,
        from_tag: str,
        resp_headers: Dict[str, str],
        cseq: int,
        branch: str,
    ) -> None:
        to_header = resp_headers.get("to", "")
        match = re.search(r"tag=([^;>\s]+)", to_header)
        to_tag = match.group(1) if match else ""
        self._send_ack(target_uri, call_id, from_tag, to_tag, cseq, branch)

    def _send_bye(self, state: CallState) -> None:
        domain = self.config.domain or self.config.server
        cseq = state.cseq + 1
        state.cseq = cseq
        branch = _gen_branch()
        lines = [
            f"BYE {state.target} {SIP_VERSION}",
            (
                f"Via: SIP/2.0/{self.config.transport} "
                f"{self._local_host}:{self._local_port};rport;branch={branch}"
            ),
            "Max-Forwards: 70",
            (
                f'From: "{self.config.display_name}" '
                f"<sip:{self.config.username}@{domain}>;tag={state.from_tag}"
            ),
            f"To: <{state.target}>"
            + (f";tag={state.to_tag}" if state.to_tag else ""),
            f"Call-ID: {state.call_id}",
            f"CSeq: {cseq} BYE",
            f"User-Agent: {self.config.user_agent}",
            "Content-Length: 0",
        ]
        try:
            self._send("\r\n".join(lines) + "\r\n\r\n")
        except Exception:
            pass

    def _send_cancel(self, state: CallState) -> None:
        domain = self.config.domain or self.config.server
        lines = [
            f"CANCEL {state.target} {SIP_VERSION}",
            (
                f"Via: SIP/2.0/{self.config.transport} "
                f"{self._local_host}:{self._local_port};rport;branch={state.branch}"
            ),
            "Max-Forwards: 70",
            (
                f'From: "{self.config.display_name}" '
                f"<sip:{self.config.username}@{domain}>;tag={state.from_tag}"
            ),
            f"To: <{state.target}>",
            f"Call-ID: {state.call_id}",
            f"CSeq: {state.cseq} CANCEL",
            f"User-Agent: {self.config.user_agent}",
            "Content-Length: 0",
        ]
        try:
            self._send("\r\n".join(lines) + "\r\n\r\n")
        except Exception:
            pass

    async def _record_history(self, state: CallState) -> None:
        if self._on_history is None:
            return
        duration: Optional[float] = None
        if state.ended_at and state.started_at:
            duration = max(0.0, state.ended_at - state.started_at)
        record = {
            "call_id": state.call_id,
            "contact_name": state.contact_name or "",
            "target": state.target,
            "state": state.state,
            "error": state.error or "",
            "started_at": state.started_at,
            "ended_at": state.ended_at,
            "duration_seconds": duration,
        }
        try:
            await self._on_history(record)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[sip] Anruf-Log fehlgeschlagen: {exc}", flush=True)

    # Eingehende Nachrichten ───────────────────────────────────────────

    def _on_message(self, text: str, addr: Tuple[str, int]) -> None:
        if "\r\n" not in text:
            return
        first_line = text.split("\r\n", 1)[0]
        headers, body = parse_headers(text)
        cseq_header = headers.get("cseq", "")
        cseq_parts = cseq_header.split()
        cseq_method = cseq_parts[1].upper() if len(cseq_parts) >= 2 else ""
        call_id = headers.get("call-id", "")
        if first_line.startswith("SIP/2.0"):
            status, reason = _parse_status_line(first_line)
            queue = self._response_queues.get(f"{call_id}|{cseq_method}")
            if queue is not None:
                try:
                    queue.put_nowait(
                        {
                            "status": status,
                            "reason": reason,
                            "headers": headers,
                            "body": body,
                        }
                    )
                except Exception:
                    pass
            call = self._current_call
            if call and call.call_id == call_id and cseq_method == "INVITE":
                if status == 100:
                    call.state = "trying"
                elif status in (180, 183):
                    call.state = "ringing"
                elif 200 <= status < 300:
                    call.state = "connected"
                elif status >= 300:
                    call.state = "failed"
                    call.error = f"{status} {reason}"
        else:
            self._handle_incoming_request(first_line, headers)

    def _handle_incoming_request(
        self, request_line: str, headers: Dict[str, str]
    ) -> None:
        parts = request_line.split(" ", 2)
        if len(parts) < 3:
            return
        method = parts[0].upper()
        call_id = headers.get("call-id", "")
        if method == "BYE" and self._current_call and self._current_call.call_id == call_id:
            self._respond_to(headers, 200, "OK")
            self._current_call.state = "ended"
            self._current_call.ended_at = time.time()
            asyncio.create_task(self._record_history(self._current_call))
            self._current_call = None
        elif method == "OPTIONS":
            self._respond_to(headers, 200, "OK")
        elif method == "INVITE":
            # Eingehende Anrufe sind Phase 2 — vorerst freundlich ablehnen.
            self._respond_to(headers, 486, "Busy Here")
        else:
            self._respond_to(headers, 200, "OK")

    def _respond_to(
        self, request_headers: Dict[str, str], status: int, reason: str
    ) -> None:
        via = request_headers.get("via", "")
        from_ = request_headers.get("from", "")
        to = request_headers.get("to", "")
        call_id = request_headers.get("call-id", "")
        cseq = request_headers.get("cseq", "")
        msg = (
            f"SIP/2.0 {status} {reason}\r\n"
            f"Via: {via}\r\n"
            f"From: {from_}\r\n"
            f"To: {to}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq}\r\n"
            f"User-Agent: {self.config.user_agent}\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        try:
            self._send(msg)
        except Exception:
            pass

    async def _await_response(
        self, call_id: str, method: str, timeout: float
    ) -> Optional[Dict[str, Any]]:
        key = f"{call_id}|{method}"
        queue = self._response_queues.setdefault(key, asyncio.Queue())
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
