# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Provider pompe di calore Samsung via SmartThings REST API.

Autenticazione supportata:

1. **OAuth2 Authorization Code** (consigliato): access token 24h + refresh token
   rotante che si auto-rinnova. Richiede la registrazione di un'app API_ONLY
   tramite SmartThings CLI (https://github.com/SmartThingsCommunity/smartthings-cli):

       npm install -g @smartthings/cli
       smartthings apps:create
         → tipo: API_ONLY
         → scopes: r:devices:*, x:devices:*
         → redirect URI: https://<host>/api/automazione/smartthings-callback

   La CLI restituisce client_id e client_secret da incollare in TermoPilota.

2. **Personal Access Token (PAT)**: supportato per retrocompatibilita', ma dal
   30/12/2024 i PAT nuovi scadono dopo 24h, quindi sconsigliato.

Documentazione: https://developer.smartthings.com/docs/connected-services/oauth-integrations
"""

import base64
import logging
import os
import time
from typing import Optional
from urllib.parse import urlencode

import requests

from termopilota.providers import HeatPumpProvider, register_heatpump, aggiorna_config_atomico
from termopilota.percorsi import CONFIG_FILE

logger = logging.getLogger(__name__)

ST_BASE = "https://api.smartthings.com/v1"
ST_AUTH_URL = "https://api.smartthings.com/oauth/authorize"
ST_TOKEN_URL = "https://auth-global.api.smartthings.com/oauth/token"
ST_SCOPES = "r:devices:* x:devices:* r:locations:*"


class SmartThingsClient(HeatPumpProvider):
    """Client SmartThings per condizionatori Samsung (OAuth2 + fallback PAT)."""

    def __init__(
        self,
        token: str = "",
        client_id: str = "",
        client_secret: str = "",
        token_data: Optional[dict] = None,
    ):
        self.pat = token
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
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": ST_SCOPES,
            "state": state,
        })
        return f"{ST_AUTH_URL}?{params}"

    def _basic_auth(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def scambia_codice(self, code: str, redirect_uri: str) -> dict:
        """Scambia il codice OAuth2 con access + refresh token."""
        resp = requests.post(
            ST_TOKEN_URL,
            headers={
                "Authorization": self._basic_auth(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
            },
            timeout=10,
        )
        resp.raise_for_status()
        self._token = resp.json()
        self._token["_expires_at"] = time.time() + self._token.get("expires_in", 86400) - 60
        self._salva_token()
        return self._token

    def _refresh(self) -> None:
        """Rinnova l'access token usando il refresh token (che viene ruotato)."""
        resp = requests.post(
            ST_TOKEN_URL,
            headers={
                "Authorization": self._basic_auth(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._token["refresh_token"],
                "client_id": self.client_id,
            },
            timeout=10,
        )
        resp.raise_for_status()
        self._token.update(resp.json())
        self._token["_expires_at"] = time.time() + self._token.get("expires_in", 86400) - 60
        self._salva_token()

    def _salva_token(self) -> None:
        aggiorna_config_atomico(CONFIG_FILE, lambda cfg: cfg.update({"smartthings_token_data": self._token}))

    def _headers(self) -> dict:
        # OAuth2 ha priorita' se configurato correttamente
        if self._token.get("access_token") and self.client_id and self.client_secret:
            if time.time() > self._token.get("_expires_at", 0):
                self._refresh()
            return {"Authorization": f"Bearer {self._token['access_token']}"}
        if self.pat:
            return {"Authorization": f"Bearer {self.pat}"}
        raise RuntimeError("SmartThings non configurato: serve OAuth2 o PAT.")

    @property
    def configurato(self) -> bool:
        if self._token.get("access_token") and self.client_id and self.client_secret:
            return True
        return bool(self.pat)

    @property
    def autenticato_oauth(self) -> bool:
        return bool(self._token.get("access_token"))

    # ── Lettura dispositivi ───────────────────────────────────────────────────

    def lista_dispositivi_ac(self) -> list:
        resp = requests.get(f"{ST_BASE}/devices", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        dispositivi = resp.json().get("items", [])
        ac_list = []
        for d in dispositivi:
            caps = [
                c.get("id")
                for comp in d.get("components", [])
                for c in comp.get("capabilities", [])
            ]
            if "airConditionerMode" in caps:
                ac_list.append({
                    "device_id": d["deviceId"],
                    "label": d.get("label", d.get("name", "AC")),
                    "location_id": d.get("locationId", ""),
                    # {capability: versione} del componente main, per le definizioni dei comandi
                    "capability": {
                        c.get("id"): c.get("version", 1)
                        for comp in d.get("components", []) if comp.get("id") == "main"
                        for c in comp.get("capabilities", [])
                    },
                    "ocf": d.get("ocf") or {},
                })
        return ac_list

    def stato_completo(self, device_id: str) -> dict:
        """Componente `main` dello stato, cosi' come lo restituisce l'API:
        {capability: {attributo: {value, unit, timestamp}}}."""
        resp = requests.get(
            f"{ST_BASE}/devices/{device_id}/status",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("components", {}).get("main", {})

    def stato_ac(self, device_id: str) -> dict:
        return {"device_id": device_id, **normalizza_stato(self.stato_completo(device_id))}

    def definizione_capability(self, capability: str, versione: int = 1) -> Optional[dict]:
        """Definizione di una capability (comandi e argomenti), in cache per processo.
        None se non e' leggibile: i controlli che ne dipendono restano nascosti."""
        chiave = (capability, versione)
        if chiave not in _definizioni:
            try:
                resp = requests.get(f"{ST_BASE}/capabilities/{capability}/{versione}",
                                    headers=self._headers(), timeout=10)
                resp.raise_for_status()
                _definizioni[chiave] = resp.json()
            except Exception as e:
                logger.warning("Definizione capability %s/%s non disponibile: %s", capability, versione, e)
                return None
        return _definizioni[chiave]

    # ── Comandi ───────────────────────────────────────────────────────────────

    def _comando(self, device_id: str, commands: list) -> bool:
        resp = requests.post(
            f"{ST_BASE}/devices/{device_id}/commands",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"commands": commands},
            timeout=10,
        )
        if resp.status_code in (200, 202, 204):
            return True
        logger.error("Errore comando AC %s: %s %s", device_id, resp.status_code, resp.text)
        return False

    def esegui_comando(self, device_id: str, capability: str, comando: str,
                       argomenti: Optional[list] = None) -> bool:
        """Un comando qualsiasi: la validazione (whitelist, valori ammessi) e' a monte."""
        return self._comando(device_id, [_cmd(capability, comando, argomenti)])

    def accendi_ac(self, device_id: str, setpoint: float = 21.0, modalita: str = "heat",
                   ventola: Optional[str] = None, modalita_opzionale: Optional[str] = None) -> bool:
        """Accende l'AC nella modalita' indicata (riscaldamento) al setpoint;
        ventola e modalita' opzionale (quiet, windFree, ...) solo se indicate."""
        comandi = [
            _cmd("switch", "on"),
            _cmd("airConditionerMode", "setAirConditionerMode", [modalita]),
            _cmd("thermostatCoolingSetpoint", "setCoolingSetpoint", [setpoint]),
        ]
        if ventola:
            comandi.append(_cmd("airConditionerFanMode", "setFanMode", [ventola]))
        if modalita_opzionale:
            comandi.append(_cmd("custom.airConditionerOptionalMode", "setAcOptionalMode",
                                [modalita_opzionale]))
        return self._comando(device_id, comandi)

    def spegni_ac(self, device_id: str) -> bool:
        """Spegne l'AC."""
        return self._comando(device_id, [_cmd("switch", "off")])


def _cmd(capability: str, comando: str, argomenti: Optional[list] = None) -> dict:
    return {"component": "main", "capability": capability, "command": comando,
            "arguments": list(argomenti or [])}


# Definizioni delle capability lette da /capabilities: non cambiano, si tengono per processo
_definizioni: dict = {}


def valore(stato: dict, capability: str, attributo: str):
    """Valore di un attributo nello stato grezzo (None se assente)."""
    return ((stato.get(capability) or {}).get(attributo) or {}).get("value")


def normalizza_stato(stato: dict) -> dict:
    """Campi usati da TermoPilota estratti dallo stato grezzo del componente main."""
    consumo = valore(stato, "powerConsumptionReport", "powerConsumption") or {}
    intervallo = valore(stato, "thermostatCoolingSetpoint", "coolingSetpointRange") or {}
    return {
        "acceso": valore(stato, "switch", "switch") == "on",
        "modalita": valore(stato, "airConditionerMode", "airConditionerMode"),
        "setpoint_riscaldamento": valore(stato, "thermostatCoolingSetpoint", "coolingSetpoint"),
        "temperatura_ambiente": valore(stato, "temperatureMeasurement", "temperature"),
        "umidita": valore(stato, "relativeHumidityMeasurement", "humidity"),
        "ventola": valore(stato, "airConditionerFanMode", "fanMode"),
        "oscillazione": valore(stato, "fanOscillationMode", "fanOscillationMode"),
        "modalita_opzionale": valore(stato, "custom.airConditionerOptionalMode", "acOptionalMode"),
        "display": valore(stato, "samsungce.airConditionerLighting", "lighting"),
        "energia_wh": consumo.get("energy"),
        "potenza_w": consumo.get("power"),
        "energia_fine": consumo.get("end"),
        "filtro_uso_h": valore(stato, "custom.dustFilter", "dustFilterUsage"),
        "filtro_capacita_h": valore(stato, "custom.dustFilter", "dustFilterCapacity"),
        "filtro_stato": valore(stato, "custom.dustFilter", "dustFilterStatus"),
        "setpoint_min": intervallo.get("minimum"),
        "setpoint_max": intervallo.get("maximum"),
        "setpoint_passo": intervallo.get("step"),
    }


# ── Controlli manuali ammessi ────────────────────────────────────────────────
# Whitelist dei comandi che la pagina Dispositivi puo' inviare. Ogni voce:
#   tipo 'switch' (comandi 'on'/'off'), 'enum' (un argomento tra `valori`),
#   'numero' (un argomento nell'intervallo), 'azione' (nessun argomento).
#   `valori`: attributo con l'elenco ammesso (gli 'available*' lo restringono se presenti).
#   `standard`: capability pubblica con comandi noti, usabile anche se la
#   definizione non e' leggibile; le altre (samsungce.*, custom.*) solo se
#   la definizione conferma il comando.
CONTROLLI_AC = [
    {"chiave": "accensione", "etichetta": "Accensione", "capability": "switch",
     "attributo": "switch", "tipo": "switch", "standard": True},
    {"chiave": "modalita", "etichetta": "Modalità", "capability": "airConditionerMode",
     "attributo": "airConditionerMode", "tipo": "enum", "comando": "setAirConditionerMode",
     "valori": "supportedAcModes", "disponibili": "availableAcModes", "standard": True},
    {"chiave": "setpoint", "etichetta": "Temperatura impostata", "capability": "thermostatCoolingSetpoint",
     "attributo": "coolingSetpoint", "tipo": "numero", "comando": "setCoolingSetpoint",
     "intervallo": "coolingSetpointRange", "unita": "°C", "standard": True},
    {"chiave": "ventola", "etichetta": "Ventola", "capability": "airConditionerFanMode",
     "attributo": "fanMode", "tipo": "enum", "comando": "setFanMode",
     "valori": "supportedAcFanModes", "disponibili": "availableAcFanModes", "standard": True},
    {"chiave": "oscillazione", "etichetta": "Oscillazione", "capability": "fanOscillationMode",
     "attributo": "fanOscillationMode", "tipo": "enum", "comando": "setFanOscillationMode",
     "valori": "supportedFanOscillationModes", "disponibili": "availableFanOscillationModes",
     "standard": True},
    {"chiave": "modalita_opzionale", "etichetta": "Modalità speciale",
     "capability": "custom.airConditionerOptionalMode", "attributo": "acOptionalMode",
     "tipo": "enum", "comando": "setAcOptionalMode",
     "valori": "supportedAcOptionalMode", "disponibili": "availableAcOptionalMode"},
    {"chiave": "display", "etichetta": "Display", "capability": "samsungce.airConditionerLighting",
     "attributo": "lighting", "tipo": "enum", "comando": "setLightingLevel",
     "valori": "supportedLightingLevels"},
    {"chiave": "beep", "etichetta": "Segnale acustico", "capability": "samsungce.airConditionerBeep",
     "attributo": "beep", "tipo": "switch", "solo_admin": True},
    {"chiave": "pulizia", "etichetta": "Pulizia automatica", "capability": "custom.autoCleaningMode",
     "attributo": "autoCleaningMode", "tipo": "enum", "comando": "setAutoCleaningMode",
     "valori": "supportedAutoCleaningModes", "solo_admin": True},
    {"chiave": "soglia_filtro", "etichetta": "Avviso pulizia filtro", "capability": "samsungce.dustFilterAlarm",
     "attributo": "alarmThreshold", "tipo": "enum", "comando": "setAlarmThreshold",
     "valori": "supportedAlarmThresholds", "unita": "h", "solo_admin": True},
    {"chiave": "reset_filtro", "etichetta": "Azzera contatore filtro", "capability": "custom.dustFilter",
     "attributo": "dustFilterUsage", "tipo": "azione", "comando": "resetDustFilter",
     "solo_admin": True, "conferma": "Azzerare il contatore del filtro? Fallo solo dopo averlo pulito."},
]

# Etichette italiane dei valori piu' comuni (gli altri si mostrano cosi' come sono)
ETICHETTE_VALORI = {
    "on": "Acceso", "off": "Spento", "auto": "Automatico", "cool": "Raffrescamento",
    "heat": "Riscaldamento", "dry": "Deumidificazione", "fan": "Ventilazione",
    "wind": "Ventilazione", "low": "Bassa", "medium": "Media", "high": "Alta", "turbo": "Turbo",
    "fixed": "Fissa", "vertical": "Verticale", "horizontal": "Orizzontale", "all": "Tutte",
    "sleep": "Notte", "quiet": "Silenzioso", "speed": "Rapido", "windFree": "WindFree",
    "windFreeSleep": "WindFree notte", "normal": "Regolare", "replace": "Da sostituire",
    "wash": "Da pulire",
}


def comandi_definiti(definizione: Optional[dict]) -> Optional[set]:
    if not definizione:
        return None
    return set((definizione.get("commands") or {}).keys())


def comandi_del_controllo(controllo: dict) -> list:
    if controllo["tipo"] == "switch":
        return ["on", "off"]
    return [controllo["comando"]]


def controlli_disponibili(stato: dict, definizioni: dict, is_admin: bool = True) -> list:
    """Controlli della whitelist utilizzabili su questo dispositivo, con i valori ammessi.

    `definizioni`: {capability: definizione o None}. Un controllo compare se la
    capability ha un attributo nello stato, il comando non e' tra quelli dichiarati
    non disponibili e (per le capability non standard) la definizione lo conferma.
    """
    non_disponibili = set(valore(stato, "samsungce.unavailableCapabilities", "unavailableCommands") or [])
    disattivate = set(valore(stato, "custom.disabledCapabilities", "disabledCapabilities") or [])
    risultato = []
    for c in CONTROLLI_AC:
        cap = c["capability"]
        if cap not in stato or cap in disattivate:
            continue
        if c.get("solo_admin") and not is_admin:
            continue
        comandi = comandi_del_controllo(c)
        if any(f"{cap}.{cmd}" in non_disponibili for cmd in comandi):
            continue
        definiti = comandi_definiti(definizioni.get(cap))
        if definiti is None and not c.get("standard"):
            continue
        if definiti is not None and not set(comandi) <= definiti:
            continue
        voce = {k: c[k] for k in ("chiave", "etichetta", "capability", "tipo") if k in c}
        voce.update({k: c[k] for k in ("comando", "unita", "conferma") if k in c})
        voce["solo_admin"] = bool(c.get("solo_admin"))
        voce["valore"] = valore(stato, cap, c["attributo"])
        if c["tipo"] == "switch":
            voce["valori"] = ["on", "off"]
        elif c["tipo"] == "enum":
            valori = valore(stato, cap, c["valori"])
            disponibili = valore(stato, cap, c["disponibili"]) if c.get("disponibili") else None
            if disponibili:
                valori = [v for v in (valori or disponibili) if v in disponibili]
            if not valori:
                continue
            voce["valori"] = list(valori)
        elif c["tipo"] == "numero":
            intervallo = valore(stato, cap, c["intervallo"]) or {}
            voce["minimo"] = intervallo.get("minimum", 16)
            voce["massimo"] = intervallo.get("maximum", 30)
            voce["passo"] = intervallo.get("step", 1) or 1
        voce["etichette"] = {str(v): ETICHETTE_VALORI.get(str(v), str(v)) for v in voce.get("valori", [])}
        risultato.append(voce)
    return risultato


def valida_comando(controlli: list, chiave: str, valore_richiesto) -> tuple:
    """Traduce una richiesta della UI in (capability, comando, argomenti).

    `controlli` e' l'esito di controlli_disponibili (gia' filtrato per utente);
    alza ValueError con un messaggio leggibile se la richiesta non e' ammessa.
    """
    controllo = next((c for c in controlli if c["chiave"] == chiave), None)
    if controllo is None:
        raise ValueError("Controllo non disponibile su questo dispositivo")
    tipo = controllo["tipo"]
    if tipo == "switch":
        if valore_richiesto not in ("on", "off"):
            raise ValueError("Valore ammesso: on oppure off")
        return controllo["capability"], valore_richiesto, []
    if tipo == "azione":
        return controllo["capability"], controllo["comando"], []
    if tipo == "enum":
        ammessi = controllo["valori"]
        # I valori numerici (soglia filtro) possono arrivare come stringa
        trovato = next((v for v in ammessi if str(v) == str(valore_richiesto)), None)
        if trovato is None:
            raise ValueError(f"Valore non ammesso: {valore_richiesto}")
        return controllo["capability"], controllo["comando"], [trovato]
    try:
        numero = float(valore_richiesto)
    except (TypeError, ValueError):
        raise ValueError("Serve un numero")
    if not controllo["minimo"] <= numero <= controllo["massimo"]:
        raise ValueError(f"Fuori intervallo ({controllo['minimo']}–{controllo['massimo']})")
    passo = controllo["passo"]
    numero = round(round((numero - controllo["minimo"]) / passo) * passo + controllo["minimo"], 2)
    if numero == int(numero):
        numero = int(numero)
    return controllo["capability"], controllo["comando"], [numero]


def client_da_config(cfg: dict) -> Optional[SmartThingsClient]:
    """Crea un SmartThingsClient da config.json. Restituisce None se non configurato.

    Preferisce OAuth2 (client_id+client_secret) se presenti, altrimenti PAT.
    """
    client_id = cfg.get("smartthings_client_id", "")
    client_secret = cfg.get("smartthings_client_secret", "")
    token_data = cfg.get("smartthings_token_data") or {}
    pat = cfg.get("smartthings_token", "")

    if client_id and client_secret:
        return SmartThingsClient(
            token=pat,
            client_id=client_id,
            client_secret=client_secret,
            token_data=token_data,
        )
    if pat:
        return SmartThingsClient(token=pat)
    return None


# Auto-registrazione nel registry
register_heatpump("smartthings", client_da_config)
