# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test di integrazione sulle route Flask: accesso, API, pagine e configurazione."""

import json
import os

import pytest

from termopilota import storico
RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAGINE_PROTETTE = ["/", "/storico", "/account", "/admin/", "/admin/credentials",
                   "/admin/zones", "/admin/users"]
API_PROTETTE = ["/api/dashboard", "/api/prezzi", "/api/dati", "/api/config",
                "/api/automazione", "/api/storico", "/api/risparmi", "/api/pannello",
                "/api/dispositivi/impianti"]


# ─── Accesso e autorizzazioni ────────────────────────────────────────────────

@pytest.mark.parametrize("url", PAGINE_PROTETTE + API_PROTETTE)
def test_senza_login_si_viene_rimandati_al_login(client, url):
    r = client.get(url)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_login_con_password_errata_fallisce(client):
    r = client.post("/login", data={"username": "admin", "password": "sbagliata"})
    assert r.status_code == 200  # ripropone il form
    assert client.get("/").status_code == 302


def test_login_corretto_apre_la_dashboard(admin_client):
    assert admin_client.get("/").status_code == 200


@pytest.mark.parametrize("next_url", ["https://evil.example/", "//evil.example/", "javascript:alert(1)"])
def test_login_non_segue_redirect_esterni(client, next_url):
    r = client.post("/login?next=" + next_url,
                    data={"username": "admin", "password": "password-admin-test"})
    assert r.status_code == 302
    assert r.headers["Location"] in ("/", "http://localhost/")


@pytest.mark.parametrize("url", ["/admin/", "/admin/credentials", "/admin/zones", "/admin/users"])
def test_pagine_admin_negate_ai_non_admin(utente_client, url):
    r = utente_client.get(url)
    assert r.status_code == 302
    assert "/admin" not in r.headers["Location"]


def test_non_admin_non_puo_modificare_la_config(utente_client):
    r = utente_client.post("/api/config", json={"efficienza_caldaia": 0.5})
    assert r.status_code == 403


def test_non_admin_non_puo_calibrare(utente_client):
    r = utente_client.post("/api/pannello/calibra", json={"produzione_kw": 0.5})
    assert r.status_code == 403


# ─── Pagine e risorse pubbliche ──────────────────────────────────────────────

def test_pagine_si_renderizzano(admin_client):
    for url in PAGINE_PROTETTE:
        r = admin_client.get(url)
        assert r.status_code == 200, url


def test_login_page_pubblica(client):
    assert client.get("/login").status_code == 200


def test_service_worker_pubblico_e_javascript(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["Content-Type"]


def test_manifest_pubblico_e_valido(client):
    r = client.get("/static/manifest.webmanifest")
    assert r.status_code == 200
    manifest = json.loads(r.data)
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    for icona in manifest["icons"]:
        assert client.get(icona["src"]).status_code == 200, icona["src"]


def test_service_worker_precache_punta_a_file_esistenti(client):
    # Se un solo URL del precache manca, cache.addAll fallisce e il service
    # worker non si installa: la PWA smette di funzionare senza dare errori.
    sw = open(os.path.join(os.path.join(RADICE, "src", "termopilota"), "static", "sw.js"), encoding="utf-8").read()
    blocco = sw.split("const PRECACHE = [")[1].split("];")[0]
    percorsi = [riga.strip().strip(",").strip("'") for riga in blocco.splitlines() if "'/static/" in riga]
    assert percorsi, "nessun asset nel precache"
    for percorso in percorsi:
        assert client.get(percorso).status_code == 200, percorso


def test_nessuna_dipendenza_da_cdn_nei_template():
    # L'app deve funzionare in LAN senza internet.
    for cartella, _, files in os.walk(os.path.join(os.path.join(RADICE, "src", "termopilota"), "templates")):
        for nome in files:
            testo = open(os.path.join(cartella, nome), encoding="utf-8").read()
            assert "cdn.jsdelivr.net" not in testo, nome
            assert "unpkg.com" not in testo, nome


# ─── Dashboard ───────────────────────────────────────────────────────────────

def test_api_dashboard_struttura(admin_client):
    dati = admin_client.get("/api/dashboard").get_json()
    for chiave in ("prezzi", "raccomandazioni", "attuale", "ore_gas_oggi",
                   "ore_ac_oggi", "errori", "generato_alle"):
        assert chiave in dati
    assert dati["errori"] == {"meteo": None, "cfr": None}
    assert len(dati["raccomandazioni"]) == 24


def test_api_dashboard_segnala_errore_meteo(admin_client, monkeypatch):
    from termopilota import app as modulo_app
    def guasto(lat, lon):
        raise RuntimeError("meteo non raggiungibile")
    monkeypatch.setattr(modulo_app, "scarica_previsioni", guasto)
    dati = admin_client.get("/api/dashboard").get_json()
    assert "meteo non raggiungibile" in dati["errori"]["meteo"]
    assert dati["raccomandazioni"] == []


def test_dashboard_mostra_badge_tariffa_fissa(admin_client):
    admin_client.post("/api/config", json={
        "gas_tariffa": "fissa", "gas_commodity_fisso_smc": 0.45,
        "luce_tariffa": "fissa", "luce_commodity_fisso_kwh": 0.12})
    html = admin_client.get("/").get_data(as_text=True)
    assert html.count(">fisso<") >= 2


# ─── Configurazione ──────────────────────────────────────────────────────────

def test_config_salva_e_rilegge(admin_client):
    r = admin_client.post("/api/config", json={"efficienza_caldaia": 0.9, "setpoint_interno": 20})
    assert r.get_json()["status"] == "ok"
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["efficienza_caldaia"] == 0.9
    assert cfg["setpoint_interno"] == 20


def test_config_clamp_di_sicurezza(admin_client):
    admin_client.post("/api/config", json={
        "efficienza_caldaia": 5, "potenza_termica_kw": 9999,
        "pannello_fattore": 50, "pompa_potenza_elettrica_kw": -3,
        "intervallo_controllo_minuti": 0})
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["efficienza_caldaia"] == 1.0
    assert cfg["potenza_termica_kw"] == 30.0
    assert cfg["pannello_fattore"] == 2.0
    assert cfg["pompa_potenza_elettrica_kw"] >= 0.1
    assert cfg["intervallo_controllo_minuti"] >= 1.0


def test_config_consumo_base_zero_resta_zero(admin_client):
    admin_client.post("/api/config", json={"consumo_base_kw": 0})
    assert admin_client.get("/api/config").get_json()["consumo_base_kw"] == 0.0


def test_config_ignora_valori_non_ammessi(admin_client):
    admin_client.post("/api/config", json={
        "gas_tariffa": "gratis", "pannello_compensazione": "boh"})
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["gas_tariffa"] == "variabile"
    assert cfg["pannello_compensazione"] == "totale"


def test_config_tariffa_fissa_accettata(admin_client):
    admin_client.post("/api/config", json={"luce_tariffa": "fissa", "luce_commodity_fisso_kwh": 0.12})
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["luce_tariffa"] == "fissa"
    assert cfg["luce_commodity_fisso_kwh"] == 0.12


def test_config_example_contiene_tutte_le_chiavi_di_default():
    from termopilota import app as modulo_app
    esempio = json.load(open(os.path.join(RADICE, "config.example.json"), encoding="utf-8"))
    mancanti = [k for k in modulo_app.DEFAULT_CONFIG if k not in esempio]
    assert mancanti == [], f"chiavi assenti da config.example.json: {mancanti}"


# ─── Storico e risparmi ──────────────────────────────────────────────────────

def _campione(ora, racc="ac"):
    return {"ora": ora, "temp_esterna": 4.0, "fonte_temp": "cfr", "cop": 3.5,
            "costo_gas_kwh": 0.105, "costo_ac_kwh": 0.075, "gas_totale_smc": 1.05,
            "luce_totale_kwh": 0.26, "raccomandazione": racc}


def test_api_storico_restituisce_i_campioni(admin_client):
    storico.registra_campione(_campione("2026-01-10T08:00"))
    r = admin_client.get("/api/storico?da=2026-01-10&a=2026-01-10&risoluzione=oraria")
    assert r.status_code == 200
    punti = r.get_json()["punti"]
    assert len(punti) == 1 and punti[0]["raccomandazione"] == "ac"


def test_api_storico_giornaliero_ha_il_risparmio_in_euro(admin_client):
    storico.registra_campione(_campione("2026-01-10T08:00"))
    r = admin_client.get("/api/storico?da=2026-01-10&a=2026-01-10&risoluzione=giornaliera")
    punto = r.get_json()["punti"][0]
    assert punto["ore_ac"] == 1 and punto["risparmio_eur"] > 0


@pytest.mark.parametrize("query", [
    "risoluzione=settimanale",
    "da=ieri&a=oggi",
    "da=2026-13-45",
])
def test_api_storico_rifiuta_parametri_invalidi(admin_client, query):
    assert admin_client.get("/api/storico?" + query).status_code == 400


def test_api_risparmi_struttura_e_vuoto(admin_client):
    dati = admin_client.get("/api/risparmi").get_json()
    assert dati["stagione_eur"] == 0.0
    for chiave in ("oggi_eur", "settimana_eur", "ore_ac_stagione", "potenza_kw", "inizio_stagione"):
        assert chiave in dati


# ─── Pannello ────────────────────────────────────────────────────────────────

def test_api_pannello_restituisce_la_stima(admin_client):
    r = admin_client.get("/api/pannello")
    assert r.status_code == 200
    dati = r.get_json()
    assert dati["stima"] is True and dati["potenza_kw"] == 0.9


def test_api_pannello_503_se_open_meteo_non_risponde(admin_client, monkeypatch):
    from termopilota import pannello
    def guasto(lat, lon):
        raise RuntimeError("rete assente")
    monkeypatch.setattr(pannello, "_scarica_irraggiamento", guasto)
    assert admin_client.get("/api/pannello").status_code == 503


def test_dashboard_funziona_anche_senza_dati_pannello(admin_client, monkeypatch):
    from termopilota import pannello
    def guasto(lat, lon):
        raise RuntimeError("rete assente")
    monkeypatch.setattr(pannello, "_scarica_irraggiamento", guasto)
    assert admin_client.get("/api/dashboard").status_code == 200
    assert admin_client.get("/").status_code == 200


def test_calibra_richiede_un_numero(admin_client):
    r = admin_client.post("/api/pannello/calibra", json={"produzione_kw": "abc"})
    assert r.status_code == 400


def test_calibra_rifiuta_irraggiamento_basso(admin_client, monkeypatch):
    from termopilota import pannello
    monkeypatch.setattr(pannello, "_scarica_irraggiamento", lambda lat, lon: [])
    r = admin_client.post("/api/pannello/calibra", json={"produzione_kw": 0.5})
    assert r.status_code == 409


def test_calibra_aggiorna_il_fattore(admin_client, monkeypatch):
    from termopilota import pannello
    from datetime import datetime
    ora = datetime.now().strftime("%Y-%m-%dT%H:00")
    monkeypatch.setattr(pannello, "_scarica_irraggiamento", lambda lat, lon: [(ora, 800.0)])
    admin_client.post("/api/config", json={"pannello_modello": "orizzontale"})
    r = admin_client.post("/api/pannello/calibra", json={"produzione_kw": 0.54})
    assert r.status_code == 200
    # 0.54 = 0.9 kW * 0.8 * fattore  ->  fattore 0.75
    assert abs(r.get_json()["pannello_fattore"] - 0.75) < 0.001
    assert admin_client.get("/api/config").get_json()["pannello_fattore"] == r.get_json()["pannello_fattore"]
