# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sensori della stazione meteo (ovunque Netatmo li metta) distinti dai termostati, tutte le
misure dei dispositivi nello storico generico e le serie per i grafici (Esplora)."""

from datetime import datetime, timedelta

import pytest

from dispositivi_finti import NetatmoFinto, SmartThingsFinto, carica
from termopilota import storico
from termopilota.providers import netatmo

BASE, ESTERNO, INTERNO = "70:ee:50:00:00:01", "02:00:00:00:00:01", "03:00:00:00:00:01"
ZONE = [{"nome": "Salotto", "room_id": "stanza-1", "ac_device_id": "ac-1"},
        {"nome": "Studio", "room_id": "stanza-2", "ac_device_id": ""}]


# ─── Misure dei moduli Netatmo ───────────────────────────────────────────────

def test_grandezze_da_getstationsdata_e_da_homestatus():
    # getstationsdata usa le maiuscole, homestatus le minuscole: stessa grandezza
    for misure in ({"Temperature": 23.1, "CO2": 872, "Noise": 38, "Pressure": 1010.1, "time_utc": 1},
                   {"temperature": 23.1, "co2": 872, "noise": 38, "pressure": 1010.1, "ts": 1}):
        g = {x["chiave"]: (x["valore"], x["unita"]) for x in netatmo.estrai_grandezze(misure)}
        assert g == {"temperatura": (23.1, "°C"), "co2": (872, "ppm"), "rumore": (38, "dB"),
                     "pressione": (1010.1, "hPa")}


def test_grandezze_vento_pioggia_e_sconosciute():
    g = netatmo.estrai_grandezze({"WindStrength": 12, "GustAngle": 200, "sum_rain_24": 3.4,
                                  "Luminosita": 400, "date_max_temp": 179, "firmware_revision": 300,
                                  "temp_trend": "up", "reachable": True, "battery_level": 5200})
    chiavi = [x["chiave"] for x in g]
    assert chiavi == ["vento", "direzione_raffica", "pioggia_24h", "luminosita"]   # note prima, poi le altre
    assert g[-1]["etichetta"] == "Luminosita" and g[-1]["unita"] == ""


def test_diagnostica_separata():
    g = {x["chiave"]: x["diagnostica"] for x in netatmo.estrai_grandezze(
        {"temperature": 20, "wifi_strength": 35, "rf_strength": 74}, {"battery_percent": 90})}
    assert g == {"temperatura": False, "segnale_wifi": True, "segnale_radio": True, "batteria": True}


# ─── Termostati e sensori nella casa ─────────────────────────────────────────

@pytest.fixture
def casa_stazione(monkeypatch):
    """Casa con la base della stazione nello studio (accanto al termostato), il modulo esterno
    in una stanza senza termostato e un modulo interno in salotto; misure di 5 minuti fa."""
    from termopilota import app as modulo_app
    from termopilota import dispositivi, providers
    st, bt = SmartThingsFinto(), NetatmoFinto(casa=carica("netatmo_casa_stazione.json"))
    for modulo in (providers, dispositivi, modulo_app):
        monkeypatch.setattr(modulo, "get_heatpump", lambda nome, cfg: st)
        monkeypatch.setattr(modulo, "get_thermostat", lambda nome, cfg: bt)
    cfg = modulo_app.carica_config()
    cfg.update(legrand_plant_id="casa-1", zone=ZONE)
    modulo_app.salva_config(cfg)
    return st, bt


def _imposta(**valori):
    from termopilota import app as modulo_app
    cfg = modulo_app.carica_config()
    cfg.update(valori)
    modulo_app.salva_config(cfg)


def test_termostati_e_sensori_distinti(utente_client, casa_stazione):
    dati = utente_client.get("/api/dispositivi/stato").get_json()
    stanze = {s["id"]: s for s in dati["stanze"]}
    assert set(stanze) == {"stanza-1", "stanza-2"}          # 'giardino' ha solo il modulo esterno
    assert all(m["tipo"] == "BNS" for s in stanze.values() for m in s["moduli"])
    sensori = {m["id"]: m for m in dati["meteo"]}
    assert {i: (m["tipo"], m["room_id"], m["stanza"]) for i, m in sensori.items()} == {
        BASE: ("NAMain", "stanza-2", "Studio"), ESTERNO: ("NAModule1", "giardino", "Giardino"),
        INTERNO: ("NAModule4", "stanza-1", "Salotto")}
    co2 = next(g for g in sensori[BASE]["grandezze"] if g["chiave"] == "co2")
    assert co2["valore"] == 872 and co2["unita"] == "ppm"
    assert [m["id"] for m in dati["meteo"] if m["in_uso"]] == [ESTERNO]     # l'unico modulo esterno


def test_sensori_nella_pagina_del_termostato_e_della_stanza(utente_client, casa_stazione):
    d = utente_client.get("/api/dispositivi/stanza/stanza-2").get_json()
    assert [m["tipo"] for m in d["moduli"]] == ["BNS"]
    assert [m["id"] for m in d["sensori"]] == [BASE] and "grezzo" not in d["sensori"][0]
    s = utente_client.get("/api/stanze/stanza-2").get_json()
    assert [m["id"] for m in s["sensori"]] == [BASE]
    assert utente_client.get("/api/stanze/stanza-1").get_json()["sensori"][0]["id"] == INTERNO
    html = utente_client.get("/dispositivi/stanza/stanza-2").get_data(as_text=True)
    assert 'id="sezioneSensori"' in html and "<title>Termostato" in html


def test_temperatura_esterna_da_un_sensore_scelto(utente_client, casa_stazione):
    assert utente_client.get("/api/temp-esterna").get_json()["fonti"]["netatmo"]["temp"] == 17.1
    _imposta(meteo_modulo_id=INTERNO)        # un sensore qualsiasi con una temperatura
    dati = utente_client.get("/api/temp-esterna").get_json()
    assert dati["scelta"]["fonte"] == "netatmo" and dati["scelta"]["temp"] == 19.8


def test_due_moduli_esterni_vanno_scelti(casa_stazione):
    from termopilota import dispositivi
    snap = {"meteo": {"a": {"id": "a", "esterno": True}, "b": {"id": "b", "esterno": True}}}
    assert dispositivi.modulo_meteo(snap) is None
    assert dispositivi.modulo_meteo(snap, "b")["id"] == "b"


def test_misure_per_storico_di_tutti_i_dispositivi(casa_stazione):
    from termopilota import app as modulo_app
    righe = [r for r in modulo_app._letture_dispositivi() if "sorgente" in r]
    misure = {(r["sorgente"], r["id"], r["grandezza"]): r for r in righe}
    assert misure[("sensore", BASE, "co2")]["valore"] == 872
    assert misure[("sensore", BASE, "pressione")]["etichetta"] == "Pressione"
    assert misure[("sensore", BASE, "segnale_wifi")]["valore"] == 35          # anche la diagnostica
    assert misure[("stanza", "stanza-2", "temperatura")]["unita"] == "°C"
    assert misure[("ac", "ac-1", "temperatura")]["nome"] == "Condizionatore Salotto"
    assert not any(k[0] == "sensore" and k[1].startswith("modulo-") for k in misure)   # i termostati no


# ─── Storico generico ────────────────────────────────────────────────────────

def _misura(sorgente, ident, grandezza, valore, nome="Dispositivo", unita="°C"):
    from termopilota.dispositivi import misura
    return misura(sorgente, ident, nome, grandezza, grandezza.capitalize(), unita, valore)


def test_registra_e_leggi_serie():
    storico.inizializza_db()
    for minuto, valore in ((0, 20.0), (15, 21.0), (30, 22.0), (45, 23.0)):
        storico.registra_misure(f"2026-01-10T08:{minuto:02d}", [_misura("sensore", BASE, "temperatura", valore)])
    storico.registra_misure("2026-01-10T09:00", [_misura("sensore", BASE, "temperatura", 30.0, nome="Base")])
    chiave = ("sensore", BASE, "temperatura")
    grezza = storico.leggi_serie([chiave], "2026-01-10", "2026-01-10", "grezza")[chiave]
    assert [p["valore"] for p in grezza] == [20.0, 21.0, 22.0, 23.0, 30.0]
    oraria = storico.leggi_serie([chiave], "2026-01-10", "2026-01-10", "oraria")[chiave]
    assert oraria == [{"periodo": "2026-01-10T08", "valore": 21.5}, {"periodo": "2026-01-10T09", "valore": 30.0}]
    [serie] = storico.catalogo_serie()
    assert (serie["nome"], serie["ultimo_ts"], serie["unita"]) == ("Base", "2026-01-10T09:00", "°C")


def test_serie_sistema_dalla_tabella_campioni():
    storico.inizializza_db()
    storico.registra_campione({"ora": "2026-01-10T08:00", "temp_esterna": 4.0, "fonte_temp": "netatmo", "cop": 3.5,
                               "costo_gas_kwh": 0.1, "costo_ac_kwh": 0.07, "gas_totale_smc": 1.0,
                               "luce_totale_kwh": 0.26, "raccomandazione": "ac"})
    catalogo = {s["grandezza"] for s in storico.catalogo_serie() if s["sorgente"] == "sistema"}
    assert {"temp_esterna", "cop", "costo_ac_kwh"} <= catalogo
    chiave = ("sistema", "termopilota", "cop")
    assert storico.leggi_serie([chiave], "2026-01-10", "2026-01-10")[chiave] == [
        {"periodo": "2026-01-10T08:00", "valore": 3.5}]


def test_oltre_90_giorni_restano_le_medie_orarie():
    storico.inizializza_db()
    vecchio = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%dT10")
    recente = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%dT10")
    for giorno in (vecchio, recente):
        for minuto, valore in ((0, 10.0), (15, 11.0), (30, 12.0), (45, 13.0)):
            storico.registra_misure(f"{giorno}:{minuto:02d}", [_misura("cfr", "TOS01", "temperatura", valore)])
    with storico._connetti() as conn:
        storico._compatta_misure(conn)
        storico._compatta_misure(conn)        # una seconda volta non cambia niente
    chiave = ("cfr", "TOS01", "temperatura")
    tutte = storico.leggi_serie([chiave], vecchio[:10], recente[:10])[chiave]
    assert [(p["periodo"][-5:], p["valore"]) for p in tutte] == [
        ("10:00", 11.5), ("10:00", 10.0), ("10:15", 11.0), ("10:30", 12.0), ("10:45", 13.0)]


def test_letture_meteo_della_170_passano_nelle_misure():
    storico.inizializza_db()
    storico.registra_letture("2026-01-10T08:15", [
        {"tipo": "meteo", "id": ESTERNO, "nome": "Esterno", "t_ambiente": 9.3, "umidita": 81},
        {"tipo": "meteo", "id": "cfr:TOS01", "nome": "Poggio", "t_ambiente": 11.0, "umidita": None},
        {"tipo": "stanza", "id": "stanza-1", "nome": "Salotto", "t_ambiente": 20.0},
    ])
    storico.inizializza_db()
    storico.inizializza_db()                  # idempotente
    s = storico.leggi_serie([("sensore", ESTERNO, "temperatura"), ("sensore", ESTERNO, "umidita"),
                             ("cfr", "TOS01", "temperatura")], "2026-01-10", "2026-01-10")
    assert [v[0]["valore"] for v in s.values()] == [9.3, 81.0, 11.0]
    assert {(c["sorgente"], c["id"], c["grandezza"]) for c in storico.catalogo_serie()} == set(s)
    assert storico.leggi_letture("stanza", "stanza-1", "2026-01-10", "2026-01-10")[0]["t_ambiente"] == 20.0
    assert storico.leggi_letture("meteo", ESTERNO, "2026-01-10", "2026-01-10") == []


# ─── API delle serie e pagina Esplora ────────────────────────────────────────

def test_api_serie_e_dati(utente_client):
    storico.inizializza_db()
    oggi = datetime.now().strftime("%Y-%m-%d")
    storico.registra_misure(f"{oggi}T08:00", [_misura("sensore", BASE, "co2", 872, nome="Base", unita="ppm")])
    [gruppo] = utente_client.get("/api/serie").get_json()["dispositivi"]
    assert gruppo["nome"] == "Base" and gruppo["serie"][0]["chiave"] == f"sensore|{BASE}|co2"
    d = utente_client.get(f"/api/serie/dati?s=sensore|{BASE}|co2&da={oggi}&a={oggi}").get_json()
    assert d["serie"][0]["punti"] == [{"periodo": f"{oggi}T08:00", "valore": 872.0}]
    assert d["serie"][0]["unita"] == "ppm"


@pytest.mark.parametrize("query", ["", "s=boh|x|y", "s=sensore|x|co2&da=10-01-2026",
                                   "s=sensore|x|co2&da=2026-01-10&a=2026-01-01",
                                   "s=sensore|x|co2&risoluzione=minuti",
                                   "&".join(f"s=sensore|x|g{i}" for i in range(9))])
def test_api_serie_dati_rifiuta_richieste_sbagliate(utente_client, query):
    storico.inizializza_db()
    storico.registra_misure("2026-01-10T08:00", [_misura("sensore", "x", "co2", 1)])
    assert utente_client.get(f"/api/serie/dati?{query}").status_code == 400


def test_api_serie_richiedono_login(client):
    for url in ("/api/serie", "/api/serie/dati?s=a|b|c"):
        assert client.get(url).status_code == 302


def test_pagina_storico_con_esplora(utente_client):
    html = utente_client.get("/storico").get_data(as_text=True)
    assert 'id="esplora"' in html and "js/esplora.js" in html
