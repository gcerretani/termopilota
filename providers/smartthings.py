"""
Provider pompe di calore Samsung via SmartThings REST API.

Token PAT: https://account.smartthings.com/tokens
Documentazione: https://developer.smartthings.com/docs/api/public

Nota: SmartThings non supporta OAuth2 per app self-hosted.
L'unica autenticazione praticabile e' il Personal Access Token (PAT).
"""

import logging
from typing import Optional

import requests

from providers import HeatPumpProvider, register_heatpump

logger = logging.getLogger(__name__)

ST_BASE = "https://api.smartthings.com/v1"


class SmartThingsClient(HeatPumpProvider):
    """Client SmartThings per condizionatori Samsung."""

    def __init__(self, token: str):
        self.token = token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def configurato(self) -> bool:
        return bool(self.token)

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
    """Crea un SmartThingsClient dal token in config.json. Restituisce None se non configurato."""
    token = cfg.get("smartthings_token", "")
    if not token:
        return None
    return SmartThingsClient(token)


# Auto-registrazione nel registry
register_heatpump("smartthings", client_da_config)
