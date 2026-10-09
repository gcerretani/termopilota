# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Polling Netatmo: confronto tra letture, cambi esterni o nostri, notifiche,
ricalcolo dell'automazione, errori di lettura."""

import copy

import pytest

from dispositivi_finti import NetatmoFinto, SmartThingsFinto
from termopilota import live, osservatore, registro


def _lettura(**stanza):
    base = {"setpoint": 20, "modalita": "home", "setpoint_fine": None, "raggiungibile": True,
            "finestra_aperta": False, "temperatura_attuale": 19.5, "nome": "Salotto"}
    return {"casa": {"id": "casa-1", "name": "Casa", "therm_mode": "schedule", "programma_attivo": "Inverno",
                     "temperature_control_mode": "heating"},
            "stanze": {"stanza-1": {**base, **stanza}}}


def test_differenze_campi_rilevanti():
    prima = _lettura()
    assert osservatore.differenze(None, prima) == []           # prima lettura: niente
    assert osservatore.differenze(prima, _lettura(temperatura_attuale=21.0)) == []
    cambi = osservatore.differenze(prima, _lettura(setpoint=22, modalita="manual", setpoint_fine=1_800_000_000))
    assert {c["campo"] for c in cambi} == {"setpoint", "modalita", "setpoint_fine"}
    dopo = _lettura(finestra_aperta=True)
    dopo["casa"]["therm_mode"] = "away"
    dopo["casa"]["programma_attivo"] = "Casa calda"
    campi = {(c["tipo"], c["campo"]) for c in osservatore.differenze(prima, dopo)}
    assert campi == {("stanza", "finestra_aperta"), ("casa", "therm_mode"), ("casa", "programma_attivo")}


def test_termostato_perso_e_ritrovato_solo_raggiungibilita():
    # Regressione: "termostato 7 °C → —, modalità programma → —" quando Netatmo lo perde
    attivo = _lettura(setpoint=7)
    perso = _lettura(setpoint=None, modalita=None, raggiungibile=False)
    [cambio] = osservatore.differenze(attivo, perso)
    assert cambio["campo"] == "raggiungibile"
    assert osservatore.descrivi([cambio]) == "termostato non raggiungibile (segnalato da Netatmo)"
    [ritorno] = osservatore.differenze(perso, attivo)
    assert osservatore.descrivi([ritorno]) == "termostato di nuovo raggiungibile"


def test_descrizione_leggibile():
    cambi = osservatore.differenze(_lettura(), _lettura(setpoint=21.5, modalita="manual"))
    assert osservatore.descrivi(cambi) == "termostato 20 °C → 21.5 °C, modalità programma → manuale"


@pytest.mark.parametrize("valore, atteso", [(None, 120), (10, 60), (5000, 900), ("x", 120), (180, 180)])
def test_intervallo(valore, atteso):
    assert osservatore.intervallo({"netatmo_polling_secondi": valore}) == atteso


@pytest.fixture
def netatmo(monkeypatch):
    from termopilota import app as modulo_app
    from termopilota import dispositivi, providers
    st, bt = SmartThingsFinto(), NetatmoFinto()
    for modulo in (providers, dispositivi, modulo_app):
        monkeypatch.setattr(modulo, "get_heatpump", lambda nome, cfg: st)
        monkeypatch.setattr(modulo, "get_thermostat", lambda nome, cfg: bt)
    cfg = {"legrand_plant_id": "casa-1", "automazione_attiva": True,
           "zone": [{"nome": "Salotto", "room_id": "stanza-1", "ac_device_id": "ac-1"}]}
    ricalcoli = []
    oss = osservatore.OsservatoreNetatmo(lambda: cfg, lambda c, tipo, ids: (lambda: ricalcoli.append(ids)))
    return bt, cfg, oss, ricalcoli


def _cambia_setpoint(bt, valore, modo="manual"):
    stanza = bt.casa["stato"]["rooms"][0]
    stanza.update(therm_setpoint_temperature=valore, therm_setpoint_mode=modo)


def test_cambio_esterno_notifica_e_ricalcola(netatmo):
    bt, cfg, oss, ricalcoli = netatmo
    assert oss.controlla(cfg, adesso=1000) == []                 # prima lettura
    _cambia_setpoint(bt, 23)
    cambi = oss.controlla(cfg, adesso=1120)
    assert {c["campo"] for c in cambi} == {"setpoint", "modalita"}
    [riga] = registro.leggi(categorie=["evento"])
    assert riga["oggetto"] == "Salotto" and "esterno" in riga["messaggio"]
    assert riga["dati"]["sorgente"] == "polling" and riga["dati"]["origine"] == "esterna"
    assert ricalcoli == [["stanza-1"]]
    s = live.stato()["netatmo_polling"]
    assert s["eventi"] == 1 and s["ultimo"]["dispositivi"] == ["stanza-1"]


def test_cambio_dopo_un_nostro_comando_non_ricalcola(netatmo):
    bt, cfg, oss, ricalcoli = netatmo
    oss.controlla(cfg)
    live.comando_nostro("stanza-1")
    _cambia_setpoint(bt, 7)
    oss.controlla(cfg)
    [riga] = registro.leggi(categorie=["evento"])
    assert "da TermoPilota" in riga["messaggio"] and riga["dati"]["origine"] == "termopilota"
    assert ricalcoli == []
    assert live.stato()["versione"] == 1                          # le pagine si aggiornano comunque


def test_nessun_cambio_nessuna_riga(netatmo):
    _, cfg, oss, _ = netatmo
    oss.controlla(cfg, adesso=10_000)
    oss.controlla(cfg, adesso=10_120)
    assert registro.leggi(categorie=["evento"]) == []             # (il "vivo" orario e' debug)
    assert len(registro.leggi(categorie=["evento"], livello_min="debug")) == 1


def test_errore_registrato_una_volta_sola(netatmo, monkeypatch):
    from termopilota import dispositivi
    bt, cfg, oss, _ = netatmo
    monkeypatch.setattr(dispositivi.time, "sleep", lambda s: None)
    oss.controlla(cfg)
    salvato = copy.deepcopy(bt.casa)

    def guasto(home_id):
        raise ConnectionError("timeout")
    monkeypatch.setattr(bt, "stato_casa", guasto)
    oss.controlla(cfg)
    oss.controlla(cfg)
    dispositivi.snapshot(cfg, forza=True)        # anche un altro lettore: nessun avviso in piu'
    avvisi = registro.leggi(categorie=["sistema"], livello_min="warning")
    assert len(avvisi) == 1 and avvisi[0]["messaggio"] == "Netatmo non risponde: timeout"
    monkeypatch.setattr(bt, "stato_casa", lambda home_id: copy.deepcopy(salvato))
    oss.controlla(cfg)
    assert registro.leggi(categorie=["sistema"])[0]["messaggio"].startswith("Netatmo risponde di nuovo")


def test_primo_tentativo_fallito_non_e_un_avviso(netatmo, monkeypatch):
    from termopilota import dispositivi
    bt, cfg, oss, _ = netatmo
    monkeypatch.setattr(dispositivi.time, "sleep", lambda s: None)
    vero, chiamate = bt.stato_casa, []

    def una_volta(home_id):
        chiamate.append(home_id)
        if len(chiamate) == 1:
            raise ConnectionError("503")
        return vero(home_id)
    monkeypatch.setattr(bt, "stato_casa", una_volta)
    oss.controlla(cfg)
    assert len(chiamate) == 2 and registro.leggi(livello_min="warning") == []


def test_raggiungibilita_nel_registro_senza_esterno_ne_ricalcolo(netatmo):
    bt, cfg, oss, ricalcoli = netatmo
    oss.controlla(cfg)
    bt.casa["stato"]["rooms"][0]["reachable"] = False
    oss.controlla(cfg)
    [riga] = registro.leggi(categorie=["evento"], livello_min="warning")
    assert riga["messaggio"] == "termostato non raggiungibile (segnalato da Netatmo)"
    assert riga["dati"]["origine"] == "netatmo" and ricalcoli == []


def test_senza_netatmo_configurato_non_fa_nulla(netatmo):
    _, cfg, oss, _ = netatmo
    assert oss.controlla({}) == [] and registro.leggi(livello_min="debug") == []
