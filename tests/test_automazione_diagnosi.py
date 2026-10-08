# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Il log dell'automazione deve dire perche' mancano i dati Netatmo di una stanza."""

from termopilota.automazione import _motivo_dati_mancanti


class _Client:
    def __init__(self, autenticato=True):
        self.autenticato = autenticato


def test_client_assente():
    assert "non configurato" in _motivo_dati_mancanti(None, "casa", "r1", {})


def test_client_non_collegato():
    assert "non collegato" in _motivo_dati_mancanti(_Client(False), "casa", "r1", {})


def test_plant_id_vuoto():
    assert "Plant ID" in _motivo_dati_mancanti(_Client(), "", "r1", {})


def test_nessuna_stanza_restituita():
    assert "nessuna stanza" in _motivo_dati_mancanti(_Client(), "casa", "r1", {})


def test_stanza_non_presente_elenca_quelle_ricevute():
    motivo = _motivo_dati_mancanti(_Client(), "casa", "r1", {"r2": {}, "r3": {}})
    assert "r1" in motivo and "r2" in motivo and "r3" in motivo


def test_campi_mancanti_con_elenco_dei_campi_ricevuti():
    stati = {"r1": {"temperatura_attuale": 19.5, "setpoint": None, "_campi": ["id", "reachable"]}}
    motivo = _motivo_dati_mancanti(_Client(), "casa", "r1", stati)
    assert "setpoint" in motivo and "temperatura" not in motivo.split("(")[0]
    assert "id, reachable" in motivo
