# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pagina Dispositivi e API collegate, con provider SmartThings e Netatmo finti:
fotografia, controlli, comandi validati, pause dopo un comando, zone."""

import pytest

from dispositivi_finti import NetatmoFinto, SmartThingsFinto

ZONE = [{"nome": "Salotto", "room_id": "stanza-1", "ac_device_id": "ac-1"},
        {"nome": "Studio", "room_id": "stanza-2", "ac_device_id": ""}]


@pytest.fixture
def finti(monkeypatch):
    from termopilota import app as modulo_app
    from termopilota import dispositivi, providers
    st, bt = SmartThingsFinto(), NetatmoFinto()
    for modulo in (providers, dispositivi, modulo_app):
        monkeypatch.setattr(modulo, "get_heatpump", lambda nome, cfg: st)
        monkeypatch.setattr(modulo, "get_thermostat", lambda nome, cfg: bt)
    cfg = modulo_app.carica_config()
    cfg.update(legrand_plant_id="casa-1", zone=ZONE)
    modulo_app.salva_config(cfg)
    return st, bt


def _attiva_automazione():
    from termopilota import app as modulo_app
    cfg = modulo_app.carica_config()
    cfg["automazione_attiva"] = True
    modulo_app.salva_config(cfg)


@pytest.mark.parametrize("url", ["/dispositivi", "/dispositivi/ac/ac-1", "/api/dispositivi/stato",
                                 "/api/dispositivi/ac/ac-1", "/api/dispositivi/stanza/stanza-1/storico"])
def test_richiedono_login(client, url):
    r = client.get(url)
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_pagine_dispositivi_con_navigazione(utente_client):
    for url in ("/dispositivi", "/dispositivi/ac/ac-1", "/dispositivi/stanza/stanza-1", "/dispositivi/casa/casa-1"):
        html = utente_client.get(url).get_data(as_text=True)
        assert 'class="tp-bottomnav"' in html, url
        assert html.count('aria-current="page"') == 2, url   # resta evidenziata "Automazione"


def test_tipo_di_dispositivo_sconosciuto(admin_client):
    assert admin_client.get("/dispositivi/frigo/1").status_code == 404


def test_stato_dispositivi(admin_client, finti):
    dati = admin_client.get("/api/dispositivi/stato").get_json()
    [ac] = dati["ac"]
    assert ac["nome"] == "Condizionatore Salotto" and ac["stato"]["umidita"] == 51
    assert ac["zone"] == ["Salotto"] and "grezzo" not in ac
    stanze = {s["id"]: s for s in dati["stanze"]}
    assert set(stanze) == {"stanza-1", "stanza-2"}          # 'esterno' non ha termostato
    assert stanze["stanza-2"]["finestra_aperta"] is True
    assert stanze["stanza-2"]["caldaia_accesa"] is True
    assert stanze["stanza-1"]["moduli"][0]["wifi"] == 69
    assert dati["casa"]["programma_attivo"] == "Inverno"
    assert stanze["stanza-1"]["target"] in (17, 20)          # dal programma, secondo l'ora


def test_dettaglio_ac_con_controlli_e_grezzo(admin_client, utente_client, finti):
    d = admin_client.get("/api/dispositivi/ac/ac-1").get_json()
    chiavi = [c["chiave"] for c in d["controlli"]]
    assert "ventola" in chiavi and "beep" in chiavi
    assert d["grezzo"]["powerConsumptionReport"]["powerConsumption"]["value"]["energy"] == 169612
    assert d["zone"][0]["nome"] == "Salotto"
    chiavi_utente = [c["chiave"] for c in utente_client.get("/api/dispositivi/ac/ac-1").get_json()["controlli"]]
    assert "ventola" in chiavi_utente and "beep" not in chiavi_utente


def test_dettaglio_stanza_e_casa(admin_client, finti):
    st = admin_client.get("/api/dispositivi/stanza/stanza-2").get_json()
    assert st["stato"]["umidita"] == 75 and st["moduli"][0]["firmware"] == 40
    assert st["grezzo"]["stanza"]["open_window"] is True
    casa = admin_client.get("/api/dispositivi/casa/casa-1").get_json()
    assert casa["stato"]["therm_setpoint_default_duration"] == 180
    assert "schedules" in casa["grezzo"]["dati"]
    assert admin_client.get("/api/dispositivi/ac/inesistente").status_code == 404


def test_comando_ac_valido(utente_client, finti):
    st, _ = finti
    r = utente_client.post("/api/dispositivi/ac/ac-1/comando", json={"chiave": "ventola", "valore": "low"})
    assert r.status_code == 200
    assert st.comandi == [("ac-1", "airConditionerFanMode", "setFanMode", ["low"])]
    assert r.get_json()["pausa"] is None        # automazione spenta: nessuna pausa


@pytest.mark.parametrize("corpo, codice", [
    ({"chiave": "ventola", "valore": "fortissima"}, 400),
    ({"chiave": "setpoint", "valore": 99}, 400),
    ({"chiave": "beep", "valore": "off"}, 403),
])
def test_comando_ac_rifiutato(utente_client, finti, corpo, codice):
    st, _ = finti
    assert utente_client.post("/api/dispositivi/ac/ac-1/comando", json=corpo).status_code == codice
    assert st.comandi == []


def test_comando_senza_json_rifiutato(admin_client, finti):
    r = admin_client.post("/api/dispositivi/ac/ac-1/comando", data={"chiave": "ventola", "valore": "low"})
    assert r.status_code == 415


def test_comando_fallito_sul_dispositivo(admin_client, finti):
    st, _ = finti
    st.esito = False
    r = admin_client.post("/api/dispositivi/ac/ac-1/comando", json={"chiave": "ventola", "valore": "low"})
    assert r.status_code == 502


def test_comando_mette_in_pausa_le_zone_automatizzate(admin_client, finti):
    _attiva_automazione()
    r = admin_client.post("/api/dispositivi/ac/ac-1/comando", json={"chiave": "accensione", "valore": "off"})
    pausa = r.get_json()["pausa"]
    assert pausa["zone"] == ["Salotto"] and pausa["fino"]
    assert "stanza-1" in admin_client.get("/api/automazione").get_json()["pause"]


def test_setpoint_stanza(utente_client, finti):
    _, bt = finti
    r = utente_client.post("/api/dispositivi/stanza/stanza-1/setpoint", json={"temp": 21.3, "durata_min": 60})
    assert r.status_code == 200
    room, modo, temp, fine = bt.comandi[0]
    assert (room, modo, temp) == ("stanza-1", "manual", 21.5) and fine == r.get_json()["fine"]
    fuori = utente_client.post("/api/dispositivi/stanza/stanza-1/setpoint", json={"temp": 40})
    assert fuori.status_code == 400


def test_ripristina_stanza(utente_client, finti):
    _, bt = finti
    assert utente_client.post("/api/dispositivi/stanza/stanza-1/ripristina", json={}).status_code == 200
    assert bt.comandi[0][:2] == ("stanza-1", "home")


def test_modalita_e_programma_della_casa(admin_client, utente_client, finti):
    _, bt = finti
    assert utente_client.post("/api/dispositivi/casa/modalita", json={"modalita": "away", "durata_min": 60}).status_code == 200
    assert bt.comandi[0][:2] == ("casa", "away")
    assert utente_client.post("/api/dispositivi/casa/modalita", json={"modalita": "vacanza"}).status_code == 400
    assert utente_client.post("/api/dispositivi/casa/programma", json={"schedule_id": "prog-inverno"}).status_code == 403
    assert admin_client.post("/api/dispositivi/casa/programma", json={"schedule_id": "prog-inverno"}).status_code == 200
    assert admin_client.post("/api/dispositivi/casa/programma", json={"schedule_id": "boh"}).status_code == 404


def test_zona_inclusa_ed_esclusa(utente_client, finti):
    r = utente_client.post("/api/automazione/zona/stanza-1/attiva", json={"attiva": False})
    assert r.get_json() == {"automazione": False}
    from termopilota import app as modulo_app
    assert modulo_app.carica_config()["zone"][0]["automazione"] is False
    assert utente_client.post("/api/automazione/zona/sconosciuta/attiva", json={"attiva": True}).status_code == 404


def test_pausa_e_ripresa_di_una_zona(utente_client, finti):
    r = utente_client.post("/api/automazione/zona/stanza-1/pausa", json={"ore": 1})
    assert r.get_json()["pausa_fino"]
    assert "stanza-1" in utente_client.get("/api/automazione").get_json()["pause"]
    utente_client.post("/api/automazione/zona/stanza-1/pausa", json={"ore": 0})
    assert utente_client.get("/api/automazione").get_json()["pause"] == {}
    assert utente_client.post("/api/automazione/zona/stanza-1/pausa", json={"ore": "x"}).status_code == 400


def test_dashboard_con_le_stanze(admin_client, finti):
    dati = admin_client.get("/api/dashboard").get_json()
    stanze = {z["room_id"]: z for z in dati["stanze"]["zone"]}
    assert stanze["stanza-1"]["ac"]["nome"] == "Condizionatore Salotto"
    assert stanze["stanza-1"]["umidita"] == 71
    assert stanze["stanza-2"]["finestra_aperta"] is True and stanze["stanza-2"]["ac"] is None
    assert dati["stanze"]["errore"] is None
    assert admin_client.get("/").status_code == 200


def test_storico_dispositivo(admin_client, finti):
    from termopilota import storico
    storico.registra_letture("2026-01-10T08:00", [{"tipo": "stanza", "id": "stanza-1", "t_ambiente": 19.0}])
    r = admin_client.get("/api/dispositivi/stanza/stanza-1/storico?da=2026-01-10&a=2026-01-10")
    assert r.get_json()["punti"][0]["t_ambiente"] == 19.0
    assert admin_client.get("/api/dispositivi/stanza/stanza-1/storico?risoluzione=boh").status_code == 400
    assert admin_client.get("/api/dispositivi/casa/casa-1/storico").status_code == 404


def test_letture_per_lo_storico(finti):
    from termopilota import app as modulo_app
    righe = {(r["tipo"], r["id"]): r for r in modulo_app._letture_dispositivi()}
    assert righe[("ac", "ac-1")]["energia_wh"] == 169612
    assert righe[("stanza", "stanza-2")]["attivo"] == 1
    assert righe[("stanza", "stanza-1")]["extra"]["target"] is not None


def test_config_zone_normalizzate(admin_client):
    admin_client.post("/api/config", json={"zone": [
        {"nome": "A", "room_id": "r1", "ac_device_id": "a1", "modalita": "boh", "riserva_gas_delta": 99,
         "offset_ac": -9, "extra": "ignorato"},
        "non una zona",
    ]})
    [zona] = admin_client.get("/api/config").get_json()["zone"]
    assert zona == {"nome": "A", "room_id": "r1", "ac_device_id": "a1", "automazione": True,
                    "modalita": "esclusiva", "riserva_gas_delta": 5.0, "offset_ac": -3.0}


def test_config_opzioni_ac(admin_client):
    admin_client.post("/api/config", json={"ac_ventola": "low", "ac_modalita_notte": "quiet",
                                           "notte_inizio": 30, "pausa_manuale_ore": 100,
                                           "automazione_simulazione": True})
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["ac_ventola"] == "low" and cfg["ac_modalita_notte"] == "quiet"
    assert cfg["notte_inizio"] == 23 and cfg["pausa_manuale_ore"] == 24.0
    assert cfg["automazione_simulazione"] is True
    admin_client.post("/api/config", json={"ac_ventola": "uragano"})
    assert admin_client.get("/api/config").get_json()["ac_ventola"] == "low"
