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


def test_stato_stazioni_solo_moduli_esterni(http):
    [m] = _client(["read_smarther", "write_smarther", "read_station"]).stato_stazioni()
    assert http == [("getstationsdata", {"get_favorites": "false"})]
    assert m["id"] == MODULO and m["nome"] == "Esterno" and m["stazione"] == "Casa (Interno)"
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
    [m] = utente_client.get("/api/dispositivi/stato").get_json()["meteo"]
    assert m["id"] == MODULO and m["in_uso"] is True and "grezzo" not in m
    d = utente_client.get(f"/api/dispositivi/meteo/{MODULO}").get_json()
    assert d["nome"] == "Esterno" and d["stato"]["temperatura"] == 9.3 and d["stato"]["in_uso"] is True
    assert d["grezzo"]["dashboard_data"]["Humidity"] == 81 and d["letto_alle"]
    html = utente_client.get(f"/dispositivi/meteo/{MODULO}").get_data(as_text=True)
    assert "Stazione meteo" in html and 'id="controlli"' not in html
    assert utente_client.get("/api/dispositivi/meteo/nessuno").status_code == 404


def test_letture_e_storico_con_la_cfr_per_confronto(utente_client, finti, cfr):
    from termopilota import app as modulo_app
    from termopilota import storico
    righe = modulo_app._letture_dispositivi()
    meteo = {r["id"]: r for r in righe if r["tipo"] == "meteo"}
    assert meteo[MODULO]["t_ambiente"] == 9.3 and meteo[MODULO]["umidita"] == 81.0
    assert meteo["cfr:TOS01"]["t_ambiente"] == 11.0 and meteo["cfr:TOS01"]["nome"] == "Poggio"
    oggi = datetime.now().date().isoformat()
    storico.registra_letture(datetime.now().strftime("%Y-%m-%dT%H:%M"), righe)
    d = utente_client.get(f"/api/dispositivi/meteo/{MODULO}/storico?da={oggi}&a={oggi}").get_json()
    assert d["punti"][0]["t_ambiente"] == 9.3 and d["cfr"][0]["t_ambiente"] == 11.0


def test_campione_orario_con_fonte_netatmo(finti, cfr, monkeypatch):
    from termopilota import app as modulo_app
    # Le previsioni finte di conftest sono del 2099: serve una riga per l'ora corrente
    prev = modulo_app.scarica_previsioni(0, 0)
    prev["hourly"]["time"][0] = datetime.now().strftime("%Y-%m-%dT%H:00")
    monkeypatch.setattr(modulo_app, "scarica_previsioni", lambda lat, lon: prev)
    campione = modulo_app._campione_corrente()
    assert campione["fonte_temp"] == "netatmo" and campione["temp_esterna"] == 9.3


def test_credenziali_avvisano_dello_scope_mancante(admin_client):
    _imposta(legrand_token={"access_token": "t", "scope": ["read_smarther", "write_smarther"]})
    html = admin_client.get("/admin/credentials").get_data(as_text=True)
    assert "read_station" in html and "stazione meteo" in html
    _imposta(legrand_token={"access_token": "t", "scope": ["read_smarther", "write_smarther", "read_station"]})
    assert "Per leggere la <strong>stazione meteo" not in admin_client.get("/admin/credentials").get_data(as_text=True)
