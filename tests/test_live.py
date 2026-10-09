# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggiornamenti live: webhook Netatmo (firma HMAC) e SmartThings (token
nell'URL, conferma, eventi), principio "notifica → rilettura", debounce e
comandi nostri recenti, configurazione admin."""

import hashlib
import hmac
import json

import pytest

from dispositivi_finti import NetatmoFinto, SmartThingsFinto

SEGRETO = "segreto-netatmo"
ZONE = [{"nome": "Salotto", "room_id": "stanza-1", "ac_device_id": "ac-1"}]


@pytest.fixture
def ambiente(monkeypatch):
    from termopilota import app as modulo_app
    from termopilota import automazione, dispositivi, live, providers
    st, bt = SmartThingsFinto(), NetatmoFinto()
    for modulo in (providers, dispositivi, modulo_app):
        monkeypatch.setattr(modulo, "get_heatpump", lambda nome, cfg: st)
        monkeypatch.setattr(modulo, "get_thermostat", lambda nome, cfg: bt)
    cfg = modulo_app.carica_config()
    cfg.update(legrand_plant_id="casa-1", legrand_client_secret=SEGRETO, zone=ZONE,
               automazione_attiva=True, smartthings_webhook_token="tok-segreto",
               smartthings_token_data={"installed_app_id": "app-installata"})
    modulo_app.salva_config(cfg)
    live.azzera()
    ricalcoli = []
    monkeypatch.setattr(automazione.get_servizio(), "ricalcola", lambda: ricalcoli.append(1))
    return st, bt, ricalcoli


def _firma(corpo: bytes) -> str:
    return hmac.new(SEGRETO.encode(), corpo, hashlib.sha256).hexdigest()


def _evento_netatmo(client, dati, firma=None):
    corpo = json.dumps(dati).encode()
    return client.post("/api/webhook/netatmo", data=corpo,
                       headers={"X-Netatmo-secret": firma if firma is not None else _firma(corpo)})


def test_webhook_netatmo_valido_rilegge_e_anticipa_il_ciclo(client, ambiente):
    from termopilota import live
    _, _, ricalcoli = ambiente
    r = _evento_netatmo(client, {"event_type": "set_point", "home_id": "casa-1", "room_id": "stanza-1"})
    assert r.status_code == 200
    s = live.stato()
    assert s["versione"] == 1 and s["netatmo"]["eventi"] == 1
    assert s["netatmo"]["ultimo"]["dispositivi"] == ["stanza-1"]
    assert ricalcoli == [1]
    # Debounce: un secondo evento subito dopo rilegge ma non riparte il ciclo
    _evento_netatmo(client, {"event_type": "set_point", "home_id": "casa-1", "room_id": "stanza-1"})
    assert live.stato()["versione"] == 2 and ricalcoli == [1]


def test_webhook_netatmo_firma_errata_rifiutato(client, ambiente):
    from termopilota import live
    r = _evento_netatmo(client, {"event_type": "set_point", "home_id": "casa-1"}, firma="0" * 64)
    assert r.status_code == 403
    assert client.post("/api/webhook/netatmo", data=b"{}").status_code == 403    # senza firma
    s = live.stato()
    assert s["versione"] == 0 and s["netatmo"]["rifiutati"] == 2


def test_webhook_netatmo_altra_casa_ignorato(client, ambiente):
    from termopilota import live
    assert _evento_netatmo(client, {"event_type": "therm_mode", "home_id": "altra"}).status_code == 200
    assert live.stato()["versione"] == 0


def test_evento_dopo_un_comando_nostro_non_anticipa_il_ciclo(client, ambiente):
    from termopilota import live
    _, _, ricalcoli = ambiente
    live.comando_nostro("stanza-1")
    _evento_netatmo(client, {"event_type": "set_point", "home_id": "casa-1", "room_id": "stanza-1"})
    assert live.stato()["versione"] == 1 and ricalcoli == []


def test_zona_esclusa_non_anticipa_il_ciclo(client, ambiente):
    from termopilota import app as modulo_app
    _, _, ricalcoli = ambiente
    cfg = modulo_app.carica_config()
    cfg["zone"] = [dict(ZONE[0], automazione=False)]
    modulo_app.salva_config(cfg)
    _evento_netatmo(client, {"event_type": "set_point", "home_id": "casa-1", "room_id": "stanza-1"})
    assert ricalcoli == []


def test_webhook_smartthings_token_errato(client, ambiente):
    assert client.post("/api/webhook/smartthings/sbagliato", json={"messageType": "PING"}).status_code == 404


def test_webhook_smartthings_conferma(client, ambiente, monkeypatch):
    from termopilota import app as modulo_app
    chiamate = []

    class Ok:
        def raise_for_status(self):
            return None
    monkeypatch.setattr(modulo_app.requests, "get", lambda url, timeout=None: chiamate.append(url) or Ok())
    url = "https://api.smartthings.com/confirm?token=x"
    r = client.post("/api/webhook/smartthings/tok-segreto",
                    json={"messageType": "CONFIRMATION", "confirmationData": {"confirmationUrl": url}})
    assert r.status_code == 200 and chiamate == [url]
    # Un URL di conferma fuori da smartthings.com non viene chiamato (niente SSRF)
    r = client.post("/api/webhook/smartthings/tok-segreto",
                    json={"messageType": "CONFIRMATION", "confirmationData": {"confirmationUrl": "http://127.0.0.1/"}})
    assert r.status_code == 400 and len(chiamate) == 1


def test_webhook_smartthings_ping(client, ambiente):
    r = client.post("/api/webhook/smartthings/tok-segreto",
                    json={"messageType": "PING", "pingData": {"challenge": "abc"}})
    assert r.get_json() == {"pingData": {"challenge": "abc"}}


def _evento_st(device_id, installata="app-installata"):
    return {"messageType": "EVENT", "eventData": {
        "installedApp": {"installedAppId": installata},
        "events": [{"eventType": "DEVICE_EVENT", "deviceEvent": {
            "deviceId": device_id, "componentId": "main", "capability": "thermostatCoolingSetpoint",
            "attribute": "coolingSetpoint", "value": 23}}]}}


def test_webhook_smartthings_evento(client, ambiente):
    from termopilota import live
    _, _, ricalcoli = ambiente
    assert client.post("/api/webhook/smartthings/tok-segreto", json=_evento_st("ac-1")).status_code == 200
    s = live.stato()
    assert s["smartthings"]["eventi"] == 1 and s["smartthings"]["ultimo"]["dispositivi"] == ["ac-1"]
    assert ricalcoli == [1]
    # Dispositivo sconosciuto: nessuna notifica
    client.post("/api/webhook/smartthings/tok-segreto", json=_evento_st("frigo"))
    assert live.stato()["smartthings"]["eventi"] == 1


def test_webhook_smartthings_altra_app_rifiutata(client, ambiente):
    from termopilota import live
    r = client.post("/api/webhook/smartthings/tok-segreto", json=_evento_st("ac-1", installata="altra"))
    assert r.status_code == 403 and live.stato()["smartthings"]["rifiutati"] == 1


def test_api_live_richiede_login(client, utente_client):
    assert client.get("/api/live").status_code == 302
    assert "versione" in utente_client.get("/api/live").get_json()


def test_configurazione_live_solo_admin(admin_client, utente_client, ambiente):
    assert utente_client.get("/api/live/configurazione").status_code == 403
    d = admin_client.get("/api/live/configurazione").get_json()
    assert d["smartthings_url"].endswith("/api/webhook/smartthings/tok-segreto")
    assert d["netatmo_url"].endswith("/api/webhook/netatmo")


def test_attiva_notifiche(admin_client, utente_client, ambiente):
    st, bt, _ = ambiente
    registrati, sottoscritti = [], []
    bt.registra_webhook = lambda url: registrati.append(url) or True
    st.installed_app_id = "app-installata"
    st.rimuovi_sottoscrizioni = lambda: sottoscritti.clear()
    st.sottoscrivi_dispositivo = lambda device_id: sottoscritti.append(device_id)
    st.sottoscrizioni = lambda: list(sottoscritti)
    assert utente_client.post("/api/live/netatmo/attiva", json={}).status_code == 403
    assert admin_client.post("/api/live/netatmo/attiva", json={}).status_code == 200
    assert registrati[0].endswith("/api/webhook/netatmo")
    r = admin_client.post("/api/live/smartthings/attiva", json={})
    assert r.get_json()["sottoscrizioni"] == 1 and sottoscritti == ["ac-1"]
    assert admin_client.post("/api/live/boh/attiva", json={}).status_code == 404


def test_firma_webhook_netatmo():
    from termopilota.providers.netatmo import firma_webhook_valida
    corpo = b'{"a":1}'
    assert firma_webhook_valida(SEGRETO, corpo, _firma(corpo))
    assert firma_webhook_valida(SEGRETO, corpo, _firma(corpo).upper())
    assert not firma_webhook_valida(SEGRETO, corpo, _firma(b"altro"))
    assert not firma_webhook_valida("", corpo, _firma(corpo))
    assert not firma_webhook_valida(SEGRETO, corpo, None)


def test_webhook_netatmo_registrato_con_il_corpo(client, ambiente):
    from termopilota import registro
    _evento_netatmo(client, {"event_type": "set_point", "home_id": "casa-1", "room_id": "stanza-1",
                             "temperature": 21})
    [r] = registro.leggi(categorie=["evento"])
    assert r["messaggio"] == "Netatmo (webhook): set_point" and r["oggetto"] == "Salotto"
    assert r["dati"]["temperature"] == 21 and r["dati"]["sorgente"] == "webhook"
    # Rifiutato: avviso con i dettagli utili alla diagnosi (senza segreti)
    client.post("/api/webhook/netatmo", data=b'{"event_type":"webhook_activation"}',
                headers={"Content-Type": "application/json"})
    [avviso] = registro.leggi(categorie=["sistema"], livello_min="warning")
    assert "firma assente" in avviso["messaggio"]
    assert avviso["dati"]["header_firma"] is False and avviso["dati"]["corpo"]["event_type"] == "webhook_activation"


def test_stanza_da_campi_annidati():
    from termopilota.app import stanze_evento_netatmo
    assert stanze_evento_netatmo({"room_id": "a"}) == ["a"]
    assert stanze_evento_netatmo({"home": {"rooms": [{"id": "b"}, {"id": "c"}]}}) == ["b", "c"]
    assert stanze_evento_netatmo({"room": {"id": "d"}, "rooms": [{"id": "d"}]}) == ["d"]
    assert stanze_evento_netatmo({"event_type": "webhook_activation"}) == []


def test_webhook_smartthings_registra_i_valori(client, ambiente):
    from termopilota import registro
    client.post("/api/webhook/smartthings/tok-segreto", json=_evento_st("ac-1"))
    [r] = registro.leggi(categorie=["evento"])
    assert r["oggetto"] == "Condizionatore Salotto" and "coolingSetpoint 23" in r["messaggio"]
    assert r["dati"]["eventi"][0]["capability"] == "thermostatCoolingSetpoint"


def _evento_attributo(capability, attributo, valore, unita=None):
    return {"messageType": "EVENT", "eventData": {
        "installedApp": {"installedAppId": "app-installata"},
        "events": [{"eventType": "DEVICE_EVENT", "deviceEvent": {
            "deviceId": "ac-1", "componentId": "main", "capability": capability,
            "attribute": attributo, "value": valore, "unit": unita}}]}}


def test_smartthings_rumore_solo_debug_e_niente_aggiornamenti(client, ambiente):
    from termopilota import live, registro
    _, _, ricalcoli = ambiente
    client.post("/api/webhook/smartthings/tok-segreto",
                json=_evento_attributo("custom.autoCleaningMode", "progress", 30, "%"))
    assert registro.leggi(categorie=["evento"]) == []
    assert registro.leggi(categorie=["evento"], livello_min="debug")[0]["dati"]["tipo"] == "altro"
    assert live.stato()["versione"] == 0 and ricalcoli == []


def test_smartthings_misura_aggiorna_le_pagine_senza_ricalcolo(client, ambiente):
    from termopilota import live, registro
    _, _, ricalcoli = ambiente
    client.post("/api/webhook/smartthings/tok-segreto", json=_evento_attributo(
        "powerConsumptionReport", "powerConsumption", {"energy": 141081, "power": 0, "deltaEnergy": 0}))
    [r] = registro.leggi(categorie=["evento"], livello_min="debug")
    assert r["livello"] == "debug" and "141081 Wh (potenza 0 W)" in r["messaggio"]
    assert live.stato()["versione"] == 1 and ricalcoli == []


def _apici_spaiati(script: str) -> list:
    """Righe JS con un numero dispari di apici singoli non escapati, fuori da
    stringhe "..." e `...` (euristica: basta per gli script inline dei template)."""
    import re
    spaiate = []
    script = re.sub(r"\\.", "", script)                    # caratteri escapati
    script = re.sub(r"`[^`]*`", "``", script)              # template literal, anche su piu' righe
    for riga in script.splitlines():
        codice = re.sub(r'"[^"]*"', '""', riga)
        if "//" in codice and codice.count("'", 0, codice.index("//")) % 2 == 0:
            codice = codice[:codice.index("//")]
        if codice.count("'") % 2:
            spaiate.append(riga.strip())
    return spaiate


@pytest.mark.parametrize("url", ["/admin/credentials", "/admin/", "/admin/zones", "/registro", "/automazione", "/stanze/stanza-1"])
def test_script_inline_senza_apici_spaiati(admin_client, ambiente, url):
    # Regressione: "da quando l'app" in una stringa con apici singoli bloccava
    # tutti i pulsanti della pagina Credenziali
    import re
    risposta = admin_client.get(url)
    assert risposta.status_code == 200
    html = risposta.get_data(as_text=True)
    for script in re.findall(r"<script>(.*?)</script>", html, re.S):
        assert _apici_spaiati(script) == []


def test_stato_notifiche_netatmo_e_conferma(admin_client, client, ambiente):
    from termopilota import app as modulo_app
    from termopilota import live, registro
    _, bt, _ = ambiente
    bt.registra_webhook = lambda url: True
    bt.rimuovi_webhook = lambda: True
    stato = lambda: admin_client.get("/api/live/configurazione").get_json()["stato_netatmo"]
    assert stato()["attivo"] is False
    admin_client.post("/api/live/netatmo/attiva", json={})
    s = stato()
    assert s["attivo"] is True and s["dal"] and s["confermato"] is None
    # Netatmo conferma con webhook_activation: niente notifica ai dispositivi
    _evento_netatmo(client, {"push_type": "webhook_activation", "user_id": "u1"})
    assert stato()["confermato"] is not None
    assert live.stato()["versione"] == 0
    assert "confermato la registrazione" in registro.leggi(categorie=["evento"])[0]["messaggio"]
    admin_client.post("/api/live/netatmo/disattiva", json={})
    assert stato() == {"attivo": False, "dal": None, "confermato": None}
    assert modulo_app.carica_config()["netatmo_webhook"]["attivo"] is False


def test_conferma_netatmo_senza_stato_salvato_attiva(admin_client, client, ambiente):
    # Webhook registrato con una versione precedente: la conferma basta a mostrarlo attivo
    _evento_netatmo(client, {"push_type": "webhook_activation"})
    s = admin_client.get("/api/live/configurazione").get_json()["stato_netatmo"]
    assert s["attivo"] is True and s["confermato"] is not None


@pytest.mark.parametrize("sottoscrizioni, attesa", [
    ([{"device": {"deviceId": "ac-1"}}], {"attivo": True, "parziale": False, "sottoscritti": 1}),
    ([], {"attivo": False, "parziale": False, "sottoscritti": 0}),
    ([{"device": {"deviceId": "altro"}}], {"attivo": False, "parziale": False, "sottoscritti": 0}),
])
def test_stato_sottoscrizioni_smartthings(ambiente, sottoscrizioni, attesa):
    from termopilota import app as modulo_app
    st, _, _ = ambiente
    st.sottoscrizioni = lambda: sottoscrizioni
    s = modulo_app.stato_sottoscrizioni_smartthings(modulo_app.carica_config(), st)
    assert {k: s[k] for k in attesa} == attesa and s["condizionatori"] == 1


def test_stato_sottoscrizioni_non_leggibile(ambiente):
    from termopilota import app as modulo_app
    st, _, _ = ambiente

    def guasto():
        raise RuntimeError("SmartThings 401")
    st.sottoscrizioni = guasto
    s = modulo_app.stato_sottoscrizioni_smartthings(modulo_app.carica_config(), st)
    assert s["attivo"] is None and "401" in s["errore"]
