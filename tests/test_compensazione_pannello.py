# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
from raccomandazioni import (
    calcola_raccomandazioni, copertura_pannello, prezzo_luce_effettivo,
)

# ─── Compensazione nel quarto d'ora ───────────────────────────────────────────


def test_esempio_utente_800w_prodotti_1000w_assorbiti():
    # Pannello 0,8 kW, pompa 1,0 kW, nessun altro carico: ne contano 0,2 kW,
    # quindi il pannello copre l'80% dell'energia della pompa.
    assert abs(copertura_pannello(0.8, 1.0, 0.0) - 0.8) < 1e-9


def test_pannello_che_supera_il_consumo_copre_tutto():
    assert copertura_pannello(0.9, 0.5, 0.0) == 1.0


def test_senza_produzione_nessuna_copertura():
    assert copertura_pannello(0.0, 1.2, 0.3) == 0.0


def test_consumo_base_assorbe_parte_della_produzione():
    # Pannello 0,8 kW, base 0,3 kW, pompa 1,0 kW:
    # prelievo con pompa = 0,5; senza pompa = 0 -> costo marginale 0,5 kW -> copertura 50%
    assert abs(copertura_pannello(0.8, 1.0, 0.3) - 0.5) < 1e-9


def test_base_superiore_alla_produzione_pompa_tutta_a_carico():
    # La base consuma gia' tutto quel che produce il pannello: la pompa paga tutto
    assert copertura_pannello(0.2, 1.0, 0.5) == 0.0


def test_prezzo_effettivo_totale():
    prezzi = {"luce_totale_kwh": 0.26, "luce_fisso_kwh": 0.14}
    assert abs(prezzo_luce_effettivo(prezzi, 0.8, "totale") - 0.052) < 1e-9


def test_prezzo_effettivo_solo_materia_prima():
    # Energia = 0.26 - 0.14 = 0.12; coperto l'80% -> sconto 0.096
    prezzi = {"luce_totale_kwh": 0.26, "luce_fisso_kwh": 0.14}
    assert abs(prezzo_luce_effettivo(prezzi, 0.8, "materia_prima") - (0.26 - 0.096)) < 1e-9


# ─── Effetto sulla raccomandazione ────────────────────────────────────────────

CFG = {"efficienza_caldaia": 0.96, "temperatura_minima_ac": -10,
       "pompa_potenza_elettrica_kw": 1.0, "consumo_base_kw": 0.0}
PREZZI = {"gas_totale_smc": 1.05, "luce_totale_kwh": 0.26, "luce_fisso_kwh": 0.14}


def previsioni_un_ora(temp, ora="2026-01-15T12:00"):
    return {"hourly": {
        "time": [ora], "temperature_2m": [temp], "apparent_temperature": [temp - 1.5],
        "precipitation_probability": [0], "weathercode": [0],
    }}


def test_pannello_ribalta_la_scelta_a_favore_della_pompa():
    # A 0°C COP 2.9: 0.26/2.9 = 0.090 €/kWh_th, caldaia 0.102 -> gia' AC.
    # Con luce piu' cara la caldaia vince, ma il pannello puo' ribaltare:
    prezzi = {**PREZZI, "luce_totale_kwh": 0.40}  # AC 0.138 > gas 0.102
    senza = calcola_raccomandazioni(previsioni_un_ora(0.0), CFG, None, prezzi)
    con = calcola_raccomandazioni(previsioni_un_ora(0.0), CFG, None, prezzi,
                                  {"2026-01-15T12:00": 0.8})
    assert senza[0]["raccomandazione"] == "gas"
    assert con[0]["raccomandazione"] == "ac"
    assert con[0]["copertura_pannello_pct"] == 80
    assert "pannello" in con[0]["motivo"]


def test_compensazione_nessuna_ignora_il_pannello():
    prezzi = {**PREZZI, "luce_totale_kwh": 0.40}
    cfg = {**CFG, "pannello_compensazione": "nessuna"}
    ris = calcola_raccomandazioni(previsioni_un_ora(0.0), cfg, None, prezzi,
                                  {"2026-01-15T12:00": 0.8})
    assert ris[0]["raccomandazione"] == "gas"
    assert ris[0]["copertura_pannello_pct"] == 0


def test_ore_senza_produzione_invariate():
    ris = calcola_raccomandazioni(previsioni_un_ora(5.0, "2026-01-15T22:00"), CFG, None, PREZZI,
                                  {"2026-01-15T12:00": 0.8})
    base = calcola_raccomandazioni(previsioni_un_ora(5.0, "2026-01-15T22:00"), CFG, None, PREZZI)
    assert ris[0]["costo_ac_kwh"] == base[0]["costo_ac_kwh"]
