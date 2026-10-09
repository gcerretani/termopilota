# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Chiamate alle API dei dispositivi: conteggio, limite superato (pausa),
cache di homesdata, cache separate della fotografia (AC e Netatmo)."""

import pytest

from dispositivi_finti import NetatmoFinto, SmartThingsFinto
from termopilota import providers
from termopilota.providers import netatmo


class _Risposta:
    def __init__(self, status_code=200, corpo=None):
        self.status_code = status_code
        self.corpo = corpo if corpo is not None else {"status": "ok", "body": {"homes": [{"id": "casa-1"}],
                                                                               "home": {"rooms": []}}}
        self.text = str(self.corpo)

    def json(self):
        return self.corpo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def http(monkeypatch):
    """requests finto: registra gli endpoint chiamati, risposta configurabile."""
    registro = []
    stato = {"risposta": _Risposta()}

    def finto(metodo):
        def chiama(url, **_):
            registro.append((metodo, url.rsplit("/", 1)[-1].split("?")[0]))
            return stato["risposta"]
        return chiama
    for metodo in ("get", "post", "delete"):
        monkeypatch.setattr(providers.requests, metodo, finto(metodo))
    return registro, stato


def _client():
    return netatmo.NetatmoClient("id", "secret", {"access_token": "t", "_expires_at": 9e12})


def test_chiamate_contate(http):
    c = _client()
    c.stato_casa("casa-1")
    c.stato_casa("casa-1")
    # homesdata in cache: la seconda lettura chiede solo homestatus
    assert http[0] == [("get", "homesdata"), ("get", "homestatus"), ("get", "homestatus")]
    assert providers.conteggio_chiamate()["netatmo"] == 3


def test_homesdata_rinnovato_dopo_un_cambio_di_programma_e_nella_ricerca(http):
    c = _client()
    c.stato_casa("casa-1")
    c.cambia_programma("casa-1", "prog")
    c.stato_casa("casa-1")
    c.lista_impianti()          # configurazione: sempre dati freschi
    assert [e for _, e in http[0]].count("homesdata") == 3


@pytest.mark.parametrize("risposta", [
    _Risposta(429, {"error": {"code": 26, "message": "User usage reached"}}),
    _Risposta(403, {"error": {"code": 26, "message": "User usage reached"}}),
])
def test_limite_superato_mette_in_pausa(http, risposta):
    from termopilota import registro
    registro.collega_logging()
    try:
        http[1]["risposta"] = risposta
        with pytest.raises(providers.LimiteChiamate):
            _client().stato_casa("casa-1")
        chiamate_prima = len(http[0])
        with pytest.raises(providers.LimiteChiamate):     # in pausa: nessuna chiamata
            _client().stato_casa("casa-1")
        assert len(http[0]) == chiamate_prima
        assert "netatmo" in providers.conteggio_chiamate()["pausa_fino"]
        avvisi = registro.leggi(categorie=["sistema"], livello_min="warning")
        assert len(avvisi) == 1 and "limite di richieste superato" in avvisi[0]["messaggio"]
    finally:
        import logging
        radice = logging.getLogger("termopilota")
        for h in [h for h in radice.handlers if isinstance(h, registro.GestoreLogRegistro)]:
            radice.removeHandler(h)


def test_403_senza_codice_26_non_e_il_limite(http):
    http[1]["risposta"] = _Risposta(403, {"error": {"code": 13, "message": "Operation is forbidden"}})
    with pytest.raises(netatmo.ErroreNetatmo):
        _client().imposta_modalita("casa-1", "stanza-1", "home")
    assert providers.conteggio_chiamate()["pausa_fino"] == {}


# ── Fotografia: cache separate ───────────────────────────────────────────────

class _Contatore(NetatmoFinto):
    def __init__(self):
        super().__init__()
        self.letture = 0

    def stato_casa(self, home_id):
        self.letture += 1
        return super().stato_casa(home_id)


class _ContatoreST(SmartThingsFinto):
    def __init__(self):
        super().__init__()
        self.letture = 0

    def lista_dispositivi_ac(self):
        self.letture += 1
        return super().lista_dispositivi_ac()


@pytest.fixture
def fotografia(monkeypatch):
    from termopilota import app as modulo_app
    from termopilota import dispositivi
    st, bt = _ContatoreST(), _Contatore()
    for modulo in (providers, dispositivi, modulo_app):
        monkeypatch.setattr(modulo, "get_heatpump", lambda nome, cfg: st)
        monkeypatch.setattr(modulo, "get_thermostat", lambda nome, cfg: bt)
    return dispositivi, st, bt, {"legrand_plant_id": "casa-1"}


def test_evento_smartthings_non_rilegge_netatmo(fotografia):
    dispositivi, st, bt, cfg = fotografia
    dispositivi.snapshot(cfg)
    dispositivi.invalida("ac")
    snap = dispositivi.snapshot(cfg)
    assert (st.letture, bt.letture) == (2, 1)
    assert set(snap["stanze"]) == {"stanza-1", "stanza-2"}     # le stanze restano
    dispositivi.invalida("netatmo")
    dispositivi.snapshot(cfg)
    assert (st.letture, bt.letture) == (2, 2)
    dispositivi.snapshot(cfg)                                   # entrambe fresche: nessuna lettura
    assert (st.letture, bt.letture) == (2, 2)


def test_polling_aggiorna_la_cache_netatmo(fotografia):
    dispositivi, st, bt, cfg = fotografia
    dispositivi.snapshot(cfg)
    bt.casa["stato"]["rooms"][0]["therm_setpoint_temperature"] = 23
    dispositivi.aggiorna_netatmo(cfg)
    snap = dispositivi.snapshot(cfg)
    assert snap["stanze"]["stanza-1"]["setpoint"] == 23 and bt.letture == 2 and st.letture == 1


def test_configurazione_live_mostra_le_chiamate(admin_client, http):
    _client().stato_casa("casa-1")
    d = admin_client.get("/api/live/configurazione").get_json()
    assert d["chiamate"]["netatmo"] == 2
