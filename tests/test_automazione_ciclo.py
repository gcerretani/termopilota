# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ciclo completo dell'automazione con provider finti: comandi inviati una
volta sola, stato persistito, simulazione senza comandi, rilascio."""

import pytest

from dispositivi_finti import NetatmoFinto, SmartThingsFinto

ZONE = [{"nome": "Salotto", "room_id": "stanza-1", "ac_device_id": "ac-1"}]
COSTI = {"temp_esterna": 6.0, "costo_gas_kwh": 0.11, "costo_ac_kwh": 0.07, "cop": 3.6}


@pytest.fixture
def ciclo(monkeypatch):
    from termopilota import app as modulo_app
    from termopilota import automazione, dispositivi, providers
    st, bt = SmartThingsFinto(), NetatmoFinto()
    for modulo in (providers, dispositivi, modulo_app):
        monkeypatch.setattr(modulo, "get_heatpump", lambda nome, cfg: st)
        monkeypatch.setattr(modulo, "get_thermostat", lambda nome, cfg: bt)
    # Target del programma fisso: il risultato non dipende dall'ora del test
    monkeypatch.setattr(dispositivi, "setpoint_programmato", lambda casa, rid, adesso: 20.0)
    servizio = automazione.get_servizio()
    monkeypatch.setattr(servizio, "_fornitore", lambda cfg: dict(COSTI))

    def configura(**altro):
        cfg = modulo_app.carica_config()
        cfg.update(legrand_plant_id="casa-1", zone=ZONE, automazione_attiva=True, **altro)
        modulo_app.salva_config(cfg)

    configura()
    return servizio, st, bt, configura


def test_primo_ciclo_invia_i_comandi_e_il_secondo_no(ciclo):
    servizio, st, bt, _ = ciclo
    servizio._ciclo()
    assert bt.comandi[0][:3] == ("stanza-1", "manual", 7.0)
    [accensione] = st.comandi     # la modalita' opzionale dipende dall'ora (fascia notte)
    assert accensione[:4] == ("ac-1", "accendi", 20, "auto")
    assert servizio.leggi_stato()["ac"]["ac-1"]["acceso_da_noi"] is True
    zona = servizio.stato()["zone"][0]
    assert zona["stato"] == "ac" and zona["simulazione"] is False

    # Il termostato ora e' in manuale a 7 °C e l'AC acceso: nulla da reinviare
    stanza = bt.casa["stato"]["rooms"][0]
    stanza.update(therm_setpoint_mode="manual", therm_setpoint_temperature=7.0)
    st.stato["switch"]["switch"]["value"] = "on"
    st.stato["airConditionerMode"]["airConditionerMode"]["value"] = "heat"
    st.stato["thermostatCoolingSetpoint"]["coolingSetpoint"]["value"] = 20
    bt.comandi.clear()
    st.comandi.clear()
    servizio._ciclo()
    assert bt.comandi == [] and st.comandi == []
    assert servizio.stato()["zone"][0]["stato"] == "ac"


def test_simulazione_non_invia_comandi(ciclo):
    servizio, st, bt, configura = ciclo
    configura(automazione_simulazione=True)
    servizio._ciclo()
    assert bt.comandi == [] and st.comandi == []
    zona = servizio.stato()["zone"][0]
    assert zona["stato"] == "ac" and zona["simulazione"] is True
    assert any(e["azione"].startswith("[simulazione]") for e in servizio.stato()["log"])
    assert servizio.leggi_stato()["ac"] == {}     # lo stato reale non cambia


def test_rilascio_allo_spegnimento(ciclo):
    servizio, st, bt, _ = ciclo
    servizio._ciclo()
    bt.comandi.clear()
    st.comandi.clear()
    servizio.rilascia_tutto()
    assert bt.comandi[0][:2] == ("stanza-1", "home")
    assert st.comandi == [("ac-1", "spegni")]
    assert servizio.leggi_stato()["ac"]["ac-1"]["acceso_da_noi"] is False


def test_comando_fallito_si_ritenta_al_ciclo_dopo(ciclo):
    servizio, st, bt, _ = ciclo
    st.esito = False
    servizio._ciclo()
    assert servizio.leggi_stato()["ac"]["ac-1"]["acceso_da_noi"] is False
    assert servizio.stato()["zone"][0]["errore_ac"]
    st.esito = True
    st.comandi.clear()
    servizio._ciclo()
    assert st.comandi and st.comandi[0][1] == "accendi"


def test_costi_non_disponibili_ciclo_saltato(ciclo, monkeypatch):
    servizio, st, bt, _ = ciclo
    monkeypatch.setattr(servizio, "_fornitore", lambda cfg: None)
    servizio._ciclo()
    assert bt.comandi == [] and st.comandi == []
    assert "ciclo saltato" in servizio.stato()["log"][0]["dettaglio"]
