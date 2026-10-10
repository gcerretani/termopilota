# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Temperatura esterna attuale: scelta tra modulo esterno Netatmo, stazione CFR
e previsione (priorita', eta' della misura), lettura della stazione meteo
Netatmo, dispositivo 'meteo' e letture per lo storico."""

from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from dispositivi_finti import NetatmoFinto, SmartThingsFinto, carica
from termopilota import temperatura_esterna as te
from termopilota.providers import netatmo

ADESSO = datetime(2026, 1, 10, 12, 0)
MODULO = "02:00:00:00:00:01"


def _misura(temp, minuti_fa, nome=None):
    return {"temp": temp, "ts": ADESSO - timedelta(minutes=minuti_fa), "nome": nome}


# ─── Scelta della fonte (pura) ───────────────────────────────────────────────

def test_vale_la_fonte_preferita():
    misure = {"netatmo": _misura(9.3, 5), "cfr": _misura(11.0, 10)}
    assert te.scegli(misure, te.ordine("netatmo"), 60, ADESSO)["fonte"] == "netatmo"
    assert te.scegli(misure, te.ordine("cfr"), 60, ADESSO)["fonte"] == "cfr"


def test_misura_vecchia_passa_alla_riserva():
    misure = {"netatmo": _misura(9.3, 90), "cfr": _misura(11.0, 20)}
    scelta = te.scegli(misure, te.ordine("netatmo"), 60, ADESSO)
    assert scelta["fonte"] == "cfr" and scelta["temp"] == 11.0


def test_nessuna_misura_valida():
    misure = {"netatmo": None, "cfr": _misura(11.0, 200)}
    assert te.scegli(misure, te.ordine("netatmo"), 60, ADESSO) is None


def test_misura_dal_futuro_non_vale():
    assert not te.valida(_misura(9.0, -30), 60, ADESSO)
    assert te.valida(_misura(9.0, -2), 60, ADESSO)     # piccolo scarto d'orologio


def test_ordine_con_fonte_sconosciuta():
    assert te.ordine("boh") == ["netatmo", "cfr"]
    assert te.ordine("cfr") == ["cfr", "netatmo"]


def test_motore_usa_la_fonte_indicata():
    from termopilota.raccomandazioni import calcola_raccomandazioni
    from test_raccomandazioni import CFG, prezzi, previsioni
    prev = previsioni([5.0])
    prev["hourly"]["time"] = [datetime.now().strftime("%Y-%m-%dT%H:00")]
    [riga] = calcola_raccomandazioni(prev, CFG, 9.3, prezzi(), fonte_attuale="netatmo")
    assert riga["fonte_temp"] == "netatmo" and riga["temp_esterna"] == 9.3


# ─── Client Netatmo: scope e getstationsdata ─────────────────────────────────

class _Risposta:
    status_code = 200

    def __init__(self, corpo):
        self.corpo = corpo

    def json(self):
        return self.corpo

    def raise_for_status(self):
        pass


@pytest.fixture
def http(monkeypatch):
    from termopilota import providers
    chiamate = []

    def get(url, **kw):
        chiamate.append((url.rsplit("/", 1)[-1], kw.get("params")))
        return _Risposta(carica("netatmo_stazione.json"))
    monkeypatch.setattr(providers.requests, "get", get)
    return chiamate


def _client(scope):
    return netatmo.NetatmoClient("id", "secret", {"access_token": "t", "_expires_at": 9e12, "scope": scope})


def test_url_di_autorizzazione_chiede_la_stazione():
    url = _client([]).url_autorizzazione("https://x/cb", "stato")
    scope = parse_qs(urlparse(url).query)["scope"][0].split()
    assert {"read_smarther", "write_smarther", "read_station"} <= set(scope)


def test_senza_scope_della_stazione_nessuna_chiamata(http):
    assert _client(["read_smarther", "write_smarther"]).stato_stazioni() == []
    assert http == []


def test_stato_stazioni_base_e_moduli(http):
    moduli = {m["id"]: m for m in _client(["read_smarther", "write_smarther", "read_station"]).stato_stazioni()}
    assert http == [("getstationsdata", {"get_favorites": "false"})]
    assert {i: (m["tipo"], m["esterno"]) for i, m in moduli.items()} == {
        "70:ee:50:00:00:01": ("NAMain", False), MODULO: ("NAModule1", True), "03:00:00:00:00:01": ("NAModule4", False)}
    m = moduli[MODULO]
    assert m["nome"] == "Esterno" and m["stazione"] == "Casa (Interno)"
    assert (m["temperatura"], m["umidita"], m["minima"], m["massima"]) == (9.3, 81.0, 6.8, 14.2)
    assert m["ts"] == 1791640460 and m["batteria_pct"] == 74 and m["raggiungibile"] is True


def test_modulo_senza_misure_non_raggiungibile():
    m = netatmo.normalizza_modulo_esterno({"_id": "x", "type": "NAModule1", "reachable": False}, {})
    assert m["temperatura"] is None and m["ts"] is None and m["raggiungibile"] is False


# ─── App: fonti, pagine e storico ────────────────────────────────────────────

@pytest.fixture
def finti(monkeypatch):
    from termopilota import app as modulo_app
    from termopilota import dispositivi, providers
    st, bt = SmartThingsFinto(), NetatmoFinto(stazione=carica("netatmo_stazione.json"))
    for modulo in (providers, dispositivi, modulo_app):
        monkeypatch.setattr(modulo, "get_heatpump", lambda nome, cfg: st)
        monkeypatch.setattr(modulo, "get_thermostat", lambda nome, cfg: bt)
    cfg = modulo_app.carica_config()
    cfg.update(legrand_plant_id="casa-1", cfr_station_id="TOS01", cfr_station_name="Poggio")
    modulo_app.salva_config(cfg)
    return st, bt


@pytest.fixture
def cfr(monkeypatch):
    """Stazione CFR finta: misura di 11 °C, `stato['minuti_fa']` minuti fa."""
    from termopilota import app as modulo_app
    stato = {"minuti_fa": 10}
    monkeypatch.setattr(modulo_app, "scarica_temp_cfr", lambda station_id: {
        "temp": 11.0, "ts": datetime.now() - timedelta(minutes=stato["minuti_fa"])})
    return stato


def _imposta(**valori):
    from termopilota import app as modulo_app
    cfg = modulo_app.carica_config()
    cfg.update(valori)
    modulo_app.salva_config(cfg)


def test_dashboard_usa_netatmo(utente_client, finti, cfr):
    dati = utente_client.get("/api/dashboard").get_json()
    assert dati["temp_info"]["fonte"] == "netatmo" and dati["temp_info"]["temp"] == 9.3
    assert dati["attuale"]["fonte_temp"] == "netatmo" and dati["attuale"]["temp_esterna"] == 9.3
    assert dati["errori"]["temp_esterna"] is None


def test_dashboard_con_priorita_cfr(utente_client, finti, cfr):
    _imposta(priorita_temp_esterna="cfr")
    dati = utente_client.get("/api/dashboard").get_json()
    assert dati["temp_info"]["fonte"] == "cfr" and dati["attuale"]["temp_esterna"] == 11.0


def test_netatmo_vecchia_ripiega_su_cfr(utente_client, finti, cfr):
    finti[1].eta_misura_min = 90
    dati = utente_client.get("/api/dashboard").get_json()
    assert dati["temp_info"]["fonte"] == "cfr"


def test_nessuna_misura_recente_usa_la_previsione(utente_client, finti, cfr):
    finti[1].eta_misura_min = 90
    cfr["minuti_fa"] = 120
    dati = utente_client.get("/api/dashboard").get_json()
    assert dati["temp_info"] is None
    assert dati["errori"]["temp_esterna"] == "nessuna misura recente"
    assert dati["attuale"]["fonte_temp"] == "previsione"


def test_api_temp_esterna(utente_client, finti, cfr):
    dati = utente_client.get("/api/temp-esterna").get_json()
    assert dati["scelta"]["fonte"] == "netatmo" and dati["priorita"] == ["netatmo", "cfr"]
    assert dati["fonti"]["netatmo"]["eta_minuti"] == 5 and dati["fonti"]["netatmo"]["valida"] is True
    assert dati["fonti"]["cfr"]["temp"] == 11.0 and dati["fonti"]["cfr"]["nome"] == "Poggio"


def test_config_temperatura_esterna(admin_client):
    admin_client.post("/api/config", json={"priorita_temp_esterna": "cfr", "temp_esterna_max_eta_minuti": 5,
                                           "meteo_modulo_id": MODULO})
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["priorita_temp_esterna"] == "cfr" and cfg["meteo_modulo_id"] == MODULO
    assert cfg["temp_esterna_max_eta_minuti"] == te.MAX_ETA_MIN
    admin_client.post("/api/config", json={"priorita_temp_esterna": "satellite"})
    assert admin_client.get("/api/config").get_json()["priorita_temp_esterna"] == "cfr"


def test_modulo_scelto_inesistente_non_vale(utente_client, finti, cfr):
    _imposta(meteo_modulo_id="altro")
    assert utente_client.get("/api/dashboard").get_json()["temp_info"]["fonte"] == "cfr"


def test_elenco_e_dettaglio_del_modulo_esterno(utente_client, finti):
    meteo = {m["id"]: m for m in utente_client.get("/api/dispositivi/stato").get_json()["meteo"]}
    assert len(meteo) == 3 and [i for i, m in meteo.items() if m["in_uso"]] == [MODULO]   # solo l'esterno
    assert "grezzo" not in meteo[MODULO]
    d = utente_client.get(f"/api/dispositivi/meteo/{MODULO}").get_json()
    assert d["nome"] == "Esterno" and d["stato"]["temperatura"] == 9.3 and d["stato"]["in_uso"] is True
    assert d["grezzo"]["dashboard_data"]["Humidity"] == 81 and d["letto_alle"]
    html = utente_client.get(f"/dispositivi/meteo/{MODULO}").get_data(as_text=True)
    assert "<title>Sensore" in html and 'id="controlli"' not in html
    assert utente_client.get("/api/dispositivi/meteo/nessuno").status_code == 404


def test_misure_e_serie_con_la_cfr_per_confronto(utente_client, finti, cfr):
    from termopilota import app as modulo_app
    from termopilota import storico
    righe = [r for r in modulo_app._letture_dispositivi() if "sorgente" in r]
    misure = {(r["sorgente"], r["id"], r["grandezza"]): r for r in righe}
    assert misure[("sensore", MODULO, "temperatura")]["valore"] == 9.3
    assert misure[("sensore", MODULO, "umidita")]["valore"] == 81.0
    assert misure[("sensore", "70:ee:50:00:00:01", "co2")]["unita"] == "ppm"      # la base, con la CO2
    assert misure[("cfr", "TOS01", "temperatura")]["valore"] == 11.0
    assert misure[("cfr", "TOS01", "temperatura")]["nome"] == "Poggio"
    storico.registra_misure(datetime.now().strftime("%Y-%m-%dT%H:%M"), righe)
    catalogo = {g["id"]: g for g in utente_client.get("/api/serie").get_json()["dispositivi"]}
    assert {"temperatura", "umidita", "minima", "massima", "batteria", "segnale_radio"} <= {
        s["grandezza"] for s in catalogo[MODULO]["serie"]}
    oggi = datetime.now().date().isoformat()
    d = utente_client.get(f"/api/serie/dati?s=sensore|{MODULO}|temperatura&s=cfr|TOS01|temperatura"
                          f"&da={oggi}&a={oggi}").get_json()
    assert [s["punti"][0]["valore"] for s in d["serie"]] == [9.3, 11.0]
    assert d["serie"][0]["unita"] == "°C" and d["serie"][1]["nome"] == "Poggio"


def test_campione_orario_con_fonte_netatmo(finti, cfr, monkeypatch):
    from termopilota import app as modulo_app
    # Le previsioni finte di conftest sono del 2099: serve una riga per l'ora corrente
    prev = modulo_app.scarica_previsioni(0, 0)
    prev["hourly"]["time"][0] = datetime.now().strftime("%Y-%m-%dT%H:00")
    monkeypatch.setattr(modulo_app, "scarica_previsioni", lambda lat, lon: prev)
    campione = modulo_app._campione_corrente()
    assert campione["fonte_temp"] == "netatmo" and campione["temp_esterna"] == 9.3


def test_credenziali_suggeriscono_lo_scope_senza_allarmi(admin_client):
    _imposta(legrand_token={"access_token": "t", "scope": ["read_smarther", "write_smarther"]})
    html = admin_client.get("/admin/credentials").get_data(as_text=True)
    assert "Facoltativo" in html and "read_station" in html and "alert-warning" not in html.split("Scopes:")[1][:600]
    _imposta(legrand_token={"access_token": "t", "scope": ["read_smarther", "write_smarther", "read_station"]})
    assert "Facoltativo: per usare anche" not in admin_client.get("/admin/credentials").get_data(as_text=True)


def test_senza_stazione_niente_avvisi_nelle_pagine(utente_client):
    for url in ("/", "/previsioni"):
        html = utente_client.get(url).get_data(as_text=True)
        assert "non disponibile" not in html and "alert-warning" not in html, url


# ─── Modulo esterno nella casa (homestatus), stanza 'outdoor' ────────────────

# Come lo restituisce homestatus: la stanza 'outdoor' non c'e', il modulo si'
MODULO_CASA = {"battery_level": 5812, "battery_state": "full", "bridge": "70:ee:50:b9:7c:a0",
               "firmware_revision": 53, "humidity": 90, "id": "02:00:00:b9:29:3c", "last_seen": 1791632902,
               "reachable": True, "rf_state": "medium", "rf_strength": 74, "temperature": 17.1,
               "ts": 1791632594, "type": "NAModule1"}


def _casa_con_stazione():
    casa = carica("netatmo_casa.json")
    casa["dati"]["rooms"] = [r for r in casa["dati"]["rooms"] if r["id"] != "esterno"] + [
        {"id": "esterno", "name": "Esterno", "type": "outdoor", "module_ids": [MODULO_CASA["id"]]}]
    casa["dati"]["modules"].append({"id": MODULO_CASA["id"], "type": "NAModule1", "name": "Modulo Esterno",
                                    "room_id": "esterno"})
    casa["dati"]["modules"].append({"id": "70:ee:50:b9:7c:a0", "type": "NAMain", "name": "Base",
                                    "room_id": "stanza-1"})
    casa["stato"]["modules"].append(MODULO_CASA)
    return casa


def test_stanza_esterno_e_stazione_non_sono_termostati(utente_client, monkeypatch):
    from termopilota import app as modulo_app
    from termopilota import dispositivi, providers
    bt = NetatmoFinto(casa=_casa_con_stazione())
    for modulo in (providers, dispositivi, modulo_app):
        monkeypatch.setattr(modulo, "get_thermostat", lambda nome, cfg: bt)
        monkeypatch.setattr(modulo, "get_heatpump", lambda nome, cfg: SmartThingsFinto())
    _imposta(legrand_plant_id="casa-1")
    dati = utente_client.get("/api/dispositivi/stato").get_json()
    assert dati["errori"] == [] and {s["id"] for s in dati["stanze"]} == {"stanza-1", "stanza-2"}
    assert all(m["tipo"] != "NAMain" for s in dati["stanze"] for m in s["moduli"])
    assert utente_client.get("/api/dispositivi/stanza/esterno").status_code == 404


def test_lista_moduli_senza_la_stanza_esterno(monkeypatch):
    c = _client(["read_smarther"])
    monkeypatch.setattr(c, "_homesdata", lambda forza=False: [_casa_con_stazione()["dati"]])
    assert "esterno" not in {m["id"] for m in c.lista_moduli("casa-1")}


def test_modulo_esterno_da_homestatus_se_getstationsdata_e_vuoto(monkeypatch):
    c = _client(["read_smarther", "write_smarther", "read_station"])
    casa = _casa_con_stazione()
    monkeypatch.setattr(netatmo, "chiamata", lambda *a, **k: _Risposta({"body": {"devices": []}}))
    monkeypatch.setattr(c, "_homesdata", lambda forza=False: [casa["dati"]])
    monkeypatch.setattr(c, "_homestatus", lambda home_id: casa["stato"] | {"home": casa["stato"]})
    [m] = c.stato_stazioni("casa-1")
    assert m["id"] == MODULO_CASA["id"] and m["nome"] == "Modulo Esterno"
    assert (m["temperatura"], m["umidita"], m["ts"], m["segnale_radio"]) == (17.1, 90.0, 1791632594, 74)
    assert m["batteria_pct"] == 92 and m["raggiungibile"] is True and m["minima"] is None


def test_senza_scope_il_modulo_arriva_comunque_da_homestatus(monkeypatch):
    c = _client(["read_smarther", "write_smarther"])
    casa = _casa_con_stazione()
    monkeypatch.setattr(c, "_homesdata", lambda forza=False: [casa["dati"]])
    monkeypatch.setattr(c, "_homestatus", lambda home_id: casa["stato"] | {"home": casa["stato"]})
    assert [m["temperatura"] for m in c.stato_stazioni("casa-1")] == [17.1]
    assert c.stato_stazioni() == []     # senza casa e senza scope non c'e' niente da leggere
