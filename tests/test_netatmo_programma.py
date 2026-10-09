# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Setpoint del programma Netatmo, stanze normalizzate e comandi inviati all'API."""

import copy
from datetime import datetime

import pytest

from dispositivi_finti import carica
from termopilota.providers import netatmo
from termopilota.providers.netatmo import (
    NetatmoClient, normalizza_stanza, programma_attivo, setpoint_programmato,
)

CASA = carica("netatmo_casa.json")["dati"]
LUNEDI = datetime(2026, 10, 5)   # un lunedi'


@pytest.mark.parametrize("giorno, ora, atteso", [
    (0, "03:00", 17),     # lunedi' notte: vale l'ultima fascia della settimana precedente
    (0, "06:00", 20),     # lunedi' comfort
    (0, "21:59", 20),
    (0, "22:00", 17),     # lunedi' notte
    (1, "07:30", 20),     # martedi' comfort
    (4, "12:00", 17),     # venerdi': resta la notte di martedi' (fino a fine settimana)
])
def test_setpoint_dalla_timetable(giorno, ora, atteso):
    h, m = map(int, ora.split(":"))
    adesso = LUNEDI.replace(day=LUNEDI.day + giorno, hour=h, minute=m)
    assert setpoint_programmato(CASA, "stanza-1", adesso) == atteso


def test_setpoint_per_stanza_diversa():
    assert setpoint_programmato(CASA, "stanza-2", LUNEDI.replace(hour=8)) == 19.5


@pytest.mark.parametrize("modo, atteso", [("away", 15), ("hg", 7)])
def test_setpoint_in_assenza_e_antigelo(modo, atteso):
    casa = dict(CASA, therm_mode=modo)
    assert setpoint_programmato(casa, "stanza-1", LUNEDI.replace(hour=8)) == atteso


def test_stanza_assente_o_senza_programmi():
    assert setpoint_programmato(CASA, "sconosciuta", LUNEDI.replace(hour=8)) is None
    assert setpoint_programmato(dict(CASA, schedules=[]), "stanza-1", LUNEDI) is None


def test_programma_cooling_ignorato():
    assert programma_attivo(CASA)["id"] == "prog-inverno"


def test_formato_rooms_temp():
    casa = copy.deepcopy(CASA)
    for zona in casa["schedules"][0]["zones"]:
        zona["rooms_temp"] = [{"room_id": r["id"], "temp": r["therm_setpoint_temperature"]} for r in zona.pop("rooms")]
    assert setpoint_programmato(casa, "stanza-1", LUNEDI.replace(hour=8)) == 20


def test_normalizza_stanza():
    s = normalizza_stanza(carica("netatmo_casa.json")["stato"]["rooms"][1])
    assert s["temperatura_attuale"] == 18.0 and s["setpoint"] == 19.5
    assert s["umidita"] == 75 and s["finestra_aperta"] is True
    assert s["richiesta_calore_pct"] == 40 and s["sta_riscaldando"] is True
    assert s["raggiungibile"] is True and s["modalita"] == "home"


class _Risposta:
    status_code = 200
    text = '{"status":"ok"}'


@pytest.fixture
def chiamate(monkeypatch):
    registro = []

    def finto_post(url, headers=None, data=None, timeout=None, **_):
        registro.append((url.rsplit("/", 1)[-1], data))
        return _Risposta()

    monkeypatch.setattr(netatmo.requests, "post", finto_post)
    return registro


def _client():
    return NetatmoClient("id", "secret", {"access_token": "t", "_expires_at": 9e12})


def test_ripristino_usa_mode_home(chiamate):
    assert _client().imposta_modalita("casa-1", "stanza-1", "AUTOMATIC")
    endpoint, dati = chiamate[0]
    assert endpoint == "setroomthermpoint"
    assert dati == {"home_id": "casa-1", "room_id": "stanza-1", "mode": "home"}


def test_manuale_con_temperatura_e_scadenza(chiamate):
    _client().imposta_modalita("casa-1", "stanza-1", "OFF", setpoint=7.0, fine=2_000_000_000)
    _, dati = chiamate[0]
    assert dati["mode"] == "manual" and dati["temp"] == 7.0 and dati["endtime"] == 2_000_000_000


def test_modalita_non_valida_rifiutata():
    with pytest.raises(ValueError):
        _client().imposta_modalita("casa-1", "stanza-1", "schedule")


def test_modalita_casa_e_programma(chiamate):
    c = _client()
    c.imposta_modalita_casa("casa-1", "away", fine=2_000_000_000)
    c.cambia_programma("casa-1", "prog-inverno")
    assert chiamate[0] == ("setthermmode", {"home_id": "casa-1", "mode": "away", "endtime": 2_000_000_000})
    assert chiamate[1] == ("switchhomeschedule", {"home_id": "casa-1", "schedule_id": "prog-inverno"})
