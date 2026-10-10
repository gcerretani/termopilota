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


def test_ora_di_lettura_per_parte(admin_client, finti, monkeypatch):
    from termopilota import dispositivi
    from termopilota.app import carica_config
    ac = admin_client.get("/api/dispositivi/ac/ac-1").get_json()
    stanza = admin_client.get("/api/dispositivi/stanza/stanza-1").get_json()
    assert ac["letto_alle"] and stanza["letto_alle"]
    # Se Netatmo non risponde resta l'ora dell'ultima lettura riuscita
    _, bt = finti

    def giu(*a, **k):
        raise RuntimeError("Netatmo giu'")
    monkeypatch.setattr(bt, "stato_casa", giu)
    snap = dispositivi.snapshot(carica_config(), forza=True)
    assert snap["errori"] and snap["letti_alle"]["netatmo"] == stanza["letto_alle"]
    assert snap["letti_alle"]["ac"] >= ac["letto_alle"]


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
    tutte = modulo_app._letture_dispositivi()
    righe = {(r["tipo"], r["id"]): r for r in tutte if "tipo" in r}
    assert righe[("ac", "ac-1")]["energia_wh"] == 169612
    assert righe[("stanza", "stanza-2")]["attivo"] == 1
    assert righe[("stanza", "stanza-1")]["extra"]["target"] is not None
    # Le stesse misure, una riga per grandezza, nella tabella generica
    misure = {(r["sorgente"], r["id"], r["grandezza"]): r["valore"] for r in tutte if "sorgente" in r}
    assert misure[("ac", "ac-1", "energia")] == 169.612
    assert misure[("stanza", "stanza-2", "richiesta_calore")] == righe[("stanza", "stanza-2")]["extra"]["richiesta_calore_pct"]
    assert ("ac", "ac-1", "acceso") in misure and ("stanza", "stanza-1", "target") in misure


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


def test_termostato_offline_resta_visibile(admin_client, finti):
    # Regressione: homestatus omette stanza e modulo dei termostati offline e li
    # elenca in `errors` (codice 6); la stanza deve restare, non raggiungibile
    _, bt = finti
    bt.casa["dati"]["rooms"].append({"id": "stanza-3", "name": "Camera", "type": "bedroom",
                                     "module_ids": ["modulo-3"]})
    bt.casa["dati"]["modules"].append({"id": "modulo-3", "name": "Smarther", "type": "BNS",
                                       "room_id": "stanza-3"})
    bt.casa["errori"] = [{"code": 6, "id": "modulo-3"}]
    stanze = {s["id"]: s for s in admin_client.get("/api/dispositivi/stato").get_json()["stanze"]}
    assert stanze["stanza-3"]["raggiungibile"] is False
    assert "errore 6" in stanze["stanza-3"]["errore"]
    assert stanze["stanza-3"]["moduli"][0]["raggiungibile"] is False
    assert stanze["stanza-1"]["raggiungibile"] is True and stanze["stanza-1"]["errore"] is None
    d = admin_client.get("/api/dispositivi/stanza/stanza-3")
    assert d.status_code == 200 and d.get_json()["stato"]["temperatura_attuale"] is None
    from termopilota import dispositivi
    assert "stanza-3" not in {r["id"] for r in dispositivi.letture_per_storico(dispositivi.snapshot({}))}


def test_zona_reinclusa_aggiorna_subito_lo_stato(utente_client, finti):
    from termopilota.automazione import get_servizio
    servizio = get_servizio()
    servizio.stato_zone = [{"room_id": "stanza-1", "stato": "esclusa", "automazione": False,
                            "motivo": "Zona esclusa dall'automazione"}]
    servizio._sveglia.clear()
    utente_client.post("/api/automazione/zona/stanza-1/attiva", json={"attiva": True})
    [z] = utente_client.get("/api/automazione").get_json()["zone"]
    assert z["stato"] is None and z["automazione"] is True
    assert servizio._sveglia.is_set()    # il ciclo riparte senza aspettare l'intervallo


def test_rifiuto_netatmo_mostra_il_messaggio(utente_client, finti):
    from termopilota.providers.netatmo import ErroreNetatmo
    _, bt = finti
    bt.esito = ErroreNetatmo("Operation is forbidden", 13)
    r = utente_client.post("/api/dispositivi/stanza/stanza-1/setpoint", json={"temp": 20, "durata_min": 60})
    assert r.status_code == 502
    assert r.get_json()["errore"] == "Netatmo: Operation is forbidden (codice 13)"


def test_boost_stanza(utente_client, finti):
    _, bt = finti
    r = utente_client.post("/api/dispositivi/stanza/stanza-1/boost", json={"durata_min": 30})
    assert r.status_code == 200
    room, modo, _, fine = bt.comandi[0]
    assert (room, modo) == ("stanza-1", "max") and fine == r.get_json()["fine"]


def test_comandi_avanzati_solo_admin(admin_client, utente_client, finti):
    assert utente_client.get("/api/dispositivi/ac/ac-1/avanzati").status_code == 403
    assert utente_client.post("/api/dispositivi/ac/ac-1/avanzato",
                              json={"capability": "audioVolume", "comando": "setVolume",
                                    "argomenti": [10]}).status_code == 403
    comandi = admin_client.get("/api/dispositivi/ac/ac-1/avanzati").get_json()["comandi"]
    assert any(c["capability"] == "audioVolume" and c["comando"] == "setVolume" for c in comandi)
    assert not any(c["capability"] == "execute" for c in comandi)


def test_comando_avanzato_validato_e_registrato(admin_client, finti):
    st, _ = finti
    _attiva_automazione()
    r = admin_client.post("/api/dispositivi/ac/ac-1/avanzato",
                          json={"capability": "audioVolume", "comando": "setVolume", "argomenti": ["30"]})
    assert r.status_code == 200
    assert st.comandi[-1] == ("ac-1", "audioVolume", "setVolume", [30])
    assert r.get_json()["pausa"]["zone"] == ["Salotto"]
    log = admin_client.get("/api/automazione").get_json()["log"]
    assert log[0]["azione"] == "comando" and "audioVolume.setVolume" in log[0]["dettaglio"]
    assert "(admin)" in log[0]["dettaglio"]
    fuori = admin_client.post("/api/dispositivi/ac/ac-1/avanzato",
                              json={"capability": "audioVolume", "comando": "setVolume", "argomenti": [300]})
    assert fuori.status_code == 400
    escluso = admin_client.post("/api/dispositivi/ac/ac-1/avanzato",
                                json={"capability": "execute", "comando": "execute", "argomenti": ["x"]})
    assert escluso.status_code == 400


def test_coordinate_della_casa(admin_client, finti):
    _, bt = finti
    bt.casa["dati"]["coordinates"] = [11.25, 43.77]
    casa = admin_client.get("/api/dispositivi/stato").get_json()["casa"]
    assert casa["coordinate"] == {"lat": 43.77, "lon": 11.25}
