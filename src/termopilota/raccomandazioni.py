# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Motore di raccomandazione: per ogni ora delle previsioni decide se conviene
la caldaia a gas o la pompa di calore. Estratto da app.py per poterlo
testare senza avviare l'app Flask (l'import di app.py fa partire i servizi
in background).
"""

from datetime import datetime
from typing import Optional

from termopilota.costanti import KWH_PER_SMC, interpola_cop

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


def copertura_pannello(p_pannello_kw: float, p_ac_kw: float, p_base_kw: float) -> float:
    """Quota (0..1) dell'energia della pompa di calore coperta dal pannello.

    Il pannello compensa il consumo totale di casa nel quarto d'ora: l'energia
    prelevata dalla rete e' max(0, consumo - produzione). Il costo marginale
    della pompa e' la differenza di prelievo con e senza pompa accesa:

        prelievo(con)  = max(0, base + ac - pannello)
        prelievo(senza) = max(0, base - pannello)

    Esempio: pannello 0,8 kW, pompa 1,0 kW, base 0 -> prelievo 0,2 kW, quindi
    il pannello copre l'80% della pompa.
    """
    if p_ac_kw <= 0:
        return 0.0
    con = max(0.0, p_base_kw + p_ac_kw - p_pannello_kw)
    senza = max(0.0, p_base_kw - p_pannello_kw)
    return max(0.0, min(1.0, 1.0 - (con - senza) / p_ac_kw))


def prezzo_luce_effettivo(prezzi: dict, copertura: float, modo: str) -> float:
    """€/kWh della pompa di calore dopo la compensazione del pannello.

    modo "totale":        la quota coperta non costa nulla (tutte le voci al kWh).
    modo "materia_prima": la quota coperta azzera solo la componente energia;
                          trasporto, oneri e tasse restano dovuti.
    """
    totale = prezzi["luce_totale_kwh"]
    if modo == "materia_prima":
        energia = max(0.0, totale - prezzi.get("luce_fisso_kwh", 0.0))
        return totale - copertura * energia
    return totale * (1.0 - copertura)


def calcola_raccomandazioni(previsioni: dict, cfg: dict, temp_cfr: Optional[float], prezzi: dict,
                            pannello_kw: Optional[dict] = None) -> list:
    """Raccomandazione oraria. `pannello_kw` e' {ora "YYYY-MM-DDTHH:00": kW}
    della produzione stimata del pannello adottato (None = nessuna compensazione)."""
    orario = previsioni["hourly"]
    modo_pannello = cfg.get("pannello_compensazione", "totale")
    p_ac_kw = max(0.1, float(cfg.get("pompa_potenza_elettrica_kw") or 1.2))
    base = cfg.get("consumo_base_kw")
    p_base_kw = max(0.0, float(0.3 if base is None else base))  # 0 e' un valore valido
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
        copertura = 0.0
        if pannello_kw and modo_pannello != "nessuna":
            copertura = copertura_pannello(pannello_kw.get(t, 0.0), p_ac_kw, p_base_kw)
        luce_effettiva = prezzo_luce_effettivo(prezzi, copertura, modo_pannello)
        costo_ac_kwh = luce_effettiva / cop

        if te_calc < temp_min_ac:
            raccomandazione = "gas"
            risparmio_pct = None
            motivo = f"Temp. troppo bassa per il condizionatore ({te_calc:.1f}°C)"
        elif costo_ac_kwh < costo_gas_kwh:
            raccomandazione = "ac"
            risparmio_pct = round((1 - costo_ac_kwh / costo_gas_kwh) * 100, 1)
            motivo = f"Condizionatore più economico — COP {cop:.1f}, risparmio {risparmio_pct:.0f}%"
            if copertura > 0:
                motivo += f" (pannello copre il {copertura * 100:.0f}% dei consumi)"
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
            "copertura_pannello_pct": round(copertura * 100),
        })

    return risultati
