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
