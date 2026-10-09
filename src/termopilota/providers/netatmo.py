# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Provider termostati Netatmo per BTicino Smarther with Netatmo.

App: Home + Control (Legrand/Netatmo/BTicino) — account Netatmo
Registrazione app: https://dev.netatmo.com
Scopes necessari: read_smarther write_smarther

Modalita' di una stanza (setroomthermpoint): 'manual' (setpoint fisso fino a
endtime), 'max' (massimo fino a endtime), 'home' (segue la modalita' della
casa, cioe' il programma). Modalita' della casa (setthermmode): 'schedule',
'away', 'hg' (antigelo).
"""

import time
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from termopilota.providers import ThermostatProvider, register_thermostat, aggiorna_config_atomico
from termopilota.percorsi import CONFIG_FILE

logger = logging.getLogger(__name__)

NETATMO_AUTH_URL = "https://api.netatmo.com/oauth2/authorize"
NETATMO_TOKEN_URL = "https://api.netatmo.com/oauth2/token"
NETATMO_BASE = "https://api.netatmo.com/api"

MODALITA_STANZA = ("manual", "max", "home")
MODALITA_CASA = ("schedule", "away", "hg")
DURATA_MANUALE_DEFAULT_S = 12 * 3600


def ora_casa(timezone: Optional[str]) -> datetime:
    """Ora corrente nel fuso della casa Netatmo (naive), o ora locale se ignoto."""
    if timezone:
        try:
            return datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now()


def programma_attivo(casa: dict) -> Optional[dict]:
    """Programma di riscaldamento selezionato (i programmi 'cooling' sono esclusi)."""
    programmi = [s for s in casa.get("schedules", []) or []
                 if s.get("type", "therm") == "therm"]
    if not programmi:
        return None
    return next((s for s in programmi if s.get("selected")), programmi[0])


def setpoint_programmato(casa: dict, room_id: str, adesso: datetime) -> Optional[float]:
    """Setpoint che la stanza avrebbe seguendo la modalita' della casa.

    `casa` e' la casa di homesdata (con `schedules` e `therm_mode`), `adesso`
    l'ora locale della casa. In 'away' e 'hg' vale la temperatura del
    programma per quella modalita'; in 'schedule' quella della fascia della
    timetable in corso (m_offset = minuti dal lunedi' alle 00:00). E' il
    target vero della stanza anche quando TermoPilota l'ha messa in manuale.
    """
    programma = programma_attivo(casa)
    if programma is None:
        return None
    modo = casa.get("therm_mode") or "schedule"
    if modo == "away":
        return _num(programma.get("away_temp"))
    if modo == "hg":
        return _num(programma.get("hg_temp"))

    fasce = sorted(programma.get("timetable", []) or [], key=lambda f: f.get("m_offset", 0))
    if not fasce:
        return None
    minuti = adesso.weekday() * 1440 + adesso.hour * 60 + adesso.minute
    # Prima della prima fascia della settimana vale l'ultima della settimana precedente
    corrente = fasce[-1]
    for fascia in fasce:
        if fascia.get("m_offset", 0) <= minuti:
            corrente = fascia
    zona = next((z for z in programma.get("zones", []) or []
                 if z.get("id") == corrente.get("zone_id")), None)
    if zona is None:
        return None
    for stanza in zona.get("rooms", []) or []:
        if stanza.get("id") == room_id:
            return _num(stanza.get("therm_setpoint_temperature"))
    for stanza in zona.get("rooms_temp", []) or []:
        if stanza.get("room_id") == room_id:
            return _num(stanza.get("temp"))
    return None


def _num(valore) -> Optional[float]:
    try:
        return float(valore)
    except (TypeError, ValueError):
        return None


class NetatmoClient(ThermostatProvider):
    """Client OAuth2 per API Netatmo (termostati BTicino Smarther with Netatmo)."""

    def __init__(self, client_id: str, client_secret: str, token_data: Optional[dict] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = token_data or {}

    # ── OAuth2 ────────────────────────────────────────────────────────────────

    def url_autorizzazione(self, redirect_uri: str, state: str) -> str:
        """URL per il flusso OAuth2 Authorization Code (primo accesso).

        `state` deve essere generato dal chiamante (es. token casuale salvato
        in session) e validato nel callback per prevenire CSRF.
        """
        params = urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": "read_smarther write_smarther",
            "response_type": "code",
            "state": state,
        })
        return f"{NETATMO_AUTH_URL}?{params}"

    def scambia_codice(self, code: str, redirect_uri: str) -> dict:
        """Scambia il codice OAuth2 con access + refresh token."""
        resp = requests.post(
            NETATMO_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
        resp.raise_for_status()
        self._token = resp.json()
        self._token["_expires_at"] = time.time() + self._token.get("expires_in", 10800) - 60
        self._salva_token()
        return self._token

    def _refresh(self) -> None:
        """Rinnova l'access token usando il refresh token."""
        resp = requests.post(
            NETATMO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self._token["refresh_token"],
            },
            timeout=10,
        )
        resp.raise_for_status()
        self._token.update(resp.json())
        self._token["_expires_at"] = time.time() + self._token.get("expires_in", 10800) - 60
        self._salva_token()

    def _salva_token(self) -> None:
        aggiorna_config_atomico(CONFIG_FILE, lambda cfg: cfg.update({"legrand_token": self._token}))

    def _headers(self) -> dict:
        if not self._token:
            raise RuntimeError("Token Netatmo non presente. Esegui prima il flusso OAuth2.")
        if time.time() > self._token.get("_expires_at", 0):
            self._refresh()
        return {"Authorization": f"Bearer {self._token['access_token']}"}

    @property
    def autenticato(self) -> bool:
        return bool(self._token.get("access_token"))

    # ── Lettura ───────────────────────────────────────────────────────────────

    def _homesdata(self) -> list:
        resp = requests.get(f"{NETATMO_BASE}/homesdata", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return resp.json().get("body", {}).get("homes", [])

    def _homestatus(self, home_id: str) -> dict:
        resp = requests.get(
            f"{NETATMO_BASE}/homestatus",
            headers=self._headers(),
            params={"home_id": home_id},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("body", {}).get("home", {})

    def lista_impianti(self) -> list:
        return [{"id": h["id"], "name": h.get("name", "Casa")} for h in self._homesdata()]

    def lista_moduli(self, home_id: str) -> list:
        home = next((h for h in self._homesdata() if h["id"] == home_id), None)
        if not home:
            return []
        rooms = []
        for room in home.get("rooms", []):
            has_thermostat = any(
                m for m in home.get("modules", [])
                if m.get("room_id") == room["id"] and m.get("type") in ("NATherm1", "NRV", "OTM")
            )
            if has_thermostat or room.get("module_ids"):
                rooms.append({"id": room["id"], "name": room.get("name", f"Stanza {room['id'][:6]}")})
        return rooms

    def stato_tutte_stanze(self, home_id: str) -> dict:
        rooms = self._homestatus(home_id).get("rooms", [])
        return {r["id"]: normalizza_stanza(r) for r in rooms}

    def stato_casa(self, home_id: str) -> dict:
        """Dati completi della casa: {'dati': homesdata della casa, 'stato': homestatus}."""
        dati = next((h for h in self._homesdata() if h.get("id") == home_id), {})
        return {"dati": dati, "stato": self._homestatus(home_id)}

    # ── Comandi ───────────────────────────────────────────────────────────────

    def _post(self, endpoint: str, parametri: dict) -> bool:
        resp = requests.post(
            f"{NETATMO_BASE}/{endpoint}",
            headers=self._headers(),
            data=parametri,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            return True
        logger.error("Errore Netatmo %s %s: %s %s", endpoint, parametri, resp.status_code, resp.text)
        return False

    def imposta_modalita(self, home_id: str, room_id: str, mode: str, setpoint: float = 7.0,
                         fine: Optional[int] = None) -> bool:
        """Modalita' di una stanza. mode: 'OFF' o 'manual' (setpoint fino a `fine`,
        epoch), 'max', 'AUTOMATIC' o 'home' (torna al programma)."""
        mode = {"OFF": "manual", "AUTOMATIC": "home"}.get(mode, mode)
        if mode not in MODALITA_STANZA:
            raise ValueError(f"Modalita' stanza non valida: {mode}")
        parametri = {"home_id": home_id, "room_id": room_id, "mode": mode}
        if mode in ("manual", "max"):
            parametri["endtime"] = int(fine or time.time() + DURATA_MANUALE_DEFAULT_S)
        if mode == "manual":
            parametri["temp"] = setpoint
        return self._post("setroomthermpoint", parametri)

    def imposta_modalita_casa(self, home_id: str, mode: str, fine: Optional[int] = None) -> bool:
        """Modalita' della casa: 'schedule', 'away' o 'hg' (fino a `fine`, se indicato)."""
        if mode not in MODALITA_CASA:
            raise ValueError(f"Modalita' casa non valida: {mode}")
        parametri = {"home_id": home_id, "mode": mode}
        if fine and mode != "schedule":
            parametri["endtime"] = int(fine)
        return self._post("setthermmode", parametri)

    def cambia_programma(self, home_id: str, schedule_id: str) -> bool:
        return self._post("switchhomeschedule", {"home_id": home_id, "schedule_id": schedule_id})


def normalizza_stanza(r: dict) -> dict:
    """Stanza di homestatus nei campi usati da TermoPilota (piu' `_campi` per la diagnosi)."""
    richiesta = r.get("heating_power_request")
    return {
        "room_id": r.get("id"),
        "temperatura_attuale": r.get("therm_measured_temperature"),
        "setpoint": r.get("therm_setpoint_temperature"),
        "modalita": r.get("therm_setpoint_mode"),
        "setpoint_fine": r.get("therm_setpoint_end_time"),
        "sta_riscaldando": (richiesta or 0) > 0,
        "richiesta_calore_pct": richiesta,
        "umidita": r.get("humidity"),
        "finestra_aperta": bool(r.get("open_window")),
        "raggiungibile": r.get("reachable", True) is not False,
        "anticipo": bool(r.get("anticipating")),
        "_campi": sorted(r.keys()),
    }


# Alias
LegrandClient = NetatmoClient


def client_da_config(cfg: dict) -> Optional[NetatmoClient]:
    """Crea un NetatmoClient dai dati in config.json. Restituisce None se non configurato."""
    cid = cfg.get("legrand_client_id", "")
    csec = cfg.get("legrand_client_secret", "")
    if not cid or not csec:
        return None
    return NetatmoClient(cid, csec, token_data=cfg.get("legrand_token"))


# Auto-registrazione nel registry
register_thermostat("netatmo", client_da_config)
