# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""IVA su gas e luce: il prezzo usato nel confronto e' (energia + altre voci) × (1 + IVA)."""

from unittest.mock import patch

import pytest

from termopilota import prezzi
from termopilota.raccomandazioni import prezzo_luce_effettivo

CFG_FISSA = {
    "gas_tariffa": "fissa", "gas_commodity_fisso_smc": 0.418, "gas_fisso_smc": 0.47,
    "luce_tariffa": "fissa", "luce_commodity_fisso_kwh": 0.09405, "luce_fisso_kwh": 0.109,
}


def test_senza_iva_il_totale_e_la_somma():
    p = prezzi.calcola_prezzi(CFG_FISSA)
    assert p["gas_totale_smc"] == pytest.approx(0.888, abs=1e-4)
    assert p["luce_totale_kwh"] == pytest.approx(0.20305, abs=1e-4)
    assert p["gas_iva_pct"] == 0 and p["luce_iva_pct"] == 0


def test_con_iva_il_totale_e_moltiplicato():
    p = prezzi.calcola_prezzi({**CFG_FISSA, "gas_iva_pct": 22, "luce_iva_pct": 10})
    assert p["gas_totale_smc"] == pytest.approx((0.418 + 0.47) * 1.22, abs=1e-4)
    assert p["luce_totale_kwh"] == pytest.approx((0.09405 + 0.109) * 1.10, abs=1e-4)
    assert p["gas_iva_pct"] == 22 and p["luce_iva_pct"] == 10


def test_l_iva_non_cambia_la_commodity_mostrata():
    p = prezzi.calcola_prezzi({**CFG_FISSA, "gas_iva_pct": 22})
    assert p["gas_commodity_smc"] == pytest.approx(0.418)


def test_iva_con_commodity_di_mercato():
    with patch.object(prezzi, "ottieni_ttf_eur_per_smc", return_value=0.40), \
            patch.object(prezzi, "ottieni_ttf_eur_per_mwh_raw", return_value=37.4):
        p = prezzi.calcola_prezzi({"gas_fisso_smc": 0.30, "gas_iva_pct": 22, "luce_iva_pct": 0})
    assert p["gas_fonte"] == "ttf_auto"
    assert p["gas_totale_smc"] == pytest.approx((0.40 + 0.30) * 1.22, abs=1e-4)


def test_il_prezzo_manuale_e_gia_lordo():
    with patch.object(prezzi, "ottieni_ttf_eur_per_smc", return_value=None), \
            patch.object(prezzi, "ottieni_ttf_eur_per_mwh_raw", return_value=None):
        p = prezzi.calcola_prezzi({"gas_totale_smc_manuale": 1.1, "gas_iva_pct": 22,
                                   "luce_totale_kwh_manuale": 0.25, "luce_iva_pct": 10})
    assert p["gas_fonte"] == "manuale" and p["gas_totale_smc"] == 1.1
    assert p["luce_fonte"] == "manuale" and p["luce_totale_kwh"] == 0.25


@pytest.mark.parametrize("valore,atteso", [("abc", 0), (None, 0), (-5, 0), (250, 100), ("22", 22)])
def test_iva_non_valida_e_riportata_nei_limiti(valore, atteso):
    p = prezzi.calcola_prezzi({**CFG_FISSA, "gas_iva_pct": valore})
    assert p["gas_iva_pct"] == atteso


def test_compensazione_materia_prima_azzera_anche_l_iva_sull_energia():
    totale = round((0.09405 + 0.109) * 1.10, 4)  # 0,2234
    p = {"luce_totale_kwh": totale, "luce_fisso_kwh": 0.109, "luce_iva_pct": 10}
    # coperta al 100%: restano le altre voci con la loro IVA
    assert prezzo_luce_effettivo(p, 1.0, "materia_prima") == pytest.approx(0.109 * 1.10, abs=1e-4)
    # coperta al 50%: meta' della materia prima con IVA
    assert prezzo_luce_effettivo(p, 0.5, "materia_prima") == pytest.approx(totale - 0.5 * 0.09405 * 1.10, abs=1e-4)


def test_compensazione_senza_iva_come_prima():
    p = {"luce_totale_kwh": 0.30, "luce_fisso_kwh": 0.14}
    assert prezzo_luce_effettivo(p, 1.0, "materia_prima") == pytest.approx(0.14)


def test_api_config_salva_e_limita_l_iva(admin_client):
    admin_client.post("/api/config", json={"gas_iva_pct": 22, "luce_iva_pct": 150})
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["gas_iva_pct"] == 22 and cfg["luce_iva_pct"] == 100
    admin_client.post("/api/config", json={"gas_iva_pct": -3})
    assert admin_client.get("/api/config").get_json()["gas_iva_pct"] == 0


def test_pagina_impostazioni_mostra_i_campi_iva(admin_client):
    html = admin_client.get("/admin/").get_data(as_text=True)
    assert 'id="gasIva"' in html and 'id="luceIva"' in html
    assert 'id="gasTotaleRiga"' in html and 'id="luceTotaleRiga"' in html
    assert "fixa" not in html.lower()
