"""
Fetching automatico prezzi energia per Edison World Luce + World Gas Plus

GAS:  TTF (Title Transfer Facility) da Yahoo Finance → proxy del PSV italiano
      PSV ≈ TTF con piccolo spread. Fonte: https://finance.yahoo.com (TTF=F)

LUCE: PUN (Prezzo Unico Nazionale) da ENTSO-E Transparency Platform
      Chiave API gratuita: https://transparency.entsoe.eu/usrm/user/createPublicUser
      Se non configurata, usa prezzo manuale.

Prezzi restituiti = commodity (auto) + componente_fissa (distribuzione+tasse, config 1 volta)
"""

import json
import time
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# ─── Conversioni ─────────────────────────────────────────────────────────────
KWH_PER_SMC = 10.691  # PCS metano (MISE Italia)
# TTF è quotato in EUR/MWh (contenuto energetico del gas)
# Per convertire in EUR/Smc: TTF_EUR_per_MWh × KWH_PER_SMC / 1000

# ─── Cache in memoria ────────────────────────────────────────────────────────
_cache: dict = {
    "ttf": {"valore": None, "timestamp": 0.0},
    "pun": {"valore": None, "timestamp": 0.0},
}
TTF_CACHE_TTL  = 3600   # 1 ora (TTF è giornaliero, non serve aggiornare spesso)
PUN_CACHE_TTL  = 3600 * 6  # 6 ore (PUN cambia durante la giornata ma si usa la media)


# ─── TTF — gas europeo da Yahoo Finance ──────────────────────────────────────

def _fetch_ttf_eur_per_mwh() -> Optional[float]:
    """
    Scarica il prezzo TTF (front-month) da Yahoo Finance.
    Restituisce EUR/MWh (contenuto energetico del gas naturale).
    """
    url = "https://query1.finance.yahoo.com/v8/finance/chart/TTF=F?interval=1d&range=5d"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        dati = json.loads(resp.read().decode())
        chiusure = dati["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        # Prende l'ultimo valore non None
        valori_validi = [x for x in chiusure if x is not None]
        if valori_validi:
            return round(valori_validi[-1], 3)
    except Exception:
        pass
    return None


def ottieni_ttf_eur_per_smc() -> Optional[float]:
    """
    Restituisce il prezzo TTF convertito in EUR/Smc (proxy per PSV italiano).
    Usa cache di 1 ora.
    """
    ora = time.time()
    cache = _cache["ttf"]
    if cache["valore"] and (ora - cache["timestamp"]) < TTF_CACHE_TTL:
        return cache["valore"]

    ttf_mwh = _fetch_ttf_eur_per_mwh()
    if ttf_mwh is not None:
        ttf_smc = round(ttf_mwh * KWH_PER_SMC / 1000, 4)
        cache["valore"] = ttf_smc
        cache["timestamp"] = ora
        return ttf_smc
    return None


def ottieni_ttf_eur_per_mwh_raw() -> Optional[float]:
    """Restituisce TTF in EUR/MWh (per visualizzazione)."""
    ora = time.time()
    cache = _cache["ttf"]
    if cache["valore"] and (ora - cache["timestamp"]) < TTF_CACHE_TTL:
        # ricalcola indietro da Smc a MWh
        return round(cache["valore"] / KWH_PER_SMC * 1000, 2)
    ttf_mwh = _fetch_ttf_eur_per_mwh()
    if ttf_mwh:
        cache["valore"] = round(ttf_mwh * KWH_PER_SMC / 1000, 4)
        cache["timestamp"] = ora
        return ttf_mwh
    return None


# ─── PUN — elettricità italiana da ENTSO-E ───────────────────────────────────

ENTSOE_BASE = "https://web-api.tp.entsoe.eu/api"
# Zona bidding Italia: 10YIT-GRTN-----B

def _fetch_pun_entsoe(token: str) -> Optional[float]:
    """
    Scarica i prezzi day-ahead italiani da ENTSO-E Transparency Platform.
    Restituisce la media giornaliera in EUR/MWh.

    token: chiave API gratuita da https://transparency.entsoe.eu/usrm/user/createPublicUser
    """
    ieri = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    params = (
        f"?securityToken={token}"
        f"&documentType=A44"
        f"&in_Domain=10YIT-GRTN-----B"
        f"&out_Domain=10YIT-GRTN-----B"
        f"&periodStart={ieri}0000"
        f"&periodEnd={ieri}2300"
    )
    try:
        req = urllib.request.Request(
            ENTSOE_BASE + params,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        xml_data = resp.read().decode("utf-8")

        # Parsa XML ENTSO-E
        root = ET.fromstring(xml_data)
        ns = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0"}

        prezzi = []
        for ts in root.findall(".//ns:TimeSeries", ns):
            for period in ts.findall(".//ns:Period", ns):
                for point in period.findall("ns:Point", ns):
                    price_el = point.find("ns:price.amount", ns)
                    if price_el is not None and price_el.text:
                        prezzi.append(float(price_el.text))

        if prezzi:
            return round(sum(prezzi) / len(prezzi), 3)  # media giornaliera EUR/MWh
    except Exception:
        pass
    return None


def ottieni_pun_eur_per_kwh(token: str) -> Optional[float]:
    """
    Restituisce il PUN in EUR/kWh (media del giorno precedente).
    Usa cache di 6 ore.
    """
    if not token or not token.strip():
        return None

    ora = time.time()
    cache = _cache["pun"]
    if cache["valore"] and (ora - cache["timestamp"]) < PUN_CACHE_TTL:
        return cache["valore"]

    pun_mwh = _fetch_pun_entsoe(token.strip())
    if pun_mwh is not None:
        pun_kwh = round(pun_mwh / 1000, 5)
        cache["valore"] = pun_kwh
        cache["timestamp"] = ora
        return pun_kwh
    return None


# ─── Calcolo prezzi finali ────────────────────────────────────────────────────

def calcola_prezzi(cfg: dict) -> dict:
    """
    Calcola i prezzi totali per gas e luce a partire da commodity + fisso.

    Restituisce un dict con:
      gas_commodity_smc   - EUR/Smc (commodity TTF, auto)
      gas_fisso_smc       - EUR/Smc (distribuzione+tasse, config)
      gas_totale_smc      - EUR/Smc (usato per confronto)
      luce_commodity_kwh  - EUR/kWh (PUN auto o None)
      luce_fisso_kwh      - EUR/kWh (distribuzione+tasse, config)
      luce_totale_kwh     - EUR/kWh (usato per confronto)
      gas_fonte           - "ttf_auto" | "manuale"
      luce_fonte          - "entsoe_auto" | "manuale"
      ttf_eur_mwh         - prezzo TTF raw in EUR/MWh (per display)
      pun_eur_mwh         - prezzo PUN raw in EUR/MWh (per display)
    """
    # ── Gas ──
    ttf_smc = ottieni_ttf_eur_per_smc()
    ttf_mwh = ottieni_ttf_eur_per_mwh_raw()

    gas_fisso = cfg.get("gas_fisso_smc", 0.35)

    if ttf_smc is not None:
        gas_commodity = ttf_smc
        gas_totale = round(gas_commodity + gas_fisso, 4)
        gas_fonte = "ttf_auto"
    else:
        gas_commodity = None
        gas_totale = cfg.get("gas_totale_smc_manuale", 0.95)
        gas_fonte = "manuale"

    # ── Luce ──
    entsoe_token = cfg.get("entsoe_token", "").strip()
    luce_fisso = cfg.get("luce_fisso_kwh", 0.14)

    pun_kwh = ottieni_pun_eur_per_kwh(entsoe_token) if entsoe_token else None
    pun_mwh = round(pun_kwh * 1000, 2) if pun_kwh else None

    if pun_kwh is not None:
        luce_commodity = pun_kwh
        luce_totale = round(luce_commodity + luce_fisso, 4)
        luce_fonte = "entsoe_auto"
    else:
        luce_commodity = None
        luce_totale = cfg.get("luce_totale_kwh_manuale", 0.27)
        luce_fonte = "manuale"

    return {
        "gas_commodity_smc":  round(gas_commodity, 4) if gas_commodity else None,
        "gas_fisso_smc":      gas_fisso,
        "gas_totale_smc":     gas_totale,
        "luce_commodity_kwh": round(pun_kwh, 5) if pun_kwh else None,
        "luce_fisso_kwh":     luce_fisso,
        "luce_totale_kwh":    luce_totale,
        "gas_fonte":          gas_fonte,
        "luce_fonte":         luce_fonte,
        "ttf_eur_mwh":        ttf_mwh,
        "pun_eur_mwh":        pun_mwh,
    }
