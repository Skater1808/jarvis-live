# Jarvis SIP/VoIP Integration

Jarvis kann Telefonanrufe über SIP/VoIP tätigen. Die komplette
SIP-Logik ist in [`sip_client.py`](../sip_client.py) als eigenständiger
Pure-Python-Stack implementiert – **keine** Abhängigkeit zu `pjsip` /
`python-sipsimple`.

## TL;DR

1. `config.json` um `sip` und `contacts` ergänzen (siehe
   `config.example.json`).
2. `"sip": { "enabled": true, ... }` setzen.
3. Server starten – Jarvis registriert sich beim SIP-Server.
4. „Jarvis, rufe Mama an" → Anruf läuft.

## Konfiguration

```json
{
  "sip": {
    "enabled": true,
    "server": "sip.example.com",
    "port": 5060,
    "username": "jarvis",
    "password": "REPLACE_WITH_SIP_PASSWORD",
    "domain": "sip.example.com",
    "transport": "udp",
    "display_name": "Jarvis",
    "user_agent": "Jarvis-SIP/1.0",
    "register_expires": 600
  },
  "contacts": {
    "Mama": "+49123456789",
    "Papa": "+49123456790",
    "Arbeit": "+49123456791"
  }
}
```

### Felder

| Feld                 | Bedeutung                                                |
|----------------------|----------------------------------------------------------|
| `enabled`            | Master-Schalter. Wenn `false`, ist die ganze Integration aus. |
| `server`             | SIP-Provider-Hostname (z. B. `sipgate.de`, lokaler Asterisk). |
| `port`               | SIP-Port, default `5060`.                                |
| `username`/`password`| SIP-Account beim Provider.                               |
| `domain`             | SIP-Realm/Domain. Default = `server`.                    |
| `transport`          | `udp` (empfohlen) oder `tcp`. Aktuell wird nur UDP signalisiert. |
| `display_name`       | Wie Jarvis sich beim Angerufenen meldet.                 |
| `user_agent`         | `User-Agent`-Header in SIP-Nachrichten.                  |
| `register_expires`   | Lifetime der REGISTER-Registrierung in Sekunden.         |

### Kontakte

`contacts` ist eine flache `{ Name: Nummer }`-Map. Namen werden
case-insensitive und mit Fuzzy-Match (`difflib.get_close_matches`)
aufgelöst, dazu Substring-Fallback – „Mam" findet also „Mama".

Nummern dürfen `+`, Bindestriche, Leerzeichen und Klammern enthalten;
das wird beim Anruf normalisiert. Wer direkt eine SIP-URI hinterlegen
will, kann auch `"Alice": "sip:alice@example.com"` schreiben.

## Sprachbefehle

| Befehl                                | Tool               |
|---------------------------------------|--------------------|
| „Jarvis, rufe Mama an"                | `make_call`        |
| „Jarvis, ruf +49 30 12345 an"         | `make_call`        |
| „Jarvis, leg auf"                     | `hangup_call`      |
| „Jarvis, bist du eingeloggt?"         | `call_status`      |
| „Jarvis, zeige meine letzten Anrufe"  | `list_recent_calls`|

## Anruf-Historie

Jeder beendete oder fehlgeschlagene Anruf wird in `jarvis_calls.db`
(SQLite) geloggt:

```
call_id | contact_name | target | state | error | started_at | ended_at | duration_seconds | created_at
```

`state` ist eines von `connected | ended | failed`. Bei `failed` enthält
`error` den SIP-Statuscode + Reason (z. B. `486 Busy Here`) oder
„Timeout beim Anruf-Aufbau.".

## Architektur

```
                  ┌─────────────────────────────────────┐
                  │             server.py               │
                  │ FUNCTION_DECLARATIONS:              │
                  │  make_call / hangup_call /          │
                  │  call_status / list_recent_calls    │
                  └───────────────┬─────────────────────┘
                                  │ asyncio
                                  ▼
       ┌──────────────────────────────────────────────┐
       │              sip_client.py                   │
       │ ┌────────────────────────────────────────┐   │
       │ │ SIPClient (asyncio UDP)                │   │
       │ │  REGISTER  (Digest MD5, qop=auth)      │   │
       │ │  INVITE → 1xx → 2xx → ACK              │   │
       │ │  BYE / CANCEL / OPTIONS                │   │
       │ │  CallState: init/trying/ringing/...    │   │
       │ │  Kontakt-Lookup (case+fuzzy+substr)    │   │
       │ │  SDP: m=audio <port> RTP/AVP 0 8       │   │
       │ │       (PCMU/PCMA – Audio Phase 5)      │   │
       │ └────────────────────────────────────────┘   │
       │              │                               │
       │              ▼                               │
       │ ┌────────────────────────────────────────┐   │
       │ │ on_history → call_history.record_call  │   │
       │ │              SQLite (jarvis_calls.db)  │   │
       │ └────────────────────────────────────────┘   │
       └──────────────────────────────────────────────┘
                                  │
                                  ▼
                        SIP-Provider (UDP/5060)
```

## Phase-Plan

Aus dem Implementierungs-Spec:

| Phase | Inhalt                                  | Status                      |
|-------|------------------------------------------|-----------------------------|
| 1     | Anruf tätigen, Kontakte, Hangup          | **fertig**                  |
| 1     | Anruf-Status, Historie                   | **fertig**                  |
| 1     | Fehlerbehandlung (4xx/5xx, Timeout)      | **fertig**                  |
| 2     | Eingehende Anrufe                        | offen (wird mit `486 Busy Here` abgelehnt) |
| 3     | In-Call-Steuerung (Mute, Volume)         | offen                       |
| 4     | Konferenzen                              | offen                       |
| 5     | Echte RTP-Audio-Streams                  | offen (SDP ist vorbereitet) |

## Fehlerbehandlung

Der SIP-Client versucht bewusst nicht, „die Welt zu retten" – er gibt
Fehlerursachen aus dem Server-Statuscode wörtlich zurück, damit du
nachvollziehen kannst, was passiert ist.

Häufige Fälle:

| Fehler                                  | Bedeutung                                    |
|-----------------------------------------|----------------------------------------------|
| `401 Unauthorized` (nach Retry)          | Falsches Passwort.                           |
| `403 Forbidden`                          | Account gesperrt / Routing verboten.         |
| `404 Not Found`                          | Nummer existiert nicht.                      |
| `486 Busy Here`                          | Anschluss besetzt.                           |
| `Timeout beim Anruf-Aufbau.`             | Keine Antwort vom Provider (NAT? Firewall?). |
| `Kontakt 'X' nicht gefunden.`            | Name nicht in `config.contacts`.             |

## NAT / Firewall

SIP über UDP ist NAT-empfindlich. Faustregeln:

- Wenn möglich, beim Provider „NAT helper / STUN" aktivieren.
- Lokales Asterisk / FreePBX: einfacher, weil im LAN.
- Heimrouter: UDP-Port `5060` weiterleiten oder Provider-spezifischen
  Outbound-Proxy nutzen.
- Wenn dein Provider TLS/SIPS anbietet, ist das langfristig sicherer –
  dieser Client unterstützt es noch nicht (siehe Phase-Plan).

## Sicherheit

- `config.json` ist in `.gitignore` – niemals committen.
- SIP-Passwörter werden ausschließlich im Speicher gehalten; nichts
  davon landet im Log (auch nicht bei Fehlern).
- Eingehende Anrufe werden vorerst pauschal mit `486 Busy Here`
  abgelehnt – Jarvis nimmt also nichts ungewollt an.

## Manuelles Testen ohne realen Provider

Für einen schnellen Trockenlauf eignet sich ein lokaler SIP-Server wie
Asterisk oder Linphone. Wenn du zunächst nur die Logik prüfen willst,
ohne SIP-Provider:

1. `sip.enabled: false` lassen.
2. Jarvis mit „rufe Mama an" testen → Antwort sollte sinngemäß lauten
   „SIP ist deaktiviert.".

Für Unit-artige Checks der SIP-Hilfsfunktionen siehe die in
`sip_client.py` enthaltenen reinen Funktionen
(`parse_headers`, `parse_digest_challenge`, `build_digest_response`,
`normalize_number`, `lookup_contact`).
