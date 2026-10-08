"""
Stima della produzione del pannello adottato (Plenitude "Adotta un Pannello",
impianto di Cerrillares, Jumilla/Yecla, Murcia).

Plenitude non espone un'API pubblica: la produzione si stima dall'irraggiamento
orizzontale previsto da Open-Meteo (shortwave_radiation, W/m²):

    kW = potenza_kw × (irraggiamento / 1000) × fattore

`fattore` assorbe rendimento del sistema, perdite e guadagno degli inseguitori
solari; si calibra una volta confrontando la stima con la produzione che si
legge nell'app Plenitude (vedi calibra_fattore).
"""

import time
from datetime import datetime
from typing import Optional

import requests

CACHE_TTL = 1800
_cache: dict = {"chiave": None, "dati": None, "timestamp": 0.0}

IRRAGGIAMENTO_STC = 1000.0  # W/m², condizioni standard di test dei moduli


def stima_potenza_kw(irraggiamento_wm2: float, potenza_kw: float, fattore: float) -> float:
    """Potenza istantanea stimata in kW (mai negativa)."""
    return max(0.0, potenza_kw * (irraggiamento_wm2 / IRRAGGIAMENTO_STC) * fattore)


def calibra_fattore(produzione_app_kw: float, irraggiamento_wm2: float, potenza_kw: float) -> Optional[float]:
    """Fattore che fa coincidere la stima con la lettura dell'app.

    None se l'irraggiamento e' troppo basso per una calibrazione affidabile
    (di notte o con cielo molto coperto il rapporto e' dominato dal rumore).
    """
    if irraggiamento_wm2 < 200 or potenza_kw <= 0:
        return None
    return round(produzione_app_kw / (potenza_kw * irraggiamento_wm2 / IRRAGGIAMENTO_STC), 3)


def stima_giornata(serie_wm2: list, potenza_kw: float, fattore: float) -> list[dict]:
    """Serie oraria [{ora, irraggiamento_wm2, kw}] dalla serie di irraggiamento."""
    return [
        {
            "ora": ora,
            "irraggiamento_wm2": irr,
            "kw": round(stima_potenza_kw(irr, potenza_kw, fattore), 3),
        }
        for ora, irr in serie_wm2
    ]


def _scarica_irraggiamento(lat: float, lon: float) -> list:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=shortwave_radiation&forecast_days=2&timezone=Europe%2FRome"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    orario = resp.json()["hourly"]
    return list(zip(orario["time"], [v or 0.0 for v in orario["shortwave_radiation"]]))


def kw_per_ora(cfg: dict) -> Optional[dict]:
    """{ora: kW} stimati per oggi e domani, o None se non disponibili.

    Usato dal motore di raccomandazione: se Open-Meteo non risponde si
    prosegue senza compensazione, senza bloccare la dashboard.
    """
    if cfg.get("pannello_compensazione", "totale") == "nessuna":
        return None
    try:
        return {o["ora"]: o["kw"] for o in produzione_pannello(cfg)["serie"]}
    except Exception:
        return None


def produzione_pannello(cfg: dict) -> dict:
    """Stima corrente e di giornata per il pannello configurato.

    Solleva eccezione se Open-Meteo non e' raggiungibile e non c'e' cache.
    """
    lat = float(cfg.get("pannello_lat") or 38.5)
    lon = float(cfg.get("pannello_lon") or -1.2)
    potenza = float(cfg.get("pannello_potenza_kw") or 0.9)
    fattore = float(cfg.get("pannello_fattore") or 0.9)

    ora = time.time()
    chiave = (lat, lon)
    if _cache["chiave"] == chiave and _cache["dati"] and ora - _cache["timestamp"] < CACHE_TTL:
        serie = _cache["dati"]
    else:
        serie = _scarica_irraggiamento(lat, lon)
        _cache.update(chiave=chiave, dati=serie, timestamp=ora)

    ore = stima_giornata(serie, potenza, fattore)
    ora_corrente = datetime.now().strftime("%Y-%m-%dT%H:00")
    oggi = ora_corrente[:10]
    adesso = next((o for o in ore if o["ora"] == ora_corrente), None)
    return {
        "adesso_kw": adesso["kw"] if adesso else 0.0,
        "irraggiamento_wm2": adesso["irraggiamento_wm2"] if adesso else 0.0,
        # ogni punto vale 1 ora, quindi kW e kWh coincidono
        "oggi_kwh": round(sum(o["kw"] for o in ore if o["ora"].startswith(oggi)), 2),
        "serie": ore,
        "potenza_kw": potenza,
        "fattore": fattore,
        "stima": True,
    }
