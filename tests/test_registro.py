# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registro eventi: scrittura e lettura con filtri, pulizia dei segreti,
ritenzione, gestore di logging, API e pagina, registrazione delle azioni."""

import json
import logging
import time

import pytest

from termopilota import registro


def test_scrivi_e_leggi_con_filtri():
    registro.scrivi("automazione", "AC acceso", oggetto="Salotto")
    registro.scrivi("comando", "Temperatura manuale 21", oggetto="Studio", utente="admin")
    registro.scrivi("sistema", "Errore di rete", livello="errore")
    registro.scrivi("evento", "ping", livello="debug")
    assert [r["messaggio"] for r in registro.leggi()] == ["Errore di rete", "Temperatura manuale 21", "AC acceso"]
    assert len(registro.leggi(livello_min="debug")) == 4
    assert [r["messaggio"] for r in registro.leggi(categorie=["comando"])] == ["Temperatura manuale 21"]
    assert [r["messaggio"] for r in registro.leggi(livello_min="warning")] == ["Errore di rete"]
    assert [r["oggetto"] for r in registro.leggi(oggetto="Salotto")] == ["Salotto"]
    assert [r["utente"] for r in registro.leggi(testo="manuale")] == ["admin"]
    assert registro.oggetti() == ["Salotto", "Studio"]


def test_prima_di_per_le_pagine():
    for i in range(5):
        registro.scrivi("evento", f"e{i}")
    prime = registro.leggi(limite=2)
    altre = registro.leggi(limite=10, prima_di=prime[-1]["ts"])
    assert [r["messaggio"] for r in prime] == ["e4", "e3"]
    assert all(r["ts"] < prime[-1]["ts"] for r in altre)


def test_dati_ripuliti_dai_segreti_e_troncati():
    registro.scrivi("evento", "webhook", dati={
        "access_token": "abc", "nested": {"client_secret": "x", "valore": 1, "lista": [{"Password": "p"}]}})
    [r] = registro.leggi()
    assert r["dati"] == {"access_token": "***", "nested": {"client_secret": "***", "valore": 1,
                                                          "lista": [{"Password": "***"}]}}
    registro.scrivi("evento", "grande", dati={"x": "a" * 20000})
    assert registro.leggi()[0]["dati"]["troncato"] is True


def test_categoria_e_livello_sconosciuti_normalizzati():
    registro.scrivi("boh", "x", livello="altissimo")
    [r] = registro.leggi()
    assert (r["categoria"], r["livello"]) == ("sistema", "info")


def test_scrivi_non_solleva_senza_db(monkeypatch):
    from termopilota import storico
    monkeypatch.setattr(storico, "DB_FILE", "/percorso/che/non/esiste/x.db")
    monkeypatch.setattr(storico.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    registro.scrivi("sistema", "non si salva")     # nessuna eccezione
    assert registro.leggi() == []


def test_ritenzione():
    adesso = time.time()
    with registro.storico._connetti() as conn:
        for giorni, livello in ((40, "info"), (10, "debug"), (10, "info"), (1, "debug")):
            conn.execute("INSERT INTO registro (ts, categoria, livello, messaggio) VALUES (?, 'evento', ?, ?)",
                         (adesso - giorni * 86400, livello, f"{giorni}-{livello}"))
    registro.pulisci(adesso)
    assert sorted(r["messaggio"] for r in registro.leggi(livello_min="debug")) == ["1-debug", "10-info"]


def test_gestore_di_logging_porta_warning_ed_errori():
    registro.collega_logging()
    registro.collega_logging()      # idempotente
    radice = logging.getLogger("termopilota")
    try:
        assert sum(isinstance(h, registro.GestoreLogRegistro) for h in radice.handlers) == 1
        logging.getLogger("termopilota.prova").warning("attenzione %s", 1)
        logging.getLogger("termopilota.prova").info("solo info")
        logging.getLogger("altro").error("non nostro")
    finally:
        for h in [h for h in radice.handlers if isinstance(h, registro.GestoreLogRegistro)]:
            radice.removeHandler(h)
    [r] = registro.leggi(categorie=["sistema"])
    assert r["messaggio"] == "attenzione 1" and r["livello"] == "warning"
    assert r["dati"]["modulo"] == "termopilota.prova"


# ── API e pagina ─────────────────────────────────────────────────────────────

def test_pagina_registro_evidenzia_automazione(utente_client):
    html = utente_client.get("/registro").get_data(as_text=True)
    assert 'id="paginaRegistro"' in html and html.count('aria-current="page"') == 2


def test_api_registro_nasconde_i_dati_ai_non_admin(admin_client, utente_client):
    registro.scrivi("evento", "webhook", oggetto="Salotto", dati={"room_id": "r1"})
    assert admin_client.get("/api/registro").get_json()["righe"][0]["dati"] == {"room_id": "r1"}
    d = utente_client.get("/api/registro?categorie=evento").get_json()
    assert d["righe"][0]["dati"] is None and d["oggetti"] == ["Salotto"]
    assert utente_client.get("/api/registro?prima_di=x").status_code == 400


def test_azioni_degli_utenti_registrate(utente_client, admin_client):
    from termopilota import app as modulo_app
    cfg = modulo_app.carica_config()
    cfg["zone"] = [{"nome": "Salotto", "room_id": "r1", "ac_device_id": ""}]
    modulo_app.salva_config(cfg)
    utente_client.post("/api/automazione/zona/r1/pausa", json={"ore": 1})
    utente_client.post("/api/automazione/zona/r1/attiva", json={"attiva": False})
    admin_client.post("/api/config", json={"efficienza_caldaia": 0.9, "legrand_client_secret": "nuovo"})
    righe = registro.leggi(categorie=["comando"])
    messaggi = [r["messaggio"] for r in righe]
    assert "Stanza in pausa per 1 h" in messaggi and "Stanza esclusa dall'automazione" in messaggi
    config = next(r for r in righe if r["messaggio"].startswith("Configurazione salvata"))
    assert "efficienza_caldaia" in config["messaggio"] and "legrand_client_secret" in config["messaggio"]
    assert "nuovo" not in json.dumps(config)              # mai i valori
    assert {r["utente"] for r in righe} == {"mario", "admin"}


def test_config_maschera_i_segreti(admin_client):
    admin_client.post("/api/config", json={"legrand_client_secret": "segretissimo", "entsoe_token": ""})
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["legrand_client_secret"] == "***" and cfg["entsoe_token"] == ""
    # Rimandare "***" non sovrascrive il segreto
    admin_client.post("/api/config", json={"legrand_client_secret": "***"})
    from termopilota import app as modulo_app
    assert modulo_app.carica_config()["legrand_client_secret"] == "segretissimo"


def test_polling_netatmo_in_configurazione(admin_client):
    from termopilota import app as modulo_app
    admin_client.post("/api/config", json={"netatmo_polling_secondi": 10})
    assert modulo_app.carica_config()["netatmo_polling_secondi"] == 60
    admin_client.post("/api/config", json={"netatmo_polling_secondi": 180})
    assert modulo_app.carica_config()["netatmo_polling_secondi"] == 180


@pytest.mark.parametrize("utente, codice", [("utente", 403), ("admin", 200)])
def test_rigenera_token_smartthings(utente_client, admin_client, utente, codice):
    from termopilota import app as modulo_app
    client = admin_client if utente == "admin" else utente_client
    cfg = modulo_app.carica_config()
    cfg["smartthings_webhook_token"] = "vecchio"
    modulo_app.salva_config(cfg)
    r = client.post("/api/live/smartthings/rigenera-token", json={})
    assert r.status_code == codice
    if codice == 200:
        nuovo = modulo_app.carica_config()["smartthings_webhook_token"]
        assert nuovo != "vecchio" and r.get_json()["smartthings_url"].endswith(nuovo)
        assert client.post("/api/webhook/smartthings/vecchio", json={"messageType": "PING"}).status_code == 404


def test_filtro_con_piu_oggetti(utente_client):
    registro.scrivi("evento", "a", oggetto="Salotto")
    registro.scrivi("evento", "b", oggetto="Condizionatore Salotto")
    registro.scrivi("evento", "c", oggetto="Studio")
    assert {r["messaggio"] for r in registro.leggi(oggetto=["Salotto", "Condizionatore Salotto"])} == {"a", "b"}
    d = utente_client.get("/api/registro?oggetto=Salotto&oggetto=Studio").get_json()
    assert {r["messaggio"] for r in d["righe"]} == {"a", "c"}
