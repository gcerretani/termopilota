# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dietro un reverse proxy gli URL assoluti devono usare lo schema e l'host pubblici."""

from flask import Flask, url_for

from termopilota.app import configura_proxy

HEADER = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "termopilota.example.org"}


def _app():
    flask_app = Flask(__name__)

    @flask_app.route("/x")
    def x():
        return url_for("x", _external=True)

    return flask_app


def test_senza_configurazione_gli_header_del_proxy_sono_ignorati(monkeypatch):
    monkeypatch.delenv("TERMOPILOTA_PROXY", raising=False)
    flask_app = _app()
    configura_proxy(flask_app)
    r = flask_app.test_client().get("/x", headers=HEADER)
    assert r.get_data(as_text=True) == "http://localhost/x"


def test_con_proxy_attivo_si_usano_schema_e_host_pubblici(monkeypatch):
    monkeypatch.setenv("TERMOPILOTA_PROXY", "1")
    flask_app = _app()
    configura_proxy(flask_app)
    r = flask_app.test_client().get("/x", headers=HEADER)
    assert r.get_data(as_text=True) == "https://termopilota.example.org/x"


def test_valore_non_numerico_equivale_a_spento(monkeypatch):
    monkeypatch.setenv("TERMOPILOTA_PROXY", "si")
    flask_app = _app()
    configura_proxy(flask_app)
    r = flask_app.test_client().get("/x", headers=HEADER)
    assert r.get_data(as_text=True) == "http://localhost/x"
