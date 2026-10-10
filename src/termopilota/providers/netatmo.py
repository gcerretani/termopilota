# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Provider termostati Netatmo per BTicino Smarther with Netatmo.

App: Home + Control (Legrand/Netatmo/BTicino) — account Netatmo
Registrazione app: https://dev.netatmo.com
Scopes necessari: read_smarther write_smarther; read_station per la stazione meteo
(modulo esterno: temperatura esterna misurata). Gli scope non si impostano su
dev.netatmo.com: li chiede l'URL di autorizzazione, quindi aggiungerne uno
richiede di ricollegare l'account.

Modalita' di una stanza (setroomthermpoint): 'manual' (setpoint fisso fino a
endtime), 'max' (massimo fino a endtime), 'home' (segue la modalita' della
casa, cioe' il programma). Modalita' della casa (setthermmode): 'schedule',
'away', 'hg' (antigelo).
"""

import hashlib
import hmac
import time
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from termopilota.providers import ThermostatProvider, aggiorna_config_atomico, chiamata, register_thermostat
from termopilota.percorsi import CONFIG_FILE

logger = logging.getLogger(__name__)

NETATMO_AUTH_URL = "https://api.netatmo.com/oauth2/authorize"
NETATMO_TOKEN_URL = "https://api.netatmo.com/oauth2/token"
NETATMO_BASE = "https://api.netatmo.com/api"

SCOPE = "read_smarther write_smarther read_station"
SCOPE_STAZIONE = "read_station"
TIPO_MODULO_ESTERNO = "NAModule1"    # modulo esterno della stazione meteo

MODALITA_STANZA = ("manual", "max", "home")


class ErroreNetatmo(Exception):
    """Comando rifiutato da Netatmo, con il messaggio e il codice della risposta."""

    def __init__(self, messaggio: str, codice=None):
        super().__init__(f"{messaggio} (codice {codice})" if codice is not None else messaggio)
        self.messaggio = messaggio
        self.codice = codice


MODALITA_CASA = ("schedule", "away", "hg")
DURATA_MANUALE_DEFAULT_S = 12 * 3600
TIMEOUT_S = 15                   # durante i disservizi Netatmo risponde lento, ma risponde
DATI_CASA_TTL_S = 900            # homesdata (stanze, moduli, programmi) cambia di rado

# homesdata condiviso tra i client (se ne crea uno per richiesta): il polling e le
# pagine chiedono cosi' solo homestatus. Si rinnova dopo un cambio di programma.
_dati_casa: dict = {"homes": None, "ts": 0.0}


def invalida_dati_casa() -> None:
    _dati_casa.update(homes=None, ts=0.0)


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
            "scope": SCOPE,
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

    def ha_scope(self, scope: str) -> bool:
        """True se il token concesso include `scope` (Netatmo lo restituisce come lista)."""
        concessi = self._token.get("scope") or []
        if isinstance(concessi, str):
            concessi = concessi.split()
        return scope in concessi

    # ── Lettura ───────────────────────────────────────────────────────────────

    def _homesdata(self, forza: bool = False) -> list:
        if (not forza and _dati_casa["homes"] is not None
                and time.time() - _dati_casa["ts"] < DATI_CASA_TTL_S):
            return _dati_casa["homes"]
        resp = chiamata("netatmo", "get", f"{NETATMO_BASE}/homesdata",
                        headers=self._headers(), timeout=TIMEOUT_S)
        resp.raise_for_status()
        homes = resp.json().get("body", {}).get("homes", [])
        _dati_casa.update(homes=homes, ts=time.time())
        return homes

    def _homestatus(self, home_id: str) -> dict:
        """Corpo di homestatus: {'home': {...}, 'errors': [{'code', 'id'}]}. I moduli
        in `errors` (es. codice 6, non raggiungibile) mancano da home.rooms/modules."""
        resp = chiamata(
            "netatmo", "get",
            f"{NETATMO_BASE}/homestatus",
            headers=self._headers(),
            params={"home_id": home_id},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json().get("body", {})

    def lista_impianti(self) -> list:
        return [{"id": h["id"], "name": h.get("name", "Casa")} for h in self._homesdata(forza=True)]

    def lista_moduli(self, home_id: str) -> list:
        home = next((h for h in self._homesdata(forza=True) if h["id"] == home_id), None)
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
        rooms = self._homestatus(home_id).get("home", {}).get("rooms", [])
        return {r["id"]: normalizza_stanza(r) for r in rooms}

    def stato_casa(self, home_id: str) -> dict:
        """Dati completi della casa: {'dati': homesdata della casa, 'stato': homestatus,
        'errori': moduli che homestatus segnala in errore}."""
        dati = next((h for h in self._homesdata() if h.get("id") == home_id), {})
        corpo = self._homestatus(home_id)
        return {"dati": dati, "stato": corpo.get("home", {}), "errori": corpo.get("errors", []) or []}

    def stato_stazioni(self) -> list:
        """Moduli esterni delle stazioni meteo (getstationsdata), normalizzati.

        Senza lo scope read_station (token autorizzato prima che servisse) non
        chiama Netatmo e restituisce una lista vuota."""
        if not self.ha_scope(SCOPE_STAZIONE):
            return []
        resp = chiamata(
            "netatmo", "get",
            f"{NETATMO_BASE}/getstationsdata",
            headers=self._headers(),
            params={"get_favorites": "false"},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        moduli = []
        for stazione in resp.json().get("body", {}).get("devices", []) or []:
            for modulo in stazione.get("modules", []) or []:
                if modulo.get("type") == TIPO_MODULO_ESTERNO:
                    moduli.append(normalizza_modulo_esterno(modulo, stazione))
        return moduli

    # ── Comandi ───────────────────────────────────────────────────────────────

    def _post(self, endpoint: str, *, data: Optional[dict] = None, json: Optional[dict] = None) -> bool:
        """POST di un comando; su errore solleva ErroreNetatmo con il messaggio di Netatmo."""
        resp = chiamata(
            "netatmo", "post",
            f"{NETATMO_BASE}/{endpoint}",
            headers=self._headers(),
            data=data,
            json=json,
            timeout=TIMEOUT_S,
        )
        if resp.status_code in (200, 204):
            return True
        logger.error("Errore Netatmo %s %s: %s %s", endpoint, data or json, resp.status_code, resp.text)
        try:
            errore = resp.json().get("error") or {}
        except ValueError:
            errore = {}
        if not isinstance(errore, dict):
            errore = {"message": str(errore)}
        raise ErroreNetatmo(errore.get("message") or f"HTTP {resp.status_code}", errore.get("code"))

    def imposta_modalita(self, home_id: str, room_id: str, mode: str, setpoint: float = 7.0,
                         fine: Optional[int] = None) -> bool:
        """Modalita' di una stanza. mode: 'OFF' o 'manual' (setpoint fino a `fine`,
        epoch), 'max', 'AUTOMATIC' o 'home' (torna al programma).

        Usa `setstate`: con gli scope Smarther (`write_smarther`) setroomthermpoint
        risponde 403 (codice 13), setstate invece e' ammesso."""
        mode = {"OFF": "manual", "AUTOMATIC": "home"}.get(mode, mode)
        if mode not in MODALITA_STANZA:
            raise ValueError(f"Modalita' stanza non valida: {mode}")
        stanza = {"id": room_id, "therm_setpoint_mode": mode}
        if mode in ("manual", "max"):
            stanza["therm_setpoint_end_time"] = int(fine or time.time() + DURATA_MANUALE_DEFAULT_S)
        if mode == "manual":
            stanza["therm_setpoint_temperature"] = setpoint
        return self._post("setstate", json={"home": {"id": home_id, "rooms": [stanza]}})

    def imposta_modalita_casa(self, home_id: str, mode: str, fine: Optional[int] = None) -> bool:
        """Modalita' della casa: 'schedule', 'away' o 'hg' (fino a `fine`, se indicato)."""
        if mode not in MODALITA_CASA:
            raise ValueError(f"Modalita' casa non valida: {mode}")
        parametri = {"home_id": home_id, "mode": mode}
        if fine and mode != "schedule":
            parametri["endtime"] = int(fine)
        return self._post("setthermmode", data=parametri)

    def cambia_programma(self, home_id: str, schedule_id: str) -> bool:
        try:
            return self._post("switchhomeschedule", data={"home_id": home_id, "schedule_id": schedule_id})
        finally:
            invalida_dati_casa()      # il programma selezionato sta in homesdata

    # ── Webhook ───────────────────────────────────────────────────────────────

    def registra_webhook(self, url: str) -> bool:
        """Netatmo inviera' gli eventi (set_point, therm_mode, ...) a `url`."""
        return self._post("addwebhook", data={"url": url})

    def rimuovi_webhook(self) -> bool:
        return self._post("dropwebhook", data={})


def firma_webhook_valida(client_secret: str, corpo: bytes, firma: Optional[str]) -> bool:
    """Header X-Netatmo-secret: HMAC-SHA256 esadecimale del corpo con il client secret."""
    if not client_secret or not firma:
        return False
    attesa = hmac.new(client_secret.encode("utf-8"), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(attesa, firma.strip().lower())


def descrivi_errore_modulo(codice) -> str:
    """Messaggio per un errore di modulo di homestatus (`errors`)."""
    if codice == 6:
        return "Termostato non raggiungibile da Netatmo (errore 6): controlla alimentazione e Wi-Fi"
    return f"Netatmo segnala un errore sul termostato (codice {codice})"


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


def normalizza_modulo_esterno(modulo: dict, stazione: dict) -> dict:
    """Modulo esterno di getstationsdata nei campi usati da TermoPilota.

    `ts` e' l'ora della misura (epoch, da dashboard_data.time_utc): un modulo
    non raggiungibile non ha dashboard_data, quindi temperatura e ora sono None."""
    misure = modulo.get("dashboard_data") or {}
    return {
        "id": modulo.get("_id"),
        "nome": modulo.get("module_name") or "Modulo esterno",
        "stazione": stazione.get("station_name") or stazione.get("home_name") or "Stazione meteo",
        "stazione_id": stazione.get("_id"),
        "temperatura": _num(misure.get("Temperature")),
        "umidita": _num(misure.get("Humidity")),
        "minima": _num(misure.get("min_temp")),
        "massima": _num(misure.get("max_temp")),
        "tendenza": misure.get("temp_trend"),
        "ts": misure.get("time_utc"),
        "batteria_pct": modulo.get("battery_percent"),
        "segnale_radio": modulo.get("rf_status"),
        "firmware": modulo.get("firmware"),
        "raggiungibile": modulo.get("reachable", True) is not False and bool(misure),
        "_campi": sorted(misure.keys()),
        "grezzo": modulo,
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
