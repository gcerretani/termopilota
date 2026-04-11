"""
Provider termostati Netatmo per BTicino Smarther with Netatmo.

App: Home + Control (Legrand/Netatmo/BTicino) — account Netatmo
Registrazione app: https://dev.netatmo.com
Scopes necessari: read_smarther write_smarther
"""

import json
import time
import os
import logging
from typing import Optional

import requests

from providers import ThermostatProvider, register_thermostat

logger = logging.getLogger(__name__)

NETATMO_AUTH_URL = "https://api.netatmo.com/oauth2/authorize"
NETATMO_TOKEN_URL = "https://api.netatmo.com/oauth2/token"
NETATMO_BASE = "https://api.netatmo.com/api"

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "config.json")


class NetatmoClient(ThermostatProvider):
    """Client OAuth2 per API Netatmo (termostati BTicino Smarther with Netatmo)."""

    def __init__(self, client_id: str, client_secret: str, token_data: Optional[dict] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = token_data or {}

    # ── OAuth2 ────────────────────────────────────────────────────────────────

    def url_autorizzazione(self, redirect_uri: str, state: str = "domotica") -> str:
        """URL per il flusso OAuth2 Authorization Code (primo accesso)."""
        params = (
            f"client_id={self.client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&scope=read_smarther+write_smarther"
            f"&response_type=code"
            f"&state={state}"
        )
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
        if not os.path.exists(CONFIG_FILE):
            return
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["legrand_token"] = self._token
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def _headers(self) -> dict:
        if not self._token:
            raise RuntimeError("Token Netatmo non presente. Esegui prima il flusso OAuth2.")
        if time.time() > self._token.get("_expires_at", 0):
            self._refresh()
        return {"Authorization": f"Bearer {self._token['access_token']}"}

    @property
    def autenticato(self) -> bool:
        return bool(self._token.get("access_token"))

    # ── API Netatmo termostati ────────────────────────────────────────────────

    def lista_impianti(self) -> list:
        resp = requests.get(f"{NETATMO_BASE}/homesdata", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        homes = resp.json().get("body", {}).get("homes", [])
        return [{"id": h["id"], "name": h.get("name", "Casa")} for h in homes]

    def lista_moduli(self, home_id: str) -> list:
        resp = requests.get(f"{NETATMO_BASE}/homesdata", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        homes = resp.json().get("body", {}).get("homes", [])
        home = next((h for h in homes if h["id"] == home_id), None)
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
        resp = requests.get(
            f"{NETATMO_BASE}/homestatus",
            headers=self._headers(),
            params={"home_id": home_id},
            timeout=10,
        )
        resp.raise_for_status()
        rooms = resp.json().get("body", {}).get("home", {}).get("rooms", [])
        risultato = {}
        for r in rooms:
            risultato[r["id"]] = {
                "room_id": r["id"],
                "temperatura_attuale": r.get("therm_measured_temperature"),
                "setpoint": r.get("therm_setpoint_temperature"),
                "modalita": r.get("therm_setpoint_mode"),
                "sta_riscaldando": r.get("heating_power_request", 0) > 0,
            }
        return risultato

    def stato_termostato(self, home_id: str, room_id: str) -> dict:
        """Legge lo stato corrente di una stanza/termostato."""
        resp = requests.get(
            f"{NETATMO_BASE}/homestatus",
            headers=self._headers(),
            params={"home_id": home_id},
            timeout=10,
        )
        resp.raise_for_status()
        rooms = resp.json().get("body", {}).get("home", {}).get("rooms", [])
        room = next((r for r in rooms if r["id"] == room_id), {})
        return {
            "module_id": room_id,
            "plant_id": home_id,
            "temperatura_attuale": room.get("therm_measured_temperature"),
            "setpoint": room.get("therm_setpoint_temperature"),
            "modalita": room.get("therm_setpoint_mode"),
            "sta_riscaldando": room.get("heating_power_request", 0) > 0,
        }

    def imposta_modalita(self, home_id: str, room_id: str, mode: str, setpoint: float = 7.0) -> bool:
        if mode == "AUTOMATIC":
            mode = "schedule"
        if mode == "OFF":
            mode = "manual"

        payload = {
            "home_id": home_id,
            "room_id": room_id,
            "mode": mode,
        }
        if mode == "manual":
            payload["temp"] = setpoint
            payload["endtime"] = int(time.time()) + 12 * 3600

        resp = requests.post(
            f"{NETATMO_BASE}/setroomthermpoint",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            return True
        logger.error("Errore imposta_modalita room %s: %s %s", room_id, resp.status_code, resp.text)
        return False


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
