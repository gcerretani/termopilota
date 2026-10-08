# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scelta dell'impianto Netatmo: elenco impianti e Plant ID automatico."""

from termopilota import app as modulo_app


class _Client:
    def __init__(self, impianti, autenticato=True):
        self._impianti = impianti
        self.autenticato = autenticato

    def lista_impianti(self):
        return self._impianti


def test_impianti_senza_credenziali_dice_di_configurarle(monkeypatch):
    monkeypatch.setattr(modulo_app, "get_thermostat", lambda nome, cfg: None)
    r = modulo_app.scopri_impianti({})
    assert r["impianti"] == [] and "non configurate" in r["errori"][0]


def test_impianti_non_autorizzato(monkeypatch):
    monkeypatch.setattr(modulo_app, "get_thermostat", lambda nome, cfg: _Client([], autenticato=False))
    r = modulo_app.scopri_impianti({"legrand_client_id": "x"})
    assert "autorizzazione" in r["errori"][0]


def test_impianti_elencati(monkeypatch):
    monkeypatch.setattr(modulo_app, "get_thermostat",
                        lambda nome, cfg: _Client([{"id": "casa1", "name": "Casa"}]))
    r = modulo_app.scopri_impianti({"legrand_client_id": "x"})
    assert r["impianti"] == [{"id": "casa1", "name": "Casa"}] and r["errori"] == []


def test_plant_id_impostato_se_l_impianto_e_unico():
    assert modulo_app.imposta_plant_id_se_unico(_Client([{"id": "casa1", "name": "Casa"}])) == "casa1"
    assert modulo_app.carica_config()["legrand_plant_id"] == "casa1"


def test_plant_id_non_cambia_se_gia_presente():
    cfg = modulo_app.carica_config()
    cfg["legrand_plant_id"] = "scelto"
    modulo_app.salva_config(cfg)
    assert modulo_app.imposta_plant_id_se_unico(_Client([{"id": "casa1", "name": "Casa"}])) is None
    assert modulo_app.carica_config()["legrand_plant_id"] == "scelto"


def test_plant_id_non_scelto_se_gli_impianti_sono_piu_di_uno():
    impianti = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    assert modulo_app.imposta_plant_id_se_unico(_Client(impianti)) is None
    assert not modulo_app.carica_config().get("legrand_plant_id")
