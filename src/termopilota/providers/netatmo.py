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
# Moduli della stazione meteo: stanno nella casa Netatmo (in una stanza qualsiasi) ma non sono
# termostati. Solo il modulo esterno (NAModule1) e' di default la temperatura esterna.
TIPI_MODULI_METEO = ("NAMain", "NAModule1", "NAModule2", "NAModule3", "NAModule4")
TIPI_MODULI_ESTERNI = ("NAModule1",)
BATTERIA_MV = {"NAModule1": (3600, 6000)}   # tensione per 0% e 100%, se Netatmo non da' la percentuale

# Misure dei sensori: chiave Netatmo normalizzata (minuscola, senza '_': getstationsdata scrive
# 'Temperature' e 'CO2', homestatus 'temperature' e 'co2') -> (chiave, etichetta, unita', diagnostica).
# Le chiavi numeriche non elencate entrano lo stesso, con la chiave come etichetta: un modulo nuovo
# funziona senza toccare il codice.
GRANDEZZE_NETATMO = {
    "temperature": ("temperatura", "Temperatura", "°C", False),
    "humidity": ("umidita", "Umidità", "%", False),
    "co2": ("co2", "CO₂", "ppm", False),
    "noise": ("rumore", "Rumore", "dB", False),
    "pressure": ("pressione", "Pressione", "hPa", False),
    "absolutepressure": ("pressione_assoluta", "Pressione assoluta", "hPa", False),
    "mintemp": ("minima", "Minima di oggi", "°C", False),
    "maxtemp": ("massima", "Massima di oggi", "°C", False),
    "windstrength": ("vento", "Vento", "km/h", False),
    "windangle": ("direzione_vento", "Direzione del vento", "°", False),
    "guststrength": ("raffica", "Raffica", "km/h", False),
    "gustangle": ("direzione_raffica", "Direzione della raffica", "°", False),
    "maxwindstr": ("vento_massimo", "Vento massimo di oggi", "km/h", False),
    "maxwindangle": ("direzione_vento_massimo", "Direzione del vento massimo", "°", False),
    "rain": ("pioggia", "Pioggia", "mm", False),
    "sumrain1": ("pioggia_1h", "Pioggia nell'ultima ora", "mm", False),
    "sumrain24": ("pioggia_24h", "Pioggia di oggi", "mm", False),
    "batterypercent": ("batteria", "Batteria", "%", True),
    "rfstatus": ("segnale_radio", "Segnale radio", "", True),
    "rfstrength": ("segnale_radio", "Segnale radio", "", True),
    "wifistatus": ("segnale_wifi", "Segnale WiFi", "", True),
    "wifistrength": ("segnale_wifi", "Segnale WiFi", "", True),
}
# Numeri che non sono misure: date e ore (epoch), versioni, tensione della batteria
_NON_MISURE_PREFISSI = ("date", "time", "last")
_NON_MISURE = {"ts", "firmware", "firmwarerevision", "batteryvp", "batterylevel", "setupdate"}

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
            # Una stanza vale se contiene qualcosa che non e' della stazione meteo
            ha_moduli = any(
                m for m in home.get("modules", [])
                if m.get("room_id") == room["id"] and m.get("type") not in TIPI_MODULI_METEO
            )
            if ha_moduli:
                rooms.append({"id": room["id"], "name": room.get("name", f"Stanza {room['id'][:6]}")})
        return rooms

    def stato_casa(self, home_id: str) -> dict:
        """Dati completi della casa: {'dati': homesdata della casa, 'stato': homestatus,
        'errori': moduli che homestatus segnala in errore}."""
        dati = next((h for h in self._homesdata() if h.get("id") == home_id), {})
        corpo = self._homestatus(home_id)
        return {"dati": dati, "stato": corpo.get("home", {}), "errori": corpo.get("errors", []) or []}

    def stato_stazioni(self, home_id: Optional[str] = None) -> list:
        """Sensori delle stazioni meteo (base e moduli), normalizzati; `esterno` indica i moduli
        che misurano fuori casa.

        Prima getstationsdata (serve lo scope read_station: senza, non chiama Netatmo); se
        non restituisce niente si leggono quelli della casa `home_id` da homestatus, dove i
        moduli della stazione compaiono con temperatura e umidita'."""
        moduli = []
        if self.ha_scope(SCOPE_STAZIONE):
            resp = chiamata(
                "netatmo", "get",
                f"{NETATMO_BASE}/getstationsdata",
                headers=self._headers(),
                params={"get_favorites": "false"},
                timeout=TIMEOUT_S,
            )
            resp.raise_for_status()
            for stazione in resp.json().get("body", {}).get("devices", []) or []:
                for modulo in [stazione, *(stazione.get("modules", []) or [])]:
                    if modulo.get("type") in TIPI_MODULI_METEO and modulo.get("dashboard_data") is not None:
                        moduli.append(normalizza_modulo_esterno(modulo, stazione))
        if not moduli and home_id:
            moduli = self.moduli_esterni_casa(home_id)
        if home_id:
            casa = next((h for h in self._homesdata() if h.get("id") == home_id), {})
            moduli = [m | stanza_del_modulo(casa, m["id"]) for m in moduli]
        return moduli

    def moduli_esterni_casa(self, home_id: str) -> list:
        """Sensori della stazione meteo nella casa, da homestatus; i nomi da homesdata."""
        casa = next((h for h in self._homesdata() if h.get("id") == home_id), {})
        return sensori_da_casa(casa, self._homestatus(home_id).get("home", {}))

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


def _chiave_netatmo(chiave: str) -> str:
    return chiave.lower().replace("_", "")


def estrai_grandezze(*sorgenti: dict) -> list:
    """Tutte le misure numeriche di un sensore: [{chiave, etichetta, unita, valore, diagnostica}].

    Prima quelle note (nell'ordine di GRANDEZZE_NETATMO), poi le altre in ordine alfabetico;
    una chiave gia' trovata in una sorgente precedente non si ripete."""
    trovate = {}
    for sorgente in sorgenti:
        for chiave, valore in (sorgente or {}).items():
            if isinstance(valore, bool) or not isinstance(valore, (int, float)):
                continue
            norm = _chiave_netatmo(chiave)
            if norm in _NON_MISURE or norm.startswith(_NON_MISURE_PREFISSI):
                continue
            nostra, etichetta, unita, diagnostica = GRANDEZZE_NETATMO.get(norm, (norm, chiave, "", False))
            trovate.setdefault(nostra, {"chiave": nostra, "etichetta": etichetta, "unita": unita,
                                        "valore": float(valore), "diagnostica": diagnostica})
    ordine = {v[0]: i for i, v in enumerate(GRANDEZZE_NETATMO.values())}
    return sorted(trovate.values(), key=lambda g: (ordine.get(g["chiave"], len(ordine)), g["chiave"]))


def sensori_da_casa(casa: dict, stato: dict) -> list:
    """Sensori della stazione meteo tra i moduli di homestatus (`stato`, con i nomi dalla casa di
    homesdata): quelli di un tipo meteo con almeno una misura."""
    nomi = {m.get("id"): m for m in casa.get("modules", []) or []}
    return [normalizza_modulo_casa(m, nomi.get(m.get("id"), {}), casa.get("name"))
            for m in stato.get("modules", []) or []
            if m.get("type") in TIPI_MODULI_METEO and any(not g["diagnostica"] for g in estrai_grandezze(m))]


def stanza_del_modulo(casa: dict, modulo_id: str) -> dict:
    """{room_id, stanza}: la stanza Netatmo (homesdata) in cui sta il modulo, se c'e'."""
    modulo = next((m for m in casa.get("modules", []) or [] if m.get("id") == modulo_id), {})
    rid = modulo.get("room_id")
    stanza = next((r for r in casa.get("rooms", []) or [] if r.get("id") == rid), {})
    return {"room_id": rid, "stanza": stanza.get("name") if rid else None}


def normalizza_modulo_esterno(modulo: dict, stazione: dict) -> dict:
    """Base o modulo di getstationsdata nei campi usati da TermoPilota.

    `ts` e' l'ora della misura (epoch, da dashboard_data.time_utc): un modulo
    non raggiungibile non ha dashboard_data, quindi temperatura e ora sono None."""
    misure = modulo.get("dashboard_data") or {}
    return {
        "id": modulo.get("_id"),
        "tipo": modulo.get("type"),
        "esterno": modulo.get("type") in TIPI_MODULI_ESTERNI,
        "nome": modulo.get("module_name") or "Sensore meteo",
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
        "grandezze": estrai_grandezze(misure, {k: modulo.get(k) for k in ("battery_percent", "rf_status", "wifi_status")}),
        "_campi": sorted(misure.keys()),
        "grezzo": modulo,
    }


def normalizza_modulo_casa(modulo: dict, dati: dict, nome_casa: Optional[str]) -> dict:
    """Modulo esterno di homestatus (`dati` = lo stesso modulo in homesdata, per il nome)
    nei campi di `normalizza_modulo_esterno`. Minima, massima e tendenza non ci sono; senza la
    percentuale di Netatmo la carica e' stimata dalla tensione (`battery_level`, mV), se nota."""
    batteria = modulo.get("battery_percent")
    tensione = _num(modulo.get("battery_level"))
    if batteria is None and tensione is not None and modulo.get("type") in BATTERIA_MV:
        minimo, massimo = BATTERIA_MV[modulo["type"]]
        batteria = round(max(0.0, min(100.0, (tensione - minimo) / (massimo - minimo) * 100)))
    ts = modulo.get("ts") or modulo.get("last_seen")
    return {
        "id": modulo.get("id"),
        "tipo": modulo.get("type"),
        "esterno": modulo.get("type") in TIPI_MODULI_ESTERNI,
        "nome": dati.get("name") or "Sensore meteo",
        "stazione": nome_casa or "Stazione meteo",
        "stazione_id": modulo.get("bridge"),
        "temperatura": _num(modulo.get("temperature")),
        "umidita": _num(modulo.get("humidity")),
        "minima": None,
        "massima": None,
        "tendenza": None,
        "ts": ts,
        "batteria_pct": batteria,
        "segnale_radio": modulo.get("rf_strength"),
        "firmware": modulo.get("firmware_revision"),
        "raggiungibile": modulo.get("reachable", True) is not False and modulo.get("ts") is not None,
        "grandezze": estrai_grandezze(modulo, {"battery_percent": batteria}),
        "_campi": sorted(modulo.keys()),
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
