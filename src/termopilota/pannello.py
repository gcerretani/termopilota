# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Stima della produzione del pannello adottato (impianto di Cerrillares,
Jumilla/Yecla, Murcia).

Non c'e' un'API pubblica: la produzione si stima dall'irraggiamento previsto da
Open-Meteo. Due modelli, scelti con `pannello_modello`:

monoasse (predefinito)
    L'impianto usa inseguitori solari monoassiali con asse orizzontale nord-sud
    (avviso ufficiale della Regione di Murcia: "seguimiento horizontal a un
    eje"). Il modello ricostruisce cio' che arriva sul piano inclinato del
    pannello:

      1. posizione del sole (formule NOAA) al punto medio di ogni ora;
      2. angolo dell'inseguitore: segue il sole in direzione est-ovest, con
         backtracking (si inclina meno quando il sole e' basso, per non
         ombreggiare la fila vicina) e limite meccanico;
      3. irraggiamento sul piano del pannello: componente diretta (DNI per il
         coseno dell'angolo di incidenza), diffusa con il modello Hay-Davies
         (circumsolare + isotropa) e riflessa dal suolo;
      4. perdita per la temperatura delle celle (NOCT e coefficiente di
         potenza).

        kW = potenza_kWp × (POA / 1000) × fattore_temperatura × fattore

    Il `fattore` e' allora un vero rendimento di sistema (inverter, cavi,
    sporco, mismatch, bifacciale...) e resta circa 0,75-0,95 tutto l'anno: se la
    calibrazione dice molto di piu', e' sbagliata la potenza configurata.

orizzontale (modello originale)
    kW = potenza × (irraggiamento orizzontale / 1000) × fattore
    Il fattore deve assorbire anche il guadagno degli inseguitori, che cambia con
    la stagione e con l'ora: va ricalibrato spesso.

I valori orari di Open-Meteo sono medie dell'ora PRECEDENTE (l'etichetta 12:00
e' la media 11:00-12:00): qui ogni dato e' riferito all'inizio del suo intervallo,
cosi' la chiave "12:00" vale per l'ora 12:00-13:00 come le previsioni meteo.
"""

import math
import time
from datetime import datetime, timezone
from typing import NamedTuple, Optional

import requests

CACHE_TTL = 1800
_cache: dict = {"chiave": None, "dati": None, "timestamp": 0.0}

IRRAGGIAMENTO_STC = 1000.0  # W/m², condizioni standard di test dei moduli

# ── Parametri fisici del modello monoasse ────────────────────────────────────
COSTANTE_SOLARE = 1367.0  # W/m², irraggiamento extraatmosferico medio
ANGOLO_MAX_GRADI = 60.0   # corsa meccanica tipica degli inseguitori monoassiali
GCR = 0.35                # rapporto di copertura del suolo (moduli/file): ~0,3-0,4
ALBEDO = 0.20             # riflettanza del suolo
NOCT_DELTA = (45.0 - 20.0) / 800.0  # °C per W/m²: T cella = T aria + NOCT_DELTA × POA
COEFF_TEMPERATURA = -0.0035         # variazione di potenza per °C sopra 25 °C

FATTORE_MONOASSE_PREDEFINITO = 0.85
FATTORE_ORIZZONTALE_PREDEFINITO = 0.9
MODELLI = ("monoasse", "orizzontale")
SOGLIA_CALIBRAZIONE_WM2 = 200.0


class Punto(NamedTuple):
    """Un'ora di irraggiamento: media dell'intervallo [ts, ts + 1 h)."""
    ora: str                      # inizio intervallo, ora locale "YYYY-MM-DDTHH:00"
    ts: float                     # inizio intervallo, epoch UTC
    ghi: float                    # irraggiamento orizzontale globale, W/m²
    dni: Optional[float] = None   # diretta normale, W/m² (None: si ricava da ghi)
    dhi: Optional[float] = None   # diffusa orizzontale, W/m² (None: si ricava da ghi)
    temp: Optional[float] = None  # temperatura dell'aria, °C


# ─── Modello orizzontale (originale) ──────────────────────────────────────────

def stima_potenza_kw(irraggiamento_wm2: float, potenza_kw: float, fattore: float) -> float:
    """Potenza istantanea stimata in kW (mai negativa), modello orizzontale."""
    return max(0.0, potenza_kw * (irraggiamento_wm2 / IRRAGGIAMENTO_STC) * fattore)


def calibra_fattore(produzione_app_kw: float, irraggiamento_wm2: float, potenza_kw: float) -> Optional[float]:
    """Fattore che fa coincidere la stima orizzontale con la lettura dell'app.

    None se l'irraggiamento e' troppo basso per una calibrazione affidabile
    (di notte o con cielo molto coperto il rapporto e' dominato dal rumore).
    """
    if irraggiamento_wm2 < SOGLIA_CALIBRAZIONE_WM2 or potenza_kw <= 0:
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


# ─── Geometria solare e inseguitore ───────────────────────────────────────────

def vettore_solare(ts: float, lat_gradi: float, lon_gradi: float) -> tuple:
    """Direzione del sole in coordinate (est, nord, alto) e giorno dell'anno.

    Formule NOAA (accuratezza ~0,1°). `ts` e' un epoch UTC, `lon` e' positiva a
    est. Se la terza componente e' <= 0 il sole e' sotto l'orizzonte.
    """
    dt = datetime.fromtimestamp(ts, timezone.utc)
    giorno = dt.timetuple().tm_yday
    ore = dt.hour + dt.minute / 60 + dt.second / 3600
    g = 2 * math.pi / 365 * (giorno - 1 + (ore - 12) / 24)
    equazione_tempo = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                                - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tempo_solare = ore * 60 + equazione_tempo + 4 * lon_gradi  # minuti
    angolo_orario = math.radians(tempo_solare / 4 - 180)
    phi = math.radians(lat_gradi)
    est = -math.cos(decl) * math.sin(angolo_orario)
    nord = math.sin(decl) * math.cos(phi) - math.cos(decl) * math.sin(phi) * math.cos(angolo_orario)
    alto = math.sin(decl) * math.sin(phi) + math.cos(decl) * math.cos(phi) * math.cos(angolo_orario)
    return est, nord, alto, giorno


def angolo_inseguitore(est: float, alto: float, gcr: float = GCR,
                       max_gradi: float = ANGOLO_MAX_GRADI) -> float:
    """Rotazione (radianti, positiva verso est) di un inseguitore con asse
    orizzontale nord-sud: segue il sole nel piano est-alto, con backtracking.
    """
    if alto <= 0:
        return 0.0
    teta = math.atan2(est, alto)
    # Backtracking (Lorenzo et al., come in pvlib): quando le file si ombreggiano a
    # vicenda si riduce l'inclinazione fino al limite senza ombra.
    rapporto = abs(math.cos(teta)) / gcr
    if rapporto < 1.0:
        teta -= math.copysign(math.acos(rapporto), teta)
    limite = math.radians(max_gradi)
    return max(-limite, min(limite, teta))


def _scomponi_erbs(ghi: float, alto: float, e0: float) -> tuple:
    """(DNI, DHI) dalla sola GHI con la correlazione di Erbs: serve solo se la
    previsione non fornisce le componenti."""
    if alto <= 0 or ghi <= 0:
        return 0.0, max(0.0, ghi)
    kt = min(1.0, max(0.0, ghi / (e0 * max(alto, 0.01745))))
    if kt <= 0.22:
        frazione = 1.0 - 0.09 * kt
    elif kt <= 0.80:
        frazione = 0.9511 - 0.1604 * kt + 4.388 * kt ** 2 - 16.638 * kt ** 3 + 12.336 * kt ** 4
    else:
        frazione = 0.165
    dhi = frazione * ghi
    dni = min(e0, max(0.0, (ghi - dhi) / max(alto, 0.01745)))
    return dni, dhi


def poa_monoasse(ts_medio: float, lat: float, lon: float, ghi: float,
                 dni: Optional[float] = None, dhi: Optional[float] = None,
                 albedo: float = ALBEDO) -> float:
    """Irraggiamento (W/m²) sul piano dell'inseguitore monoassiale.

    Diretta + diffusa (Hay-Davies) + riflessa dal suolo, al punto medio
    dell'intervallo `ts_medio`.
    """
    est, _nord, alto, giorno = vettore_solare(ts_medio, lat, lon)
    if alto <= 0 or ghi <= 0:
        return 0.0
    e0 = COSTANTE_SOLARE * (1 + 0.033 * math.cos(2 * math.pi * giorno / 365))
    if dni is None or dhi is None:
        dni, dhi = _scomponi_erbs(ghi, alto, e0)
    teta = angolo_inseguitore(est, alto)
    cos_incidenza = max(0.0, est * math.sin(teta) + alto * math.cos(teta))
    cos_inclinazione = math.cos(teta)
    indice_anisotropia = min(1.0, max(0.0, dni / e0))
    rb = cos_incidenza / max(alto, 0.01745)
    diretta = dni * cos_incidenza
    diffusa = dhi * (indice_anisotropia * rb + (1 - indice_anisotropia) * (1 + cos_inclinazione) / 2)
    riflessa = ghi * albedo * (1 - cos_inclinazione) / 2
    return max(0.0, diretta + diffusa + riflessa)


def fattore_temperatura(poa_wm2: float, temp_aria: Optional[float]) -> float:
    """Moltiplicatore di potenza dovuto alla temperatura delle celle."""
    t_aria = 20.0 if temp_aria is None else temp_aria
    t_cella = t_aria + NOCT_DELTA * poa_wm2
    return max(0.0, 1.0 + COEFF_TEMPERATURA * (t_cella - 25.0))


def _poa_punto(p: Punto, lat: float, lon: float, modello: str) -> float:
    if modello == "orizzontale":
        return p.ghi
    return poa_monoasse(p.ts + 1800, lat, lon, p.ghi, p.dni, p.dhi)


def _kw_punto(p: Punto, poa: float, potenza: float, fattore: float, modello: str) -> float:
    if modello == "orizzontale":
        return stima_potenza_kw(p.ghi, potenza, fattore)
    return max(0.0, potenza * poa / IRRAGGIAMENTO_STC * fattore_temperatura(poa, p.temp) * fattore)


# ─── Dati di irraggiamento ────────────────────────────────────────────────────

def _normalizza(serie: list) -> list:
    """Accetta Punto o la vecchia forma (ora, irraggiamento) e restituisce Punto."""
    punti = []
    for voce in serie:
        if isinstance(voce, Punto):
            punti.append(voce)
        else:
            ora, ghi = voce[0], voce[1]
            ts = datetime.strptime(ora, "%Y-%m-%dT%H:%M").timestamp()
            punti.append(Punto(ora=ora, ts=ts, ghi=float(ghi)))
    return punti


def _scarica_irraggiamento(lat: float, lon: float) -> list:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation,temperature_2m"
        "&forecast_days=2&timeformat=unixtime&timezone=Europe%2FRome"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    orario = resp.json()["hourly"]
    punti = []
    for t, ghi, dni, dhi, temp in zip(orario["time"], orario["shortwave_radiation"],
                                      orario["direct_normal_irradiance"], orario["diffuse_radiation"],
                                      orario["temperature_2m"]):
        inizio = t - 3600  # le medie sono dell'ora precedente all'etichetta
        punti.append(Punto(
            ora=datetime.fromtimestamp(inizio).strftime("%Y-%m-%dT%H:00"),
            ts=float(inizio),
            ghi=ghi or 0.0,
            dni=dni or 0.0,
            dhi=dhi or 0.0,
            temp=temp,
        ))
    return punti


def _valore_adesso(punti: list, valori: list, adesso: float) -> float:
    """Valore all'istante `adesso`: ogni ora e' una media, riferita al suo punto
    medio, e fra due punti medi si interpola linearmente."""
    for i, p in enumerate(punti):
        if p.ts <= adesso < p.ts + 3600:
            medio = p.ts + 1800
            if adesso >= medio and i + 1 < len(punti) and punti[i + 1].ts == p.ts + 3600:
                altro, peso = valori[i + 1], (adesso - medio) / 3600
            elif adesso < medio and i > 0 and punti[i - 1].ts == p.ts - 3600:
                altro, peso = valori[i - 1], (medio - adesso) / 3600
            else:
                return valori[i]
            return valori[i] * (1 - peso) + altro * peso
    return 0.0


def modello_attivo(cfg: dict) -> str:
    modello = cfg.get("pannello_modello")
    return modello if modello in MODELLI else "monoasse"


def fattore_configurato(cfg: dict, modello: str) -> float:
    """Il fattore e' per modello: quello del modello orizzontale non vale per il monoasse."""
    if modello == "orizzontale":
        return float(cfg.get("pannello_fattore") or FATTORE_ORIZZONTALE_PREDEFINITO)
    return float(cfg.get("pannello_fattore_monoasse") or FATTORE_MONOASSE_PREDEFINITO)


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
    modello = modello_attivo(cfg)
    fattore = fattore_configurato(cfg, modello)

    ora = time.time()
    chiave = (lat, lon)
    if _cache["chiave"] == chiave and _cache["dati"] and ora - _cache["timestamp"] < CACHE_TTL:
        punti = _cache["dati"]
    else:
        punti = _normalizza(_scarica_irraggiamento(lat, lon))
        _cache.update(chiave=chiave, dati=punti, timestamp=ora)

    poa = [_poa_punto(p, lat, lon, modello) for p in punti]
    kw = [_kw_punto(p, w, potenza, fattore, modello) for p, w in zip(punti, poa)]
    serie = [
        {"ora": p.ora, "irraggiamento_wm2": round(p.ghi, 1), "poa_wm2": round(w, 1), "kw": round(k, 3)}
        for p, w, k in zip(punti, poa, kw)
    ]
    oggi = datetime.fromtimestamp(ora).strftime("%Y-%m-%d")
    return {
        "adesso_kw": round(_valore_adesso(punti, kw, ora), 3),
        "irraggiamento_wm2": round(_valore_adesso(punti, [p.ghi for p in punti], ora), 1),
        "poa_wm2": round(_valore_adesso(punti, poa, ora), 1),
        # ogni punto vale 1 ora, quindi kW e kWh coincidono
        "oggi_kwh": round(sum(k for p, k in zip(punti, kw) if p.ora.startswith(oggi)), 2),
        "serie": serie,
        "potenza_kw": potenza,
        "fattore": fattore,
        "modello": modello,
        "stima": True,
    }


def calibra(stima: dict, produzione_app_kw: float) -> Optional[float]:
    """Fattore del modello attivo che fa coincidere la stima con la lettura dell'app.

    None se la luce e' troppo poca per una calibrazione affidabile.
    """
    irraggiamento = stima["irraggiamento_wm2"] if stima["modello"] == "orizzontale" else stima["poa_wm2"]
    if irraggiamento < SOGLIA_CALIBRAZIONE_WM2 or stima["adesso_kw"] <= 0:
        return None
    # adesso_kw = modello(fattore): il rapporto con la lettura da' il fattore giusto
    return round(produzione_app_kw * stima["fattore"] / stima["adesso_kw"], 3)


def avviso_calibrazione(modello: str, fattore: float) -> Optional[str]:
    """Messaggio se il fattore calibrato e' fuori dal range fisicamente plausibile."""
    if modello == "monoasse" and not 0.6 <= fattore <= 1.1:
        return (f"Fattore {fattore} insolito per il modello monoasse (atteso 0,7-0,95): "
                "controlla la potenza di picco del pannello (kWp) oppure ripeti la calibrazione "
                "con cielo sereno nelle ore centrali.")
    return None
