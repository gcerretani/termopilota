# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime

from termopilota.raccomandazioni import calcola_raccomandazioni

CFG = {"efficienza_caldaia": 0.96, "temperatura_minima_ac": -10}


def previsioni(temperature):
    ore = [f"2026-01-15T{h:02d}:00" for h in range(len(temperature))]
    return {
        "hourly": {
            "time": ore,
            "temperature_2m": temperature,
            "apparent_temperature": [t - 1.5 for t in temperature],
            "precipitation_probability": [0] * len(temperature),
            "weathercode": [0] * len(temperature),
        }
    }


def prezzi(gas_smc=1.05, luce_kwh=0.26):
    return {"gas_totale_smc": gas_smc, "luce_totale_kwh": luce_kwh}


def test_ac_conviene_quando_fa_mite():
    # A 10°C il COP e' 4.80: 0.26/4.80 = 0.054 €/kWh_th contro
    # 1.05/(10.691*0.96) = 0.102 €/kWh_th della caldaia.
    ris = calcola_raccomandazioni(previsioni([10.0]), CFG, None, prezzi())
    assert ris[0]["raccomandazione"] == "ac"
    assert ris[0]["risparmio_pct"] > 0


def test_gas_conviene_con_luce_cara():
    ris = calcola_raccomandazioni(previsioni([10.0]), CFG, None, prezzi(luce_kwh=0.60))
    assert ris[0]["raccomandazione"] == "gas"


def test_gas_forzato_sotto_temperatura_minima_ac():
    # Anche con luce gratis, sotto la soglia il condizionatore non e' consigliato
    ris = calcola_raccomandazioni(previsioni([-12.0]), CFG, None, prezzi(luce_kwh=0.01))
    assert ris[0]["raccomandazione"] == "gas"
    assert ris[0]["risparmio_pct"] is None
    assert "troppo bassa" in ris[0]["motivo"]


def test_override_cfr_solo_su_ora_corrente():
    ora_corrente = datetime.now().strftime("%Y-%m-%dT%H:00")
    prev = previsioni([5.0, 5.0])
    prev["hourly"]["time"] = [ora_corrente, "2099-01-01T00:00"]
    ris = calcola_raccomandazioni(prev, CFG, 12.3, prezzi())
    assert ris[0]["fonte_temp"] == "cfr"
    assert ris[0]["temp_esterna"] == 12.3
    assert ris[1]["fonte_temp"] == "previsione"
    assert ris[1]["temp_esterna"] == 5.0


def test_costo_gas_dipende_da_efficienza():
    ris_alta = calcola_raccomandazioni(previsioni([5.0]), {**CFG, "efficienza_caldaia": 0.99}, None, prezzi())
    ris_bassa = calcola_raccomandazioni(previsioni([5.0]), {**CFG, "efficienza_caldaia": 0.80}, None, prezzi())
    assert ris_alta[0]["costo_gas_kwh"] < ris_bassa[0]["costo_gas_kwh"]
