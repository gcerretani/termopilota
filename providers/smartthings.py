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
import json
import logging
import os
import time
from typing import Optional

import requests

from providers import HeatPumpProvider, register_heatpump

logger = logging.getLogger(__name__)

ST_BASE = "https://api.smartthings.com/v1"
ST_AUTH_URL = "https://api.smartthings.com/oauth/authorize"
ST_TOKEN_URL = "https://auth-global.api.smartthings.com/oauth/token"
ST_SCOPES = "r:devices:* x:devices:* r:locations:*"

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "config.json")


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

    def url_autorizzazione(self, redirect_uri: str, state: str = "smartthings") -> str:
        """URL per il flusso OAuth2 Authorization Code (primo accesso)."""
        params = (
            f"client_id={self.client_id}"
            f"&response_type=code"
            f"&redirect_uri={requests.utils.quote(redirect_uri, safe='')}"
            f"&scope={requests.utils.quote(ST_SCOPES, safe='')}"
            f"&state={state}"
        )
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
        if not os.path.exists(CONFIG_FILE):
            return
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["smartthings_token_data"] = self._token
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

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
                })
        return ac_list

    def stato_ac(self, device_id: str) -> dict:
        resp = requests.get(
            f"{ST_BASE}/devices/{device_id}/status",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        status = resp.json().get("components", {}).get("main", {})

        def _val(cap, attr):
            return status.get(cap, {}).get(attr, {}).get("value")

        acceso = _val("switch", "switch") == "on"
        return {
            "device_id": device_id,
            "acceso": acceso,
            "modalita": _val("airConditionerMode", "airConditionerMode"),
            "setpoint_riscaldamento": _val("thermostatCoolingSetpoint", "coolingSetpoint"),
            "temperatura_ambiente": _val("temperatureMeasurement", "temperature"),
        }

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

    def accendi_ac(self, device_id: str, setpoint: float = 21.0, modalita: str = "heat") -> bool:
        """Accende l'AC in modalita' riscaldamento con il setpoint indicato."""
        return self._comando(device_id, [
            {"component": "main", "capability": "switch", "command": "on", "arguments": []},
            {"component": "main", "capability": "airConditionerMode",
             "command": "setAirConditionerMode", "arguments": [modalita]},
            {"component": "main", "capability": "thermostatCoolingSetpoint",
             "command": "setCoolingSetpoint", "arguments": [setpoint]},
        ])

    def spegni_ac(self, device_id: str) -> bool:
        """Spegne l'AC."""
        return self._comando(device_id, [
            {"component": "main", "capability": "switch", "command": "off", "arguments": []},
        ])


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
