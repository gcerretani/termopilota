# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pagina della stanza (zona): dati riuniti di termostato e condizionatore,
calcolo dei setpoint, storico combinato, gestione (crea, modifica, elimina),
differenza tra i sensori e correzione suggerita."""

import pytest

from dispositivi_finti import NetatmoFinto, SmartThingsFinto

ZONE = [{"nome": "Salotto", "room_id": "stanza-1", "ac_device_id": "ac-1", "offset_ac": 1.0},
        {"nome": "Ingresso", "room_id": "stanza-x", "ac_device_id": "ac-1"}]


@pytest.fixture
def finti(monkeypatch):
    from termopilota import app as modulo_app
    from termopilota import dispositivi, providers
    st, bt = SmartThingsFinto(), NetatmoFinto()
    for modulo in (providers, dispositivi, modulo_app):
        monkeypatch.setattr(modulo, "get_heatpump", lambda nome, cfg: st)
        monkeypatch.setattr(modulo, "get_thermostat", lambda nome, cfg: bt)
    monkeypatch.setattr(dispositivi, "setpoint_programmato", lambda casa, rid, adesso: 20.0)
    cfg = modulo_app.carica_config()
    cfg.update(legrand_plant_id="casa-1", zone=modulo_app.normalizza_zone(ZONE))
    modulo_app.salva_config(cfg)
    return st, bt


def _zone():
    from termopilota import app as modulo_app
    return modulo_app.carica_config()["zone"]


def test_pagina_stanza(client, utente_client, finti):
    assert client.get("/stanze/stanza-1").status_code == 302
    html = utente_client.get("/stanze/stanza-1").get_data(as_text=True)
    assert 'id="paginaStanza"' in html and "Salotto" in html
    assert html.count('aria-current="page"') == 2              # resta evidenziata la Home
    assert 'id="impostazioniStanza"' not in html                 # impostazioni solo admin
    assert utente_client.get("/stanze/sconosciuta").status_code == 404


def test_api_stanza_riunisce_termostato_e_condizionatore(utente_client, admin_client, finti):
    d = utente_client.get("/api/stanze/stanza-1").get_json()
    assert d["stanza"]["temperatura_attuale"] == 19.2 and d["ac"]["stato"]["temperatura_ambiente"] == 24
    assert d["calcolo"] == {"target": 20.0, "offset_ac": 1.0, "setpoint_ac_previsto": 21.0, "modalita": "esclusiva",
                            "riserva_gas_delta": 1.5, "setpoint_termostato_in_ac": 7.0, "condiviso_con": ["Ingresso"]}
    assert set(d["oggetti_registro"]) == {"Salotto", "Condizionatore Salotto"}
    assert "opzioni" not in d
    opzioni = admin_client.get("/api/stanze/stanza-1").get_json()["opzioni"]
    termostati = {t["id"]: t for t in opzioni["termostati"]}
    assert termostati["stanza-1"]["usata_da"] is None and termostati["stanza-2"]["usata_da"] is None
    assert opzioni["condizionatori"][0]["usato_da"] == ["Ingresso"]


def test_calcolo_in_modalita_affiancata(admin_client, finti):
    admin_client.post("/api/stanze/stanza-1", json={"modalita": "affiancata", "riserva_gas_delta": 2})
    c = admin_client.get("/api/stanze/stanza-1").get_json()["calcolo"]
    assert c["modalita"] == "affiancata" and c["setpoint_termostato_in_ac"] == 18.0


def test_affiancata_senza_margine(admin_client, finti):
    admin_client.post("/api/stanze/stanza-1", json={"modalita": "affiancata", "riserva_gas_delta": 0})
    c = admin_client.get("/api/stanze/stanza-1").get_json()["calcolo"]
    assert c["riserva_gas_delta"] == 0 and c["setpoint_termostato_in_ac"] == 20.0


def test_api_stanza_ha_l_ora_di_lettura_dei_due_dispositivi(utente_client, finti):
    d = utente_client.get("/api/stanze/stanza-1").get_json()
    assert {"ac", "netatmo"} <= set(d["letti_alle"]) and all(d["letti_alle"].values())


def test_storico_combinato(utente_client, finti):
    from termopilota import storico
    storico.registra_letture("2026-01-12T10:00", [
        {"tipo": "stanza", "id": "stanza-1", "nome": "Salotto", "t_ambiente": 19.0, "umidita": 60, "setpoint": 20,
         "attivo": 0, "modalita": "home", "energia_wh": None, "potenza_w": None, "extra": {}},
        {"tipo": "ac", "id": "ac-1", "nome": "AC", "t_ambiente": 21.0, "umidita": 50, "setpoint": 21,
         "attivo": 1, "modalita": "heat", "energia_wh": 1000, "potenza_w": None, "extra": {}}])
    d = utente_client.get("/api/stanze/stanza-1/storico?da=2026-01-12&a=2026-01-12").get_json()
    assert d["stanza"][0]["t_ambiente"] == 19.0 and d["ac"][0]["t_ambiente"] == 21.0
    assert utente_client.get("/api/stanze/stanza-1/storico?risoluzione=x").status_code == 400


def test_crea_stanza(admin_client, utente_client, finti):
    assert utente_client.post("/api/stanze", json={"room_id": "stanza-2"}).status_code == 403
    assert admin_client.post("/api/stanze", data="x").status_code == 415
    assert admin_client.post("/api/stanze", json={"room_id": "inesistente"}).status_code == 400
    assert admin_client.post("/api/stanze", json={"room_id": "stanza-1"}).status_code == 400   # gia' usata
    assert admin_client.post("/api/stanze", json={"room_id": "stanza-2", "ac_device_id": "boh"}).status_code == 400
    r = admin_client.post("/api/stanze", json={"room_id": "stanza-2"})
    assert r.get_json()["room_id"] == "stanza-2"
    nuova = _zone()[-1]
    assert nuova["nome"] == "Studio" and nuova["ac_device_id"] == "" and nuova["automazione"] is True
    from termopilota import registro
    assert registro.leggi(categorie=["comando"])[0]["messaggio"] == "Stanza creata"


def test_modifica_stanza_e_cambio_termostato(admin_client, finti, monkeypatch):
    from termopilota.automazione import get_servizio
    rilasci = []
    monkeypatch.setattr(get_servizio(), "rilascia_zona", lambda *a: rilasci.append(a))
    r = admin_client.post("/api/stanze/stanza-1", json={"nome": "Soggiorno", "offset_ac": 9})
    assert r.get_json()["zona"]["offset_ac"] == 3.0 and _zone()[0]["nome"] == "Soggiorno"
    assert rilasci == []
    r = admin_client.post("/api/stanze/stanza-1", json={"room_id": "stanza-2"})
    assert r.get_json()["room_id"] == "stanza-2" and _zone()[0]["room_id"] == "stanza-2"
    assert rilasci[0][0] == "stanza-1"
    assert admin_client.post("/api/stanze/stanza-2", json={"room_id": "stanza-x"}).status_code == 400


def test_elimina_stanza(admin_client, utente_client, finti, monkeypatch):
    from termopilota.automazione import get_servizio
    rilasci = []
    monkeypatch.setattr(get_servizio(), "rilascia_zona", lambda *a: rilasci.append(a))
    assert utente_client.delete("/api/stanze/stanza-1").status_code == 403
    assert admin_client.delete("/api/stanze/stanza-1").status_code == 200
    assert [z["room_id"] for z in _zone()] == ["stanza-x"]
    # L'AC resta acceso se lo usa ancora un'altra stanza
    assert rilasci == [("stanza-1", "ac-1", True)]
    assert admin_client.delete("/api/stanze/stanza-1").status_code == 404


def test_rilascio_di_una_stanza():
    from termopilota.automazione import rilascio_zona
    stato = {"stanze": {"r1": {"override": {"setpoint": 7, "fine": 1}, "pausa_fino": 5}, "r2": {}},
             "ac": {"ac-1": {"acceso_da_noi": True}}}
    piano = rilascio_zona(stato, "r1", "ac-1", ac_in_uso=False)
    assert piano["netatmo"] == [{"room_id": "r1", "zona": "r1", "tipo": "home"}]
    assert piano["ac"] == [{"device_id": "ac-1", "tipo": "spegni"}]
    assert "r1" not in piano["stato"]["stanze"] and "r2" in piano["stato"]["stanze"]
    assert rilascio_zona(stato, "r1", "ac-1", ac_in_uso=True)["ac"] == []


def test_opzioni_e_pagina_admin(admin_client, finti):
    d = admin_client.get("/api/stanze/opzioni").get_json()
    assert {t["id"]: t["usata_da"] for t in d["termostati"]} == {"stanza-1": "Salotto", "stanza-2": None}
    html = admin_client.get("/admin/zones").get_data(as_text=True)
    assert 'href="/stanze/stanza-1"' in html and 'id="creaStanza"' in html


# ── Differenza tra i sensori ─────────────────────────────────────────────────

def _letture(coppie, attivo=1, modalita="heat"):
    from datetime import datetime, timedelta
    from termopilota import storico
    adesso = datetime.now().replace(second=0, microsecond=0)
    for i, (t_stanza, t_ac) in enumerate(coppie):
        ts = (adesso - timedelta(minutes=15 * (i + 1))).strftime("%Y-%m-%dT%H:%M")
        storico.registra_letture(ts, [
            {"tipo": "stanza", "id": "r1", "nome": "S", "t_ambiente": t_stanza, "umidita": None, "setpoint": None,
             "attivo": 0, "modalita": "home", "energia_wh": None, "potenza_w": None, "extra": {}},
            {"tipo": "ac", "id": "a1", "nome": "A", "t_ambiente": t_ac, "umidita": None, "setpoint": 21,
             "attivo": attivo, "modalita": modalita, "energia_wh": None, "potenza_w": None, "extra": {}}])


def test_differenza_sensori_e_suggerimento():
    from termopilota import storico
    _letture([(19.0, 20.4)] * 4 + [(19.0, 20.6)] * 4)
    d = storico.differenza_sensori("r1", "a1")
    assert d == {"media": 1.5, "letture": 8, "suggerita": 1.5}


def test_differenza_sensori_poche_letture_o_ac_spento():
    from termopilota import storico
    _letture([(19.0, 21.0)] * 3)
    assert storico.differenza_sensori("r1", "a1")["suggerita"] is None
    assert storico.differenza_sensori("r1", "")["letture"] == 0


def test_differenza_sensori_ignora_raffrescamento_e_limita():
    from termopilota import storico
    _letture([(19.0, 30.0)] * 10, modalita="cool")
    assert storico.differenza_sensori("r1", "a1")["letture"] == 0
    _letture([(15.0, 25.0)] * 10)
    assert storico.differenza_sensori("r1", "a1")["suggerita"] == 3.0
