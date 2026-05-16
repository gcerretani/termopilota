"""
Sistema di raccomandazione energetica per riscaldamento domestico.
Confronta costo riscaldamento: caldaia a condensazione (gas) vs pompa di calore (AC).

Temperatura attuale:  CFR Toscana (stazione configurabile)
Previsioni 48h:       Open-Meteo (gratuito, nessuna API key)
Prezzi gas:           TTF da Yahoo Finance (automatico, aggiornato ogni ora)
Prezzi luce:          PUN da ENTSO-E (con chiave gratuita) oppure manuale

Avvio:  ADMIN_USER=admin ADMIN_PASSWORD=password venv/bin/python app.py
Apri:   http://localhost:5001
"""

import json
import logging
import os
import re
import secrets
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests
from flask import (
    Blueprint, Flask, abort, flash, jsonify, redirect, render_template, request,
    session, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from prezzi import calcola_prezzi
from automazione import get_servizio, avvia_se_attiva
from auth import (
    User, authenticate, change_password, count_admin_attivi, create_user,
    delete_user, link_google_account, list_users, set_active, set_admin,
    set_password, setup_auth, update_user_email,
)
from auth_google import google_attivo, oauth, setup_google_oauth
from providers import (
    aggiorna_config_atomico, get_heatpump, get_thermostat, scrivi_json_atomico,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)
setup_auth(app)

# ─── Costanti fisiche ──────────────────────────────────────────────────────────

KWH_PER_SMC = 10.691

# COP pompa di calore Samsung AJ040TXJ2KG/EU (WindFree Comfort Dual)
# COP nominale certificato EN14511: 4.47 W/W a +7°C est. / +20°C int.
# SCOP stagionale: 4.61 W/W — classe A++
# Tabella ancorata al punto certificato con modello η=0.199 × COP_Carnot
COP_TABELLA = [
    (-15, 1.60), (-10, 1.95), (-7, 2.20), (-5, 2.40), (-2, 2.70),
    (0, 2.90), (2, 3.15), (5, 3.75), (7, 4.47), (10, 4.80),
    (15, 5.15), (20, 5.40),
]

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "data", "config.json")
DEFAULT_CONFIG = {
    "gas_fisso_smc": 0.38,
    "gas_totale_smc_manuale": 0.95,
    "luce_fisso_kwh": 0.14,
    "luce_totale_kwh_manuale": 0.27,
    "entsoe_token": "",
    "temperatura_minima_ac": -10,
    "setpoint_interno": 21,
    "efficienza_caldaia": 0.96,
    "ultima_modifica_fissi": "2025-01-01",
    "note_bolletta": "",
    "automazione_attiva": False,
    "intervallo_controllo_minuti": 15.0,
    "soglia_delta_risparmio": 0.01,
    "smartthings_token": "",
    "smartthings_client_id": "",
    "smartthings_client_secret": "",
    "smartthings_token_data": {},
    "legrand_client_id": "",
    "legrand_client_secret": "",
    "legrand_plant_id": "",
    "google_client_id": "",
    "google_client_secret": "",
    "zone": [],
    "cfr_station_id": "",
    "cfr_station_name": "",
    "lat": 0.0,
    "lon": 0.0,
}


# ─── Config ───────────────────────────────────────────────────────────────────

def carica_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            backup = f"{CONFIG_FILE}.broken-{int(time.time())}"
            try:
                os.replace(CONFIG_FILE, backup)
                logger.error("config.json corrotto, rinominato in %s: %s", backup, e)
            except OSError:
                logger.error("config.json corrotto e impossibile salvarne backup: %s", e)
            return DEFAULT_CONFIG.copy()
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()


def salva_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    scrivi_json_atomico(CONFIG_FILE, cfg)


# ─── COP interpolation ────────────────────────────────────────────────────────

def interpola_cop(temp: float) -> float:
    if temp <= COP_TABELLA[0][0]:
        return COP_TABELLA[0][1]
    if temp >= COP_TABELLA[-1][0]:
        return COP_TABELLA[-1][1]
    for i in range(len(COP_TABELLA) - 1):
        t0, c0 = COP_TABELLA[i]
        t1, c1 = COP_TABELLA[i + 1]
        if t0 <= temp <= t1:
            return round(c0 + (c1 - c0) * (temp - t0) / (t1 - t0), 2)
    return 3.0


# ─── CFR Toscana — temperatura attuale ───────────────────────────────────────

_cache_cfr: dict = {"dati": None, "timestamp": 0.0}
CFR_CACHE_TTL = 600


def _parse_cfr_html(html: str) -> list[dict]:
    pattern = re.compile(
        r'new Array\(\s*"[^"]*"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,',
    )
    misure = []
    for ts_str, temp_str in pattern.findall(html):
        try:
            ts = datetime.strptime(ts_str.strip(), "%d/%m/%Y %H.%M")
            temp = float(temp_str.strip())
            misure.append({"ts": ts, "temp": temp})
        except (ValueError, TypeError):
            continue
    return sorted(misure, key=lambda x: x["ts"])


def scarica_temp_cfr(station_id: str) -> Optional[dict]:
    if not station_id:
        return None
    ora = time.time()
    if _cache_cfr["dati"] and (ora - _cache_cfr["timestamp"]) < CFR_CACHE_TTL:
        return _cache_cfr["dati"]
    try:
        cfr_url = (
            f"https://cfr.toscana.it/monitoraggio/dettaglio.php"
            f"?id={station_id}&type=termo&json=1"
        )
        resp = requests.get(cfr_url, timeout=10)
        resp.raise_for_status()
        misure = _parse_cfr_html(resp.text)
        if misure:
            ultima = misure[-1]
            _cache_cfr["dati"] = ultima
            _cache_cfr["timestamp"] = ora
            return ultima
    except Exception:
        pass
    return None


# ─── Previsioni 48h: Open-Meteo con fallback Met.no ──────────────────────────

_cache_meteo: dict = {"dati": None, "timestamp": 0.0}
METEO_CACHE_TTL = 1800

_METNO_TO_WMO = {
    "clearsky": 0, "fair": 1, "partlycloudy": 2, "cloudy": 3,
    "fog": 45, "lightrain": 61, "rain": 63, "heavyrain": 65,
    "lightrainshowers": 80, "rainshowers": 81, "heavyrainshowers": 82,
    "lightsleet": 71, "sleet": 73, "lightsnow": 71, "snow": 73, "heavysnow": 75,
    "thunder": 95, "thundershowers": 96,
}


def _wmo_da_metno(symbol: str) -> int:
    base = symbol.lower().split("_")[0]
    for k, v in _METNO_TO_WMO.items():
        if k in base:
            return v
    return 3


def _scarica_openmeteo(lat: float, lon: float) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=temperature_2m,apparent_temperature,"
        "precipitation_probability,weathercode"
        "&forecast_days=2&timezone=Europe%2FRome"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _scarica_metno(lat: float, lon: float) -> dict:
    url = (
        f"https://api.met.no/weatherapi/locationforecast/2.0/compact"
        f"?lat={lat}&lon={lon}"
    )
    resp = requests.get(
        url, timeout=10,
        headers={"User-Agent": "termopilota/1.0 github.com/gcerretani/termopilota"},
    )
    resp.raise_for_status()
    raw = resp.json()

    times, temps, app_temps, precip_probs, wcodes = [], [], [], [], []
    for entry in raw["properties"]["timeseries"]:
        t = entry["time"][:16]
        if not t.endswith(":00"):
            continue
        inst = entry["data"]["instant"]["details"]
        t2m = inst.get("air_temperature")
        if t2m is None:
            continue
        next1h = entry["data"].get("next_1_hours", {})
        precip = next1h.get("details", {}).get("precipitation_amount", 0.0)
        symbol = next1h.get("summary", {}).get("symbol_code", "cloudy")
        wmo = _wmo_da_metno(symbol)
        times.append(t)
        temps.append(round(t2m, 1))
        app_temps.append(round(t2m - 1.5, 1))
        precip_probs.append(min(100, int(precip * 30)))
        wcodes.append(wmo)

    times = times[:48]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": temps[:48],
            "apparent_temperature": app_temps[:48],
            "precipitation_probability": precip_probs[:48],
            "weathercode": wcodes[:48],
        }
    }


def scarica_previsioni(lat: float, lon: float) -> dict:
    ora = time.time()
    if _cache_meteo["dati"] and (ora - _cache_meteo["timestamp"]) < METEO_CACHE_TTL:
        return _cache_meteo["dati"]
    try:
        dati = _scarica_openmeteo(lat, lon)
    except Exception:
        dati = _scarica_metno(lat, lon)
    _cache_meteo["dati"] = dati
    _cache_meteo["timestamp"] = ora
    return dati


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


# ─── Logica di raccomandazione ────────────────────────────────────────────────

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


# ─── Route autenticazione ────────────────────────────────────────────────────

def _safe_next(url: Optional[str]) -> str:
    """Restituisce un URL relativo sicuro, o '/' se l'input non e' valido.

    Previene open redirect: rifiuta URL assoluti (con scheme/netloc) o
    'protocol-relative' (che iniziano con '//').
    """
    if not url:
        return "/"
    if not url.startswith("/") or url.startswith("//"):
        return "/"
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        return "/"
    return url


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    next_url = _safe_next(request.values.get("next"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = authenticate(username, password)
        if user:
            login_user(user)
            return redirect(next_url)
        flash("Credenziali non valide.", "error")
    return render_template(
        "login.html",
        next_url=next_url,
        google_abilitato=google_attivo(),
    )


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/login/google")
def login_google():
    if not google_attivo():
        flash("Login con Google non e' configurato.", "error")
        return redirect(url_for("login"))
    next_url = _safe_next(request.args.get("next"))
    session["_login_next"] = next_url
    redirect_uri = url_for("login_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def login_google_callback():
    if not google_attivo():
        flash("Login con Google non e' configurato.", "error")
        return redirect(url_for("login"))
    next_url = _safe_next(session.pop("_login_next", "/"))
    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        logger.warning("Errore OAuth Google: %s", e)
        flash("Autenticazione Google fallita o annullata.", "error")
        return redirect(url_for("login"))

    info = token.get("userinfo") or {}
    if not info:
        try:
            info = oauth.google.userinfo(token=token)
        except Exception as e:
            logger.warning("Errore userinfo Google: %s", e)
            flash("Impossibile leggere il profilo Google.", "error")
            return redirect(url_for("login"))

    if not info.get("email_verified"):
        flash("L'email Google non risulta verificata.", "error")
        return redirect(url_for("login"))

    sub = info.get("sub", "")
    email = (info.get("email") or "").strip().lower()

    user = User.get_by_google_sub(sub) if sub else None
    if user is None and email:
        user = User.get_by_email(email)
        if user and sub and not user.google_sub:
            link_google_account(user.id, sub)

    if user is None:
        flash("Nessun account associato a questa email. Chiedi all'amministratore.", "error")
        return redirect(url_for("login"))
    if not user.is_active:
        flash("Account disabilitato.", "error")
        return redirect(url_for("login"))

    login_user(user)
    return redirect(next_url)


# ─── Route Dashboard ─────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    cfg = carica_config()
    errore_meteo = errore_cfr = None
    raccomandazioni = []
    attuale = None
    cfr_info = None

    prezzi = calcola_prezzi(cfg)

    station_id = cfg.get("cfr_station_id", "")
    lat = cfg.get("lat", 0.0)
    lon = cfg.get("lon", 0.0)

    try:
        misura_cfr = scarica_temp_cfr(station_id)
        if misura_cfr:
            cfr_info = {
                "temp": misura_cfr["temp"],
                "ora": misura_cfr["ts"].strftime("%H:%M"),
                "data": misura_cfr["ts"].strftime("%d/%m/%Y"),
            }
    except Exception as e:
        errore_cfr = str(e)
        misura_cfr = None

    temp_cfr_val = misura_cfr["temp"] if misura_cfr else None

    try:
        previsioni = scarica_previsioni(lat, lon)
        raccomandazioni = calcola_raccomandazioni(previsioni, cfg, temp_cfr_val, prezzi)
        ora_str = datetime.now().strftime("%Y-%m-%dT%H:00")
        attuale = next((r for r in raccomandazioni if r["ora"] == ora_str),
                       raccomandazioni[0] if raccomandazioni else None)
        if attuale and temp_cfr_val is not None:
            attuale["temp_esterna"] = temp_cfr_val
            attuale["fonte_temp"] = "cfr"
    except Exception as e:
        errore_meteo = str(e)

    oggi = datetime.now().strftime("%Y-%m-%d")
    oggi_recs = [r for r in raccomandazioni if r["ora"].startswith(oggi)]
    ore_gas_oggi = sum(1 for r in oggi_recs if r["raccomandazione"] == "gas")
    ore_ac_oggi = sum(1 for r in oggi_recs if r["raccomandazione"] == "ac")
    stato_stanze_dashboard = leggi_stato_stanze_dashboard(cfg)

    return render_template(
        "dashboard.html",
        cfg=cfg,
        prezzi=prezzi,
        attuale=attuale,
        cfr_info=cfr_info,
        raccomandazioni=raccomandazioni,
        raccomandazioni_json=json.dumps(raccomandazioni),
        ore_gas_oggi=ore_gas_oggi,
        ore_ac_oggi=ore_ac_oggi,
        stato_stanze_dashboard=stato_stanze_dashboard,
        errore_meteo=errore_meteo,
        errore_cfr=errore_cfr,
        ora_aggiornamento=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


# ─── API JSON ────────────────────────────────────────────────────────────────

def leggi_stato_stanze_dashboard(cfg: dict) -> dict:
    zone_cfg = cfg.get("zone", []) or []
    zone = [{
        "nome": z.get("nome", "Zona"),
        "room_id": z.get("room_id", ""),
        "ac_device_id": z.get("ac_device_id", ""),
        "t_stanza": None,
        "setpoint": None,
        "modalita": None,
        "sta_riscaldando": None,
    } for z in zone_cfg]

    if not zone:
        return {"zone": [], "errore": None}

    home_id = cfg.get("legrand_plant_id", "")
    bt = get_thermostat("netatmo", cfg)

    if not bt or not bt.autenticato or not home_id:
        return {"zone": zone, "errore": "Configura credenziali Netatmo e Plant ID per leggere lo stato stanze."}

    try:
        stati = bt.stato_tutte_stanze(home_id)
        for z in zone:
            if not z["room_id"]:
                continue
            stato = stati.get(z["room_id"], {})
            z["t_stanza"] = stato.get("temperatura_attuale")
            z["setpoint"] = stato.get("setpoint")
            z["modalita"] = stato.get("modalita")
            z["sta_riscaldando"] = stato.get("sta_riscaldando")
        return {"zone": zone, "errore": None}
    except Exception as e:
        return {"zone": zone, "errore": f"Netatmo non raggiungibile: {e}"}


def scopri_termostati(cfg: dict) -> dict:
    risultato = {"bticino": [], "errori": []}
    bt = get_thermostat("netatmo", cfg)
    if bt and bt.autenticato:
        try:
            impianti = bt.lista_impianti()
            for imp in impianti:
                plant_id = imp.get("id", "")
                moduli = bt.lista_moduli(plant_id)
                for m in moduli:
                    risultato["bticino"].append({
                        "plant_id": plant_id,
                        "id": m.get("id", ""),
                        "name": m.get("name", "Termostato"),
                    })
        except Exception as e:
            risultato["errori"].append(f"BTicino: {e}")
    elif not cfg.get("legrand_client_id"):
        risultato["errori"].append("BTicino: credenziali Netatmo non configurate")
    else:
        risultato["errori"].append("BTicino: autorizzazione OAuth2 non completata")
    return risultato


def scopri_condizionatori(cfg: dict) -> dict:
    risultato = {"samsung": [], "errori": []}
    st = get_heatpump("smartthings", cfg)
    if st and st.configurato:
        try:
            risultato["samsung"] = st.lista_dispositivi_ac()
        except Exception as e:
            risultato["errori"].append(f"SmartThings: {e}")
    else:
        risultato["errori"].append("SmartThings: token non configurato")
    return risultato

@app.route("/api/prezzi")
@login_required
def api_prezzi():
    cfg = carica_config()
    return jsonify(calcola_prezzi(cfg))


@app.route("/api/dati")
@login_required
def api_dati():
    cfg = carica_config()
    prezzi = calcola_prezzi(cfg)
    misura_cfr = scarica_temp_cfr(cfg.get("cfr_station_id", ""))
    temp_cfr = misura_cfr["temp"] if misura_cfr else None
    previsioni = scarica_previsioni(cfg.get("lat", 0.0), cfg.get("lon", 0.0))
    return jsonify(calcola_raccomandazioni(previsioni, cfg, temp_cfr, prezzi))


@app.route("/api/temp-cfr")
@login_required
def api_temp_cfr():
    cfg = carica_config()
    station_id = cfg.get("cfr_station_id", "")
    station_name = cfg.get("cfr_station_name", "") or station_id
    misura = scarica_temp_cfr(station_id)
    if misura:
        return jsonify({
            "stazione": station_id,
            "nome": station_name,
            "temperatura": misura["temp"],
            "timestamp": misura["ts"].isoformat(),
        })
    return jsonify({"errore": "Dati CFR non disponibili"}), 503


@app.route("/api/config", methods=["GET", "POST"])
@login_required
def api_config():
    if request.method == "POST":
        if not current_user.is_admin:
            return jsonify({"errore": "Solo gli amministratori possono modificare la configurazione"}), 403
        dati = request.get_json()
        cfg = carica_config()
        campi_float = ("gas_fisso_smc", "gas_totale_smc_manuale",
                       "luce_fisso_kwh", "luce_totale_kwh_manuale",
                       "temperatura_minima_ac", "setpoint_interno", "efficienza_caldaia",
                       "intervallo_controllo_minuti", "soglia_delta_risparmio",
                       "lat", "lon")
        campi_str = ("entsoe_token", "note_bolletta",
                     "smartthings_token",
                     "smartthings_client_id", "smartthings_client_secret",
                     "legrand_client_id", "legrand_client_secret",
                     "legrand_subscription_key", "legrand_plant_id",
                     "google_client_id", "google_client_secret",
                     "cfr_station_id", "cfr_station_name")
        for campo in campi_float:
            if campo in dati:
                try:
                    cfg[campo] = float(dati[campo])
                except (ValueError, TypeError):
                    pass
        for campo in campi_str:
            if campo in dati:
                cfg[campo] = str(dati[campo])
        if "zone" in dati and isinstance(dati["zone"], list):
            cfg["zone"] = dati["zone"]

        # Clamp di sicurezza lato server
        cfg["efficienza_caldaia"] = max(0.05, min(1.0, float(cfg.get("efficienza_caldaia") or 0.96)))
        cfg["intervallo_controllo_minuti"] = max(1.0, min(1440.0,
            float(cfg.get("intervallo_controllo_minuti") or 15.0)))
        cfg["soglia_delta_risparmio"] = max(0.0, float(cfg.get("soglia_delta_risparmio") or 0.01))

        cfg["ultima_modifica_fissi"] = datetime.now().strftime("%Y-%m-%d")
        salva_config(cfg)
        _cache_meteo["timestamp"] = 0.0
        _cache_cfr["timestamp"] = 0.0
        return jsonify({"status": "ok", "messaggio": "Configurazione salvata"})
    return jsonify(carica_config())


# ─── Route Automazione ────────────────────────────────────────────────────────

@app.route("/api/automazione")
@login_required
def api_automazione():
    return jsonify(get_servizio().stato())


@app.route("/api/automazione/toggle", methods=["POST"])
@login_required
def api_automazione_toggle():
    cfg = carica_config()
    servizio = get_servizio()
    attiva_ora = not cfg.get("automazione_attiva", False)
    cfg["automazione_attiva"] = attiva_ora
    salva_config(cfg)
    if attiva_ora:
        servizio.avvia()
    else:
        servizio.ferma()
    return jsonify({"automazione_attiva": attiva_ora})


@app.route("/api/dispositivi")
@login_required
def api_dispositivi():
    cfg = carica_config()
    termostati = scopri_termostati(cfg)
    condizionatori = scopri_condizionatori(cfg)
    risultato = {
        "bticino": termostati["bticino"],
        "samsung": condizionatori["samsung"],
        "errori": termostati["errori"] + condizionatori["errori"],
    }

    return jsonify(risultato)


@app.route("/api/dispositivi/termostati")
@login_required
def api_dispositivi_termostati():
    return jsonify(scopri_termostati(carica_config()))


@app.route("/api/dispositivi/condizionatori")
@login_required
def api_dispositivi_condizionatori():
    return jsonify(scopri_condizionatori(carica_config()))


# ─── OAuth callback Netatmo / SmartThings ────────────────────────────────────

def _redirect_credenziali():
    return redirect(url_for("admin.admin_credentials"))


@app.route("/api/automazione/oauth-callback")
def api_oauth_callback():
    code = request.args.get("code")
    state = request.args.get("state", "")
    state_atteso = session.pop("netatmo_oauth_state", None)
    if not state_atteso or state != state_atteso:
        flash("Stato OAuth Netatmo non valido. Ripeti l'autorizzazione.", "error")
        return _redirect_credenziali()
    if not code:
        flash("Nessun codice ricevuto da Netatmo.", "error")
        return _redirect_credenziali()
    cfg = carica_config()
    bt = get_thermostat("netatmo", cfg)
    if not bt:
        flash("Credenziali Netatmo non configurate.", "error")
        return _redirect_credenziali()
    try:
        redirect_uri = request.url_root.rstrip("/") + "/api/automazione/oauth-callback"
        bt.scambia_codice(code, redirect_uri)
        flash("Autorizzazione Netatmo completata.", "success")
    except Exception as e:
        logger.warning("Errore OAuth Netatmo: %s", e)
        flash(f"Errore autorizzazione Netatmo: {e}", "error")
    return _redirect_credenziali()


@app.route("/api/automazione/oauth-url")
@login_required
def api_oauth_url():
    cfg = carica_config()
    bt = get_thermostat("netatmo", cfg)
    if not bt:
        return jsonify({"errore": "Credenziali Netatmo non configurate"}), 400
    state = secrets.token_urlsafe(24)
    session["netatmo_oauth_state"] = state
    redirect_uri = request.url_root.rstrip("/") + "/api/automazione/oauth-callback"
    return jsonify({"url": bt.url_autorizzazione(redirect_uri, state)})


@app.route("/api/automazione/smartthings-callback")
def api_smartthings_callback():
    code = request.args.get("code")
    state = request.args.get("state", "")
    state_atteso = session.pop("smartthings_oauth_state", None)
    if not state_atteso or state != state_atteso:
        flash("Stato OAuth SmartThings non valido. Ripeti l'autorizzazione.", "error")
        return _redirect_credenziali()
    if not code:
        flash("Nessun codice ricevuto da SmartThings.", "error")
        return _redirect_credenziali()
    cfg = carica_config()
    st = get_heatpump("smartthings", cfg)
    if not st or not st.client_id:
        flash("Credenziali SmartThings OAuth non configurate.", "error")
        return _redirect_credenziali()
    try:
        redirect_uri = request.url_root.rstrip("/") + "/api/automazione/smartthings-callback"
        st.scambia_codice(code, redirect_uri)
        flash("Autorizzazione SmartThings completata.", "success")
    except Exception as e:
        logger.warning("Errore OAuth SmartThings: %s", e)
        flash(f"Errore autorizzazione SmartThings: {e}", "error")
    return _redirect_credenziali()


@app.route("/api/automazione/smartthings-oauth-url")
@login_required
def api_smartthings_oauth_url():
    cfg = carica_config()
    st = get_heatpump("smartthings", cfg)
    if not st or not st.client_id or not st.client_secret:
        return jsonify({"errore": "Client ID/Secret SmartThings non configurati"}), 400
    state = secrets.token_urlsafe(24)
    session["smartthings_oauth_state"] = state
    redirect_uri = request.url_root.rstrip("/") + "/api/automazione/smartthings-callback"
    return jsonify({"url": st.url_autorizzazione(redirect_uri, state)})


# ─── Blueprint Admin ──────────────────────────────────────────────────────────

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
@login_required
def admin_before_request():
    if not current_user.is_admin:
        flash("Accesso riservato agli amministratori.", "error")
        return redirect(url_for("index"))


@admin_bp.route("/")
def admin_settings():
    cfg = carica_config()
    return render_template("admin/settings.html", cfg=cfg)


@admin_bp.route("/credentials")
def admin_credentials():
    cfg = carica_config()
    return render_template("admin/credentials.html", cfg=cfg)


@admin_bp.route("/zones")
def admin_zones():
    cfg = carica_config()
    return render_template("admin/zones.html", cfg=cfg)


@admin_bp.route("/users", methods=["GET", "POST"])
def admin_users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        email = request.form.get("email", "").strip() or None
        is_admin = request.form.get("is_admin") == "1"
        if not username or not password:
            flash("Username e password sono obbligatori.", "error")
        elif create_user(username, password, is_admin=is_admin, email=email):
            flash(f"Utente '{username}' creato.", "success")
        else:
            flash(f"Username o email gia' esistenti.", "error")
        return redirect(url_for("admin.admin_users"))
    users = list_users()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
def admin_delete_user(user_id):
    if user_id == current_user.id:
        flash("Non puoi eliminare il tuo stesso account.", "error")
    else:
        target = User.get(user_id)
        if target and target.is_admin and target.is_active and count_admin_attivi() <= 1:
            flash("Non puoi eliminare l'ultimo amministratore attivo.", "error")
        elif delete_user(user_id):
            flash("Utente eliminato.", "success")
        else:
            flash("Utente non trovato.", "error")
    return redirect(url_for("admin.admin_users"))


@admin_bp.route("/users/<int:user_id>/edit", methods=["POST"])
def admin_edit_user(user_id):
    target = User.get(user_id)
    if target is None:
        flash("Utente non trovato.", "error")
        return redirect(url_for("admin.admin_users"))
    email = request.form.get("email", "").strip() or None
    if not update_user_email(user_id, email):
        flash("Email gia' in uso da un altro utente.", "error")
        return redirect(url_for("admin.admin_users"))
    if "is_admin" in request.form and user_id != current_user.id:
        nuovo_admin = request.form.get("is_admin") == "1"
        if not set_admin(user_id, nuovo_admin):
            flash("Impossibile rimuovere l'ultimo amministratore attivo.", "error")
            return redirect(url_for("admin.admin_users"))
    flash(f"Utente '{target.username}' aggiornato.", "success")
    return redirect(url_for("admin.admin_users"))


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
def admin_reset_password(user_id):
    nuova = request.form.get("password", "")
    if not nuova or len(nuova) < 4:
        flash("Password troppo corta (minimo 4 caratteri).", "error")
    elif set_password(user_id, nuova):
        flash("Password aggiornata.", "success")
    else:
        flash("Utente non trovato.", "error")
    return redirect(url_for("admin.admin_users"))


@admin_bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
def admin_toggle_active(user_id):
    if user_id == current_user.id:
        flash("Non puoi disattivare il tuo stesso account.", "error")
        return redirect(url_for("admin.admin_users"))
    target = User.get(user_id)
    if target is None:
        flash("Utente non trovato.", "error")
        return redirect(url_for("admin.admin_users"))
    nuovo_stato = not target.is_active
    if set_active(user_id, nuovo_stato):
        msg = "Utente attivato." if nuovo_stato else "Utente disattivato."
        flash(msg, "success")
    else:
        flash("Impossibile disattivare l'ultimo amministratore attivo.", "error")
    return redirect(url_for("admin.admin_users"))


app.register_blueprint(admin_bp)


# ─── Account self-service ────────────────────────────────────────────────────

@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        vecchia = request.form.get("password_vecchia", "")
        nuova = request.form.get("password_nuova", "")
        conferma = request.form.get("password_conferma", "")
        if not nuova or len(nuova) < 4:
            flash("La nuova password deve essere di almeno 4 caratteri.", "error")
        elif nuova != conferma:
            flash("Le due password non coincidono.", "error")
        elif not change_password(current_user.id, vecchia, nuova):
            flash("Password attuale non corretta.", "error")
        else:
            flash("Password aggiornata.", "success")
        return redirect(url_for("account"))
    return render_template("account.html", user=current_user)


# ─── Avvio automazione (compatibile gunicorn --preload) ──────────────────────

_automazione_avviata = False


def _avvia_automazione():
    global _automazione_avviata
    if not _automazione_avviata:
        if not os.path.exists(CONFIG_FILE):
            salva_config(DEFAULT_CONFIG)
        avvia_se_attiva()
        _automazione_avviata = True


_avvia_automazione()

# Registra Google OAuth se le credenziali sono configurate. Cambiarle richiede
# un riavvio dell'app.
_cfg_iniziale = carica_config()
setup_google_oauth(
    app,
    _cfg_iniziale.get("google_client_id", ""),
    _cfg_iniziale.get("google_client_secret", ""),
)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  TermoPilota — controllo riscaldamento")
    print("─" * 42)
    print("  Apri il browser su:  http://localhost:5001")
    print("─" * 42)
    app.run(debug=False, port=5001, host="0.0.0.0")
