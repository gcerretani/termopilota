"""Configurazione pytest condivisa.

Le variabili d'ambiente vanno impostate PRIMA di importare l'app: i moduli
leggono la cartella dati all'import. In questo modo i test non toccano mai
i dati reali (data/) e non avviano thread in background ne' chiamate di rete.
"""

import os
import tempfile

_DATI_TEST = tempfile.mkdtemp(prefix="termopilota-test-")
os.environ["TERMOPILOTA_DATA_DIR"] = _DATI_TEST
os.environ["TERMOPILOTA_SENZA_SERVIZI"] = "1"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "password-admin-test"
os.environ["SECRET_KEY"] = "chiave-segreta-solo-per-i-test"

import pytest  # noqa: E402

PASSWORD_ADMIN = "password-admin-test"
PASSWORD_UTENTE = "password-utente-test"


@pytest.fixture(scope="session")
def app_flask():
    import app as modulo_app
    modulo_app.app.config["TESTING"] = True
    return modulo_app.app


@pytest.fixture(autouse=True)
def stato_pulito(app_flask, monkeypatch):
    """Config e storico puliti e nessuna rete, per ogni test."""
    import app as modulo_app
    import pannello
    import storico

    if os.path.exists(modulo_app.CONFIG_FILE):
        os.remove(modulo_app.CONFIG_FILE)
    monkeypatch.setattr(storico, "DB_FILE", os.path.join(_DATI_TEST, "storico-test.db"))
    if os.path.exists(storico.DB_FILE):
        os.remove(storico.DB_FILE)
    storico.inizializza_db()

    previsioni = {"hourly": {
        "time": [f"{d}T{h:02d}:00" for d in ("2099-01-01",) for h in range(24)],
        "temperature_2m": [6.0] * 24,
        "apparent_temperature": [4.5] * 24,
        "precipitation_probability": [0] * 24,
        "weathercode": [0] * 24,
    }}
    monkeypatch.setattr(modulo_app, "scarica_previsioni", lambda lat, lon: previsioni)
    monkeypatch.setattr(modulo_app, "scarica_temp_cfr", lambda station_id: None)
    monkeypatch.setattr(modulo_app, "calcola_prezzi", lambda cfg: _prezzi_finti(cfg))
    monkeypatch.setattr(pannello, "_scarica_irraggiamento",
                        lambda lat, lon: [("2099-01-01T12:00", 800.0)])
    pannello._cache.update(chiave=None, dati=None, timestamp=0.0)


def _prezzi_finti(cfg):
    return {
        "gas_commodity_smc": cfg.get("gas_commodity_fisso_smc") or 0.35,
        "gas_fisso_smc": 0.66,
        "gas_totale_smc": 1.05,
        "luce_commodity_kwh": cfg.get("luce_commodity_fisso_kwh") or 0.11,
        "luce_fisso_kwh": 0.14,
        "luce_totale_kwh": 0.26,
        "gas_fonte": "fisso" if cfg.get("gas_tariffa") == "fissa" else "ttf_auto",
        "luce_fonte": "fisso" if cfg.get("luce_tariffa") == "fissa" else "entsoe_auto",
        "ttf_eur_mwh": 32.7,
        "pun_eur_mwh": 110.0,
    }


def _accedi(client, username, password):
    return client.post("/login", data={"username": username, "password": password})


@pytest.fixture
def client(app_flask):
    return app_flask.test_client()


@pytest.fixture
def admin_client(app_flask):
    c = app_flask.test_client()
    risposta = _accedi(c, "admin", PASSWORD_ADMIN)
    assert risposta.status_code == 302
    return c


@pytest.fixture
def utente_client(app_flask):
    import auth
    auth.create_user("mario", PASSWORD_UTENTE, is_admin=False, email=None)
    c = app_flask.test_client()
    risposta = _accedi(c, "mario", PASSWORD_UTENTE)
    assert risposta.status_code == 302
    return c
