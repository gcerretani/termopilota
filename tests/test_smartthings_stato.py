# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stato normalizzato dei condizionatori SmartThings e whitelist dei controlli manuali."""

import copy

import pytest

from dispositivi_finti import DEFINIZIONI, carica
from termopilota.providers.smartthings import (
    controlli_disponibili, normalizza_stato, valida_comando,
)

STATO = carica("smartthings_status_ac.json")


def _chiavi(controlli):
    return [c["chiave"] for c in controlli]


def test_normalizza_stato_dal_dispositivo_reale():
    s = normalizza_stato(STATO)
    assert s["acceso"] is False
    assert s["modalita"] == "cool"
    assert s["setpoint_riscaldamento"] == 25
    assert s["temperatura_ambiente"] == 24
    assert s["umidita"] == 51
    assert s["ventola"] == "auto"
    assert s["modalita_opzionale"] == "off"
    assert s["energia_wh"] == 169612
    assert s["potenza_w"] == 0
    assert (s["filtro_uso_h"], s["filtro_capacita_h"], s["filtro_stato"]) == (20, 500, "normal")
    assert (s["setpoint_min"], s["setpoint_max"], s["setpoint_passo"]) == (16, 30, 1)


def test_normalizza_stato_vuoto_non_esplode():
    s = normalizza_stato({})
    assert s["acceso"] is False and s["energia_wh"] is None


def test_controlli_con_definizioni_per_admin():
    chiavi = _chiavi(controlli_disponibili(STATO, DEFINIZIONI, is_admin=True))
    assert chiavi == ["accensione", "modalita", "setpoint", "ventola", "oscillazione",
                      "modalita_opzionale", "display", "beep", "pulizia", "soglia_filtro", "reset_filtro"]


def test_controlli_di_servizio_nascosti_ai_non_admin():
    chiavi = _chiavi(controlli_disponibili(STATO, DEFINIZIONI, is_admin=False))
    assert "beep" not in chiavi and "reset_filtro" not in chiavi and "soglia_filtro" not in chiavi
    assert "ventola" in chiavi


def test_senza_definizioni_restano_solo_le_capability_standard():
    chiavi = _chiavi(controlli_disponibili(STATO, {}, is_admin=True))
    assert chiavi == ["accensione", "modalita", "setpoint", "ventola", "oscillazione"]


def test_comando_dichiarato_non_disponibile_viene_escluso():
    stato = copy.deepcopy(STATO)
    stato["samsungce.unavailableCapabilities"]["unavailableCommands"]["value"].append(
        "custom.airConditionerOptionalMode.setAcOptionalMode")
    assert "modalita_opzionale" not in _chiavi(controlli_disponibili(stato, DEFINIZIONI))


def test_comando_assente_dalla_definizione_viene_escluso():
    definizioni = dict(DEFINIZIONI, **{"samsungce.airConditionerLighting": {"commands": {"on": {}}}})
    assert "display" not in _chiavi(controlli_disponibili(STATO, definizioni))


def test_capability_disattivata_esclusa():
    stato = copy.deepcopy(STATO)
    stato["custom.disabledCapabilities"]["disabledCapabilities"]["value"].append("custom.dustFilter")
    assert "reset_filtro" not in _chiavi(controlli_disponibili(stato, DEFINIZIONI))


def test_valori_ammessi_e_etichette():
    controlli = {c["chiave"]: c for c in controlli_disponibili(STATO, DEFINIZIONI)}
    assert controlli["modalita"]["valori"] == ["auto", "cool", "dry", "fan", "heat"]
    assert controlli["modalita"]["etichette"]["heat"] == "Riscaldamento"
    assert controlli["setpoint"]["minimo"] == 16 and controlli["setpoint"]["massimo"] == 30
    assert controlli["soglia_filtro"]["valori"] == [180, 300, 500, 700]
    assert controlli["reset_filtro"]["conferma"]


@pytest.mark.parametrize("chiave, valore, atteso", [
    ("ventola", "low", ("airConditionerFanMode", "setFanMode", ["low"])),
    ("accensione", "on", ("switch", "on", [])),
    ("setpoint", "21.4", ("thermostatCoolingSetpoint", "setCoolingSetpoint", [21])),
    ("soglia_filtro", "300", ("samsungce.dustFilterAlarm", "setAlarmThreshold", [300])),
    ("reset_filtro", None, ("custom.dustFilter", "resetDustFilter", [])),
    ("beep", "off", ("samsungce.airConditionerBeep", "off", [])),
])
def test_valida_comando_ammesso(chiave, valore, atteso):
    controlli = controlli_disponibili(STATO, DEFINIZIONI)
    assert valida_comando(controlli, chiave, valore) == atteso


@pytest.mark.parametrize("chiave, valore", [
    ("ventola", "fortissima"),
    ("setpoint", 35),
    ("setpoint", "abc"),
    ("accensione", "forse"),
    ("inesistente", "on"),
])
def test_valida_comando_rifiuta(chiave, valore):
    controlli = controlli_disponibili(STATO, DEFINIZIONI)
    with pytest.raises(ValueError):
        valida_comando(controlli, chiave, valore)


def test_valida_comando_admin_non_disponibile_al_non_admin():
    controlli = controlli_disponibili(STATO, DEFINIZIONI, is_admin=False)
    with pytest.raises(ValueError):
        valida_comando(controlli, "reset_filtro", None)
