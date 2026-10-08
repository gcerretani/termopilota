# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import patch

from termopilota import prezzi
from termopilota.pannello import calibra_fattore, stima_giornata, stima_potenza_kw


# ─── Tariffe fisse / variabili ────────────────────────────────────────────────

CFG_BASE = {
    "gas_fisso_smc": 0.66,
    "luce_fisso_kwh": 0.117,
    "gas_totale_smc_manuale": 1.09,
    "luce_totale_kwh_manuale": 0.246,
}


def test_tariffa_fissa_non_interroga_i_mercati():
    cfg = {**CFG_BASE,
           "gas_tariffa": "fissa", "gas_commodity_fisso_smc": 0.45,
           "luce_tariffa": "fissa", "luce_commodity_fisso_kwh": 0.12}
    with patch.object(prezzi, "ottieni_ttf_eur_per_smc") as ttf, \
         patch.object(prezzi, "ottieni_pun_eur_per_kwh") as pun:
        p = prezzi.calcola_prezzi(cfg)
    ttf.assert_not_called()
    pun.assert_not_called()
    assert p["gas_fonte"] == "fisso"
    assert p["luce_fonte"] == "fisso"
    assert p["gas_totale_smc"] == round(0.45 + 0.66, 4)
    assert p["luce_totale_kwh"] == round(0.12 + 0.117, 4)
    assert p["ttf_eur_mwh"] is None and p["pun_eur_mwh"] is None


def test_tariffa_variabile_usa_ttf_e_pun():
    cfg = {**CFG_BASE, "entsoe_token": "x"}
    with patch.object(prezzi, "ottieni_ttf_eur_per_smc", return_value=0.35), \
         patch.object(prezzi, "ottieni_ttf_eur_per_mwh_raw", return_value=32.7), \
         patch.object(prezzi, "ottieni_pun_eur_per_kwh", return_value=0.11):
        p = prezzi.calcola_prezzi(cfg)
    assert p["gas_fonte"] == "ttf_auto"
    assert p["luce_fonte"] == "entsoe_auto"
    assert p["gas_totale_smc"] == round(0.35 + 0.66, 4)
    assert p["luce_totale_kwh"] == round(0.11 + 0.117, 4)


def test_tariffa_fissa_senza_prezzo_ricade_sul_variabile():
    # Prezzo bloccato dimenticato (0): meglio il mercato che un totale sbagliato
    cfg = {**CFG_BASE, "gas_tariffa": "fissa", "gas_commodity_fisso_smc": 0.0}
    with patch.object(prezzi, "ottieni_ttf_eur_per_smc", return_value=0.35), \
         patch.object(prezzi, "ottieni_ttf_eur_per_mwh_raw", return_value=32.7):
        p = prezzi.calcola_prezzi(cfg)
    assert p["gas_fonte"] == "ttf_auto"


def test_modalita_miste_gas_fisso_luce_variabile():
    cfg = {**CFG_BASE, "entsoe_token": "x",
           "gas_tariffa": "fissa", "gas_commodity_fisso_smc": 0.45}
    with patch.object(prezzi, "ottieni_pun_eur_per_kwh", return_value=0.11):
        p = prezzi.calcola_prezzi(cfg)
    assert p["gas_fonte"] == "fisso"
    assert p["luce_fonte"] == "entsoe_auto"


# ─── Stima pannello ───────────────────────────────────────────────────────────

def test_potenza_a_irraggiamento_standard():
    assert stima_potenza_kw(1000, 0.9, 1.0) == 0.9


def test_potenza_notte_zero_e_mai_negativa():
    assert stima_potenza_kw(0, 0.9, 0.9) == 0.0
    assert stima_potenza_kw(-5, 0.9, 0.9) == 0.0


def test_calibra_fattore_riproduce_la_lettura():
    fattore = calibra_fattore(0.63, 800, 0.9)
    assert abs(stima_potenza_kw(800, 0.9, fattore) - 0.63) < 0.01


def test_calibra_rifiuta_irraggiamento_basso():
    assert calibra_fattore(0.05, 100, 0.9) is None


def test_stima_giornata_ha_un_punto_per_ora():
    serie = [("2026-10-07T11:00", 600.0), ("2026-10-07T12:00", 700.0)]
    ore = stima_giornata(serie, 0.9, 0.9)
    assert [o["ora"] for o in ore] == ["2026-10-07T11:00", "2026-10-07T12:00"]
    assert ore[1]["kw"] > ore[0]["kw"]
