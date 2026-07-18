"""
Motore di raccomandazione: per ogni ora delle previsioni decide se conviene
la caldaia a gas o la pompa di calore. Estratto da app.py per poterlo
testare senza avviare l'app Flask (l'import di app.py fa partire i servizi
in background).
"""

from datetime import datetime
from typing import Optional

from costanti import KWH_PER_SMC, interpola_cop

WMO_DESC = {
    0: "Sereno", 1: "Prevalentemente sereno", 2: "Parzialmente nuvoloso", 3: "Coperto",
    45: "Nebbia", 48: "Nebbia gelata",
    51: "Pioggerella leggera", 53: "Pioggerella moderata", 55: "Pioggerella intensa",
    61: "Pioggia leggera", 63: "Pioggia moderata", 65: "Pioggia intensa",
    71: "Neve leggera", 73: "Neve moderata", 75: "Neve intensa",
    80: "Rovesci leggeri", 81: "Rovesci moderati", 82: "Rovesci intensi",
    95: "Temporale", 96: "Temporale con grandine",
}
WMO_ICON = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️", 45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌧️", 61: "🌧️", 63: "🌧️", 65: "🌧️",
    71: "❄️", 73: "❄️", 75: "❄️", 80: "🌦️", 81: "🌧️", 82: "⛈️",
    95: "⛈️", 96: "⛈️",
}


def calcola_raccomandazioni(previsioni: dict, cfg: dict, temp_cfr: Optional[float], prezzi: dict) -> list:
    orario = previsioni["hourly"]
    gas_totale_smc = prezzi["gas_totale_smc"]
    luce_totale_kwh = prezzi["luce_totale_kwh"]
    eff = max(0.05, min(1.0, cfg.get("efficienza_caldaia") or 0.96))
    costo_gas_kwh = gas_totale_smc / (KWH_PER_SMC * eff)
    temp_min_ac = cfg.get("temperatura_minima_ac", -10)
    ora_corrente = datetime.now().strftime("%Y-%m-%dT%H:00")

    risultati = []
    for i, t in enumerate(orario["time"]):
        te = orario["temperature_2m"][i]
        tp = orario["apparent_temperature"][i]
        pp = orario["precipitation_probability"][i]
        wmo = orario["weathercode"][i]

        is_ora_corrente = (t == ora_corrente)
        if is_ora_corrente and temp_cfr is not None:
            te_calc = temp_cfr
            fonte_temp = "cfr"
        else:
            te_calc = te
            fonte_temp = "previsione"

        cop = interpola_cop(te_calc)
        costo_ac_kwh = luce_totale_kwh / cop

        if te_calc < temp_min_ac:
            raccomandazione = "gas"
            risparmio_pct = None
            motivo = f"Temp. troppo bassa per il condizionatore ({te_calc:.1f}°C)"
        elif costo_ac_kwh < costo_gas_kwh:
            raccomandazione = "ac"
            risparmio_pct = round((1 - costo_ac_kwh / costo_gas_kwh) * 100, 1)
            motivo = f"Condizionatore più economico — COP {cop:.1f}, risparmio {risparmio_pct:.0f}%"
        else:
            raccomandazione = "gas"
            risparmio_pct = round((1 - costo_gas_kwh / costo_ac_kwh) * 100, 1)
            motivo = f"Caldaia più economica — risparmio {risparmio_pct:.0f}% vs condizionatore"

        risultati.append({
            "ora": t,
            "temp_esterna": round(te_calc, 1),
            "temp_percepita": round(tp, 1),
            "pioggia_prob": pp,
            "meteo_desc": WMO_DESC.get(wmo, "—"),
            "meteo_icon": WMO_ICON.get(wmo, "🌡️"),
            "cop": cop,
            "costo_gas_kwh": round(costo_gas_kwh, 4),
            "costo_ac_kwh": round(costo_ac_kwh, 4),
            "raccomandazione": raccomandazione,
            "motivo": motivo,
            "risparmio_pct": risparmio_pct,
            "fonte_temp": fonte_temp,
        })

    return risultati
