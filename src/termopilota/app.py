# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Sistema di raccomandazione energetica per riscaldamento domestico.
Confronta costo riscaldamento: caldaia a condensazione (gas) vs pompa di calore (AC).

Temperatura attuale:  stazione meteo Netatmo o CFR Toscana (priorita' configurabile)
Previsioni 48h:       Open-Meteo (gratuito, nessuna API key)
Prezzi gas:           TTF da Yahoo Finance (automatico, aggiornato ogni ora)
Prezzi luce:          PUN da ENTSO-E (con chiave gratuita) oppure manuale

Avvio:  ADMIN_USER=admin ADMIN_PASSWORD=password venv/bin/python app.py
Apri:   http://localhost:5001
"""

import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import requests
from flask import (
    Blueprint, Flask, abort, flash, jsonify, redirect, render_template, request,
    send_from_directory, session, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import current_user, login_required, login_user, logout_user
from termopilota.prezzi import calcola_prezzi
from termopilota.automazione import get_servizio, avvia_se_attiva
from termopilota import dispositivi
from termopilota import live
from termopilota import osservatore
from termopilota import registro
from termopilota import pannello
from termopilota import storico
from termopilota import temperatura_esterna
from termopilota.versione import VERSIONE
from termopilota.auth import (
    User, authenticate, change_password, count_admin_attivi, create_user,
    delete_user, link_google_account, list_users, set_active, set_admin,
    set_password, setup_auth, update_user_email,
)
from termopilota.auth_google import google_attivo, oauth, setup_google_oauth
from termopilota.providers import (
    aggiorna_config_atomico, conteggio_chiamate, get_heatpump, get_thermostat, scrivi_json_atomico,
)

from termopilota.providers.netatmo import ErroreNetatmo
from termopilota.providers.netatmo import firma_webhook_valida as netatmo_firma_valida
from termopilota.raccomandazioni import calcola_raccomandazioni
from termopilota.percorsi import CONFIG_FILE

# Senza handler i messaggi INFO/WARNING dei moduli non arrivano al log del container.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)

def configura_proxy(flask_app: Flask) -> None:
    """Fidati degli header X-Forwarded-* di N reverse proxy (TERMOPILOTA_PROXY=N).

    Dietro Traefik/nginx serve perche' url_for(_external=True), per esempio il
    callback di Google OAuth, usi https e l'host pubblico. Di default e' spento:
    con la porta esposta direttamente un client potrebbe falsificare gli header.
    """
    try:
        n = int(os.environ.get("TERMOPILOTA_PROXY", "0"))
    except ValueError:
        n = 0
    if n > 0:
        flask_app.wsgi_app = ProxyFix(flask_app.wsgi_app, x_for=n, x_proto=n, x_host=n)


app = Flask(__name__)
configura_proxy(app)
setup_auth(app)
# Il cookie di sessione non parte nelle richieste POST da altri siti: con le API
# JSON (vedi _json_richiesto) e' la protezione CSRF dei comandi ai dispositivi
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
app.config.setdefault("REMEMBER_COOKIE_SAMESITE", "Lax")

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
    "potenza_termica_kw": 4.0,
    "gas_iva_pct": 0.0,                  # % IVA sul gas; 0 se le voci sopra sono gia' lorde
    "luce_iva_pct": 0.0,                 # % IVA sulla luce
    "gas_tariffa": "variabile",          # "variabile" (TTF) | "fissa" (prezzo bloccato)
    "gas_commodity_fisso_smc": 0.0,      # €/Smc, solo materia prima gas se tariffa fissa
    "luce_tariffa": "variabile",         # "variabile" (PUN) | "fissa"
    "luce_commodity_fisso_kwh": 0.0,     # €/kWh, solo materia prima energia se fissa
    "pannello_potenza_kw": 0.9,          # potenza di picco (kWp) del pannello adottato
    "pannello_lat": 38.5,
    "pannello_lon": -1.2,
    # Modello di produzione: "monoasse" (inseguitore, predefinito) | "orizzontale".
    # Ogni modello ha il suo fattore: quello orizzontale assorbe il guadagno
    # dell'inseguitore e non vale per il monoasse (qui e' un rendimento di sistema).
    "pannello_modello": "monoasse",
    "pannello_fattore": 0.9,             # modello orizzontale
    "pannello_fattore_monoasse": 0.85,   # modello monoasse
    # Compensazione nel quarto d'ora: "totale" | "materia_prima" | "nessuna"
    "pannello_compensazione": "totale",
    "pompa_potenza_elettrica_kw": 1.2,   # assorbimento elettrico della pompa quando riscalda
    "consumo_base_kw": 0.3,              # consumo medio del resto della casa
    "smartthings_token": "",
    "smartthings_client_id": "",
    "smartthings_client_secret": "",
    "smartthings_token_data": {},
    "smartthings_webhook_token": "",
    "netatmo_webhook": {},
    "legrand_client_id": "",
    "legrand_client_secret": "",
    "legrand_plant_id": "",
    "google_client_id": "",
    "google_client_secret": "",
    "zone": [],
    # Automazione: simulazione (decide e registra senza inviare comandi), opzioni
    # dell'AC quando lo accende TermoPilota, pausa dopo un comando manuale
    "automazione_simulazione": False,
    "ac_ventola": "auto",                # auto | low | medium | high | turbo
    "ac_modalita_notte": "off",          # off | sleep | quiet | windFree | windFreeSleep
    "notte_inizio": 22,
    "notte_fine": 7,
    "pausa_manuale_ore": 3.0,
    "netatmo_polling_secondi": 120,
    "cfr_station_id": "",
    "cfr_station_name": "",
    # Temperatura esterna attuale: fonte preferita (l'altra fa da riserva), modulo
    # esterno Netatmo ("" = l'unico presente), eta' massima di una misura valida
    "priorita_temp_esterna": "netatmo",  # netatmo | cfr
    "meteo_modulo_id": "",
    "temp_esterna_max_eta_minuti": 60,
    "lat": 0.0,
    "lon": 0.0,
}


@app.context_processor
def _contesto_template():
    return {"versione_app": VERSIONE}


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


# ─── Temperatura esterna attuale: Netatmo, CFR o previsione ──────────────────

def misure_temp_esterna(cfg: dict) -> dict:
    """{netatmo: {temp, ts, nome, id} | None, cfr: {temp, ts, nome, id} | None}.

    Il modulo Netatmo arriva dalla fotografia dei dispositivi (parte 'meteo',
    5 min di cache), la CFR dalla sua cache di 10 min."""
    misure = {"netatmo": None, "cfr": None}
    station_id = cfg.get("cfr_station_id", "")
    try:
        cfr = scarica_temp_cfr(station_id)
        if cfr:
            misure["cfr"] = {"temp": cfr["temp"], "ts": cfr["ts"], "id": station_id,
                             "nome": cfg.get("cfr_station_name") or f"Stazione CFR {station_id}"}
    except Exception as e:
        logger.info("Temperatura CFR non disponibile: %s", e)
    try:
        snap = dispositivi.snapshot(cfg, parti=("meteo",))
        modulo = dispositivi.modulo_meteo(snap, cfg.get("meteo_modulo_id", ""))
        if modulo and modulo.get("temperatura") is not None and modulo.get("ts"):
            misure["netatmo"] = {"temp": modulo["temperatura"], "ts": datetime.fromtimestamp(modulo["ts"]),
                                 "id": modulo["id"], "nome": modulo.get("nome") or "Stazione Netatmo"}
    except Exception as e:
        logger.info("Temperatura Netatmo non disponibile: %s", e)
    return misure


def _max_eta_temp(cfg: dict) -> float:
    return _limita(cfg.get("temp_esterna_max_eta_minuti"), temperatura_esterna.MAX_ETA_DEFAULT_MIN,
                   temperatura_esterna.MAX_ETA_MIN, temperatura_esterna.MAX_ETA_MAX)


def temperatura_esterna_attuale(cfg: dict, misure: Optional[dict] = None) -> Optional[dict]:
    """Misura valida della fonte preferita, o della riserva: {temp, ts, fonte, nome} o None
    (allora il motore usa la previsione)."""
    if misure is None:
        misure = misure_temp_esterna(cfg)
    return temperatura_esterna.scegli(misure, temperatura_esterna.ordine(cfg.get("priorita_temp_esterna")),
                                      _max_eta_temp(cfg), datetime.now())


def raccomandazioni_con_temp(cfg: dict, prezzi: dict, scelta: Optional[dict]) -> list:
    """Motore delle raccomandazioni con la temperatura esterna misurata sull'ora corrente."""
    previsioni = scarica_previsioni(cfg.get("lat", 0.0), cfg.get("lon", 0.0))
    return calcola_raccomandazioni(previsioni, cfg, scelta["temp"] if scelta else None, prezzi,
                                   pannello.kw_per_ora(cfg), fonte_attuale=scelta["fonte"] if scelta else "cfr")


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

def dati_dashboard(cfg: dict) -> dict:
    """Raccoglie tutti i dati della dashboard: usato dal render Jinja al primo
    paint e dall'API /api/dashboard per il refresh live senza ricaricare."""
    errore_meteo = errore_temp = None
    raccomandazioni = []
    attuale = None
    temp_info = None

    prezzi = calcola_prezzi(cfg)

    misure = misure_temp_esterna(cfg)
    scelta = temperatura_esterna_attuale(cfg, misure)
    if scelta:
        temp_info = {
            "temp": scelta["temp"],
            "ora": scelta["ts"].strftime("%H:%M"),
            "data": scelta["ts"].strftime("%d/%m/%Y"),
            "fonte": scelta["fonte"],
            "nome": scelta["nome"],
        }
    elif any(misure.values()) or cfg.get("cfr_station_id"):
        # Fonti configurate ma nessuna misura recente: si usa la previsione
        errore_temp = "nessuna misura recente"

    try:
        raccomandazioni = raccomandazioni_con_temp(cfg, prezzi, scelta)
        ora_str = datetime.now().strftime("%Y-%m-%dT%H:00")
        attuale = next((r for r in raccomandazioni if r["ora"] == ora_str),
                       raccomandazioni[0] if raccomandazioni else None)
        if attuale and scelta:
            attuale["temp_esterna"] = scelta["temp"]
            attuale["fonte_temp"] = scelta["fonte"]
    except Exception as e:
        errore_meteo = str(e)

    oggi = datetime.now().strftime("%Y-%m-%d")
    oggi_recs = [r for r in raccomandazioni if r["ora"].startswith(oggi)]
    ore_gas_oggi = sum(1 for r in oggi_recs if r["raccomandazione"] == "gas")
    ore_ac_oggi = sum(1 for r in oggi_recs if r["raccomandazione"] == "ac")

    return {
        "prezzi": prezzi,
        "attuale": attuale,
        "temp_info": temp_info,
        "raccomandazioni": raccomandazioni,
        "ore_gas_oggi": ore_gas_oggi,
        "ore_ac_oggi": ore_ac_oggi,
        "errori": {"meteo": errore_meteo, "temp_esterna": errore_temp},
        "generato_alle": datetime.now().strftime("%H:%M"),
    }


@app.route("/")
@login_required
def index():
    cfg = carica_config()
    dati = dati_dashboard(cfg)
    stato_stanze_dashboard = leggi_stato_stanze_dashboard(cfg)

    return render_template(
        "dashboard.html",
        cfg=cfg,
        prezzi=dati["prezzi"],
        attuale=dati["attuale"],
        temp_info=dati["temp_info"],
        raccomandazioni=dati["raccomandazioni"],
        raccomandazioni_json=json.dumps(dati["raccomandazioni"]),
        ore_gas_oggi=dati["ore_gas_oggi"],
        ore_ac_oggi=dati["ore_ac_oggi"],
        stato_stanze_dashboard=stato_stanze_dashboard,
        consumo_ac_oggi=consumo_ac_oggi(),
        errore_meteo=dati["errori"]["meteo"],
        errore_temp=dati["errori"]["temp_esterna"],
        generato_alle=dati["generato_alle"],
    )


@app.route("/previsioni")
@login_required
def pagina_previsioni():
    cfg = carica_config()
    dati = dati_dashboard(cfg)
    return render_template(
        "previsioni.html",
        cfg=cfg,
        raccomandazioni_json=json.dumps(dati["raccomandazioni"]),
        errore_meteo=dati["errori"]["meteo"],
        errore_temp=dati["errori"]["temp_esterna"],
        generato_alle=dati["generato_alle"],
    )


@app.route("/automazione")
@login_required
def pagina_automazione():
    cfg = carica_config()
    return render_template("automazione.html", cfg=cfg)


@app.route("/storico")
@login_required
def pagina_storico():
    return render_template("storico.html")


@app.route("/impostazioni")
@login_required
def pagina_impostazioni():
    return render_template("impostazioni.html")


@app.route("/dispositivi")
@login_required
def pagina_dispositivi():
    return render_template("dispositivi.html")


TIPI_DISPOSITIVO = ("ac", "stanza", "casa", "meteo")


@app.route("/dispositivi/<tipo>/<ident>")
@login_required
def pagina_dispositivo(tipo, ident):
    if tipo not in TIPI_DISPOSITIVO:
        abort(404)
    return render_template("dispositivo.html", tipo=tipo, ident=ident)


@app.route("/stanze/<room_id>")
@login_required
def pagina_stanza(room_id):
    zona = _zona_configurata(carica_config(), room_id)
    if not zona:
        abort(404)
    return render_template("stanza.html", zona=zona)


@app.route("/sw.js")
def service_worker():
    # Servito dalla root cosi' lo scope del service worker copre tutta l'app.
    return send_from_directory(app.static_folder, "sw.js",
                               mimetype="application/javascript")


# ─── API JSON ────────────────────────────────────────────────────────────────

def leggi_stato_stanze_dashboard(cfg: dict) -> dict:
    """Stanze delle zone configurate per la Home: termostato, condizionatore e
    decisione dell'automazione, dalla fotografia condivisa dei dispositivi."""
    zone_cfg = cfg.get("zone", []) or []
    if not zone_cfg:
        return {"zone": [], "errore": None}

    snap = dispositivi.snapshot(cfg)
    servizio = get_servizio().stato()
    decisioni = {z.get("room_id"): z for z in servizio["zone"]}
    pause = servizio.get("pause", {})
    zone = []
    for z in zone_cfg:
        rid, acid = z.get("room_id", ""), z.get("ac_device_id", "")
        st = snap["stanze"].get(rid) or {}
        ac = (snap["ac"].get(acid) or {}).get("stato") or {}
        decisione = decisioni.get(rid) or {}
        zone.append({
            "nome": z.get("nome", "Zona"),
            "room_id": rid,
            "ac_device_id": acid,
            "t_stanza": st.get("temperatura_attuale"),
            "setpoint": st.get("setpoint"),
            "target": st.get("target"),
            "modalita": st.get("modalita"),
            "umidita": st.get("umidita"),
            "sta_riscaldando": st.get("sta_riscaldando"),
            "richiesta_calore_pct": st.get("richiesta_calore_pct"),
            "finestra_aperta": st.get("finestra_aperta"),
            "raggiungibile": st.get("raggiungibile"),
            "errore_termostato": st.get("errore"),
            "ac": {
                "nome": snap["ac"][acid]["nome"],
                "acceso": ac.get("acceso"),
                "modalita": ac.get("modalita"),
                "setpoint": ac.get("setpoint_riscaldamento"),
                "umidita": ac.get("umidita"),
                "filtro_stato": ac.get("filtro_stato"),
            } if ac else None,
            "inclusa": z.get("automazione", True) is not False,
            "stato_automazione": decisione.get("stato"),
            "motivo": decisione.get("motivo"),
            "pausa_fino": pause.get(rid),
        })

    errore = None
    bt = get_thermostat("netatmo", cfg)
    if not bt or not bt.autenticato or not cfg.get("legrand_plant_id", ""):
        errore = "Configura credenziali Netatmo e Plant ID per leggere lo stato stanze."
    elif snap["errori"]:
        errore = "; ".join(snap["errori"])
    return {"zone": zone, "errore": errore}


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


def scopri_impianti(cfg: dict) -> dict:
    """Elenco degli impianti (home) Netatmo: serve a scegliere il Plant ID."""
    risultato = {"impianti": [], "errori": []}
    bt = get_thermostat("netatmo", cfg)
    if bt and bt.autenticato:
        try:
            risultato["impianti"] = bt.lista_impianti()
        except Exception as e:
            risultato["errori"].append(f"Netatmo: {e}")
    elif not cfg.get("legrand_client_id"):
        risultato["errori"].append("Netatmo: credenziali non configurate")
    else:
        risultato["errori"].append("Netatmo: autorizzazione OAuth2 non completata")
    return risultato


def imposta_plant_id_se_unico(bt) -> Optional[str]:
    """Dopo l'autorizzazione: se Netatmo ha un solo impianto e il Plant ID e'
    vuoto, lo imposta. Restituisce l'ID impostato, altrimenti None."""
    cfg = carica_config()
    if cfg.get("legrand_plant_id"):
        return None
    impianti = bt.lista_impianti()
    if len(impianti) != 1:
        return None
    cfg["legrand_plant_id"] = impianti[0]["id"]
    salva_config(cfg)
    return cfg["legrand_plant_id"]


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

@app.route("/api/dashboard")
@login_required
def api_dashboard():
    cfg = carica_config()
    dati = dati_dashboard(cfg)
    dati["stanze"] = leggi_stato_stanze_dashboard(cfg)
    dati["consumo_ac_oggi_kwh"] = consumo_ac_oggi()
    return jsonify(dati)


def consumo_ac_oggi() -> Optional[float]:
    """kWh misurati oggi da tutti i condizionatori (None senza letture)."""
    oggi = datetime.now().date().isoformat()
    giorno = storico.consumi_misurati(oggi, oggi).get(oggi)
    return round(giorno["kwh"], 2) if giorno else None


@app.route("/api/pannello")
@login_required
def api_pannello():
    try:
        return jsonify(pannello.produzione_pannello(carica_config()))
    except Exception as e:
        logger.warning("Stima pannello non disponibile: %s", e)
        return jsonify({"errore": "Dati di irraggiamento non disponibili"}), 503


@app.route("/api/pannello/calibra", methods=["POST"])
@login_required
def api_pannello_calibra():
    """Calibra il fattore del modello attivo dalla produzione letta nell'app del pannello."""
    if not current_user.is_admin:
        return jsonify({"errore": "Solo gli amministratori possono calibrare"}), 403
    dati = request.get_json(silent=True) or {}
    try:
        lettura_kw = float(dati["produzione_kw"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"errore": "produzione_kw mancante o non numerico"}), 400
    cfg = carica_config()
    try:
        stima = pannello.produzione_pannello(cfg)
    except Exception:
        return jsonify({"errore": "Dati di irraggiamento non disponibili"}), 503
    fattore = pannello.calibra(stima, lettura_kw)
    if fattore is None:
        return jsonify({"errore": "Irraggiamento troppo basso per calibrare: riprova "
                                  "nelle ore centrali di una giornata soleggiata"}), 409
    fattore = max(0.1, min(2.0, fattore))
    modello = stima["modello"]
    cfg["pannello_fattore" if modello == "orizzontale" else "pannello_fattore_monoasse"] = fattore
    salva_config(cfg)
    return jsonify({"status": "ok", "modello": modello, "pannello_fattore": fattore,
                    "avviso": pannello.avviso_calibrazione(modello, fattore)})


@app.route("/api/storico")
@login_required
def api_storico():
    oggi = datetime.now().date()
    da = request.args.get("da", (oggi - timedelta(days=6)).isoformat())
    a = request.args.get("a", oggi.isoformat())
    risoluzione = request.args.get("risoluzione", "oraria")
    if risoluzione not in ("oraria", "giornaliera"):
        return jsonify({"errore": "risoluzione deve essere 'oraria' o 'giornaliera'"}), 400
    try:
        datetime.strptime(da, "%Y-%m-%d")
        datetime.strptime(a, "%Y-%m-%d")
    except ValueError:
        return jsonify({"errore": "date nel formato YYYY-MM-DD"}), 400
    cfg = carica_config()
    potenza = max(0.5, min(30.0, float(cfg.get("potenza_termica_kw") or 4.0)))
    punti = storico.leggi_campioni(da, a, risoluzione, potenza)
    return jsonify({"da": da, "a": a, "risoluzione": risoluzione, "punti": punti,
                    # Consumo reale dei condizionatori (contatore): per AC e per giorno
                    "energia_ac": storico.energia_ac(da, a, risoluzione),
                    "misurati": storico.consumi_misurati(da, a)})


@app.route("/api/risparmi")
@login_required
def api_risparmi():
    cfg = carica_config()
    potenza = max(0.5, min(30.0, float(cfg.get("potenza_termica_kw") or 4.0)))
    return jsonify(storico.calcola_risparmi(potenza))


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
    return jsonify(raccomandazioni_con_temp(cfg, prezzi, temperatura_esterna_attuale(cfg)))


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


def _misura_json(misura: Optional[dict], adesso: datetime) -> Optional[dict]:
    if not misura:
        return None
    eta = temperatura_esterna.eta_minuti(misura, adesso)
    return {**misura, "ts": misura["ts"].isoformat(timespec="minutes"),
            "eta_minuti": round(eta) if eta is not None else None}


@app.route("/api/temp-esterna")
@login_required
def api_temp_esterna():
    """Temperatura esterna attuale: la misura scelta e quelle di tutte le fonti."""
    cfg = carica_config()
    misure = misure_temp_esterna(cfg)
    scelta = temperatura_esterna_attuale(cfg, misure)
    adesso = datetime.now()
    max_eta = _max_eta_temp(cfg)
    return jsonify({
        "scelta": _misura_json(scelta, adesso),
        "fonti": {f: (_misura_json(m, adesso) | {"valida": temperatura_esterna.valida(m, max_eta, adesso)}
                      if m else None)
                  for f, m in misure.items()},
        "priorita": temperatura_esterna.ordine(cfg.get("priorita_temp_esterna")),
        "max_eta_minuti": max_eta,
    })


VENTOLE_AC = ("auto", "low", "medium", "high", "turbo")
MODALITA_NOTTE_AC = ("off", "sleep", "quiet", "windFree", "windFreeSleep")


def _limita(valore, predefinito: float, minimo: float, massimo: float) -> float:
    try:
        return max(minimo, min(massimo, float(valore)))
    except (TypeError, ValueError):
        return predefinito


def normalizza_zone(zone: list) -> list:
    """Zone dall'editor: solo i campi noti, con tipi e limiti controllati."""
    risultato = []
    for z in zone:
        if not isinstance(z, dict):
            continue
        risultato.append({
            "nome": str(z.get("nome") or "Zona")[:60],
            "room_id": str(z.get("room_id") or ""),
            "ac_device_id": str(z.get("ac_device_id") or ""),
            "automazione": z.get("automazione", True) is not False,
            "modalita": "affiancata" if z.get("modalita") == "affiancata" else "esclusiva",
            "riserva_gas_delta": _limita(z.get("riserva_gas_delta", 1.5), 1.5, 0.0, 5.0),
            "offset_ac": _limita(z.get("offset_ac", 0.0), 0.0, -3.0, 3.0),
        })
    return risultato


@app.route("/api/config", methods=["GET", "POST"])
@login_required
def api_config():
    if request.method == "POST":
        if not current_user.is_admin:
            return jsonify({"errore": "Solo gli amministratori possono modificare la configurazione"}), 403
        dati = request.get_json()
        cfg = carica_config()
        prima = json.loads(json.dumps(cfg))
        # I segreti tornano al browser come "***": quel valore non va salvato
        dati = {k: v for k, v in (dati or {}).items() if v != SEGRETO_MASCHERATO}
        campi_float = ("gas_fisso_smc", "gas_totale_smc_manuale",
                       "luce_fisso_kwh", "luce_totale_kwh_manuale",
                       "temperatura_minima_ac", "setpoint_interno", "efficienza_caldaia",
                       "intervallo_controllo_minuti", "soglia_delta_risparmio",
                       "potenza_termica_kw", "lat", "lon",
                       "gas_commodity_fisso_smc", "luce_commodity_fisso_kwh",
                       "gas_iva_pct", "luce_iva_pct",
                       "pannello_potenza_kw", "pannello_lat", "pannello_lon",
                       "pannello_fattore", "pannello_fattore_monoasse", "pompa_potenza_elettrica_kw",
                       "consumo_base_kw")
        campi_str = ("entsoe_token", "note_bolletta",
                     "smartthings_token",
                     "smartthings_client_id", "smartthings_client_secret",
                     "legrand_client_id", "legrand_client_secret",
                     "legrand_subscription_key", "legrand_plant_id",
                     "google_client_id", "google_client_secret",
                     "cfr_station_id", "cfr_station_name", "meteo_modulo_id")
        for campo in campi_float:
            if campo in dati:
                try:
                    cfg[campo] = float(dati[campo])
                except (ValueError, TypeError):
                    pass
        for campo in campi_str:
            if campo in dati:
                cfg[campo] = str(dati[campo])
        for campo in ("gas_tariffa", "luce_tariffa"):
            if dati.get(campo) in ("variabile", "fissa"):
                cfg[campo] = dati[campo]
        if dati.get("pannello_compensazione") in ("totale", "materia_prima", "nessuna"):
            cfg["pannello_compensazione"] = dati["pannello_compensazione"]
        if dati.get("pannello_modello") in pannello.MODELLI:
            cfg["pannello_modello"] = dati["pannello_modello"]
        if "zone" in dati and isinstance(dati["zone"], list):
            cfg["zone"] = normalizza_zone(dati["zone"])
        if "automazione_simulazione" in dati:
            cfg["automazione_simulazione"] = bool(dati["automazione_simulazione"])
        if dati.get("ac_ventola") in VENTOLE_AC:
            cfg["ac_ventola"] = dati["ac_ventola"]
        if dati.get("priorita_temp_esterna") in temperatura_esterna.FONTI:
            cfg["priorita_temp_esterna"] = dati["priorita_temp_esterna"]
        if "temp_esterna_max_eta_minuti" in dati:
            try:
                cfg["temp_esterna_max_eta_minuti"] = int(max(
                    temperatura_esterna.MAX_ETA_MIN,
                    min(temperatura_esterna.MAX_ETA_MAX, float(dati["temp_esterna_max_eta_minuti"]))))
            except (ValueError, TypeError):
                pass
        if dati.get("ac_modalita_notte") in MODALITA_NOTTE_AC:
            cfg["ac_modalita_notte"] = dati["ac_modalita_notte"]
        for campo, minimo, massimo in (("notte_inizio", 0, 23), ("notte_fine", 0, 23)):
            if campo in dati:
                try:
                    cfg[campo] = max(minimo, min(massimo, int(float(dati[campo]))))
                except (ValueError, TypeError):
                    pass
        if "pausa_manuale_ore" in dati:
            try:
                cfg["pausa_manuale_ore"] = max(0.25, min(24.0, float(dati["pausa_manuale_ore"])))
            except (ValueError, TypeError):
                pass
        if "netatmo_polling_secondi" in dati:
            try:
                cfg["netatmo_polling_secondi"] = int(max(60, min(900, float(dati["netatmo_polling_secondi"]))))
            except (ValueError, TypeError):
                pass

        # Clamp di sicurezza lato server
        cfg["efficienza_caldaia"] = max(0.05, min(1.0, float(cfg.get("efficienza_caldaia") or 0.96)))
        cfg["intervallo_controllo_minuti"] = max(1.0, min(1440.0,
            float(cfg.get("intervallo_controllo_minuti") or 15.0)))
        cfg["soglia_delta_risparmio"] = max(0.0, float(cfg.get("soglia_delta_risparmio") or 0.01))
        cfg["potenza_termica_kw"] = max(0.5, min(30.0, float(cfg.get("potenza_termica_kw") or 4.0)))
        cfg["gas_iva_pct"] = max(0.0, min(100.0, float(cfg.get("gas_iva_pct") or 0.0)))
        cfg["luce_iva_pct"] = max(0.0, min(100.0, float(cfg.get("luce_iva_pct") or 0.0)))
        cfg["gas_commodity_fisso_smc"] = max(0.0, float(cfg.get("gas_commodity_fisso_smc") or 0.0))
        cfg["luce_commodity_fisso_kwh"] = max(0.0, float(cfg.get("luce_commodity_fisso_kwh") or 0.0))
        cfg["pannello_potenza_kw"] = max(0.1, min(100.0, float(cfg.get("pannello_potenza_kw") or 0.9)))
        cfg["pannello_fattore"] = max(0.1, min(2.0, float(cfg.get("pannello_fattore") or 0.9)))
        cfg["pannello_fattore_monoasse"] = max(0.1, min(2.0, float(cfg.get("pannello_fattore_monoasse") or 0.85)))
        cfg["pompa_potenza_elettrica_kw"] = max(0.1, min(10.0, float(cfg.get("pompa_potenza_elettrica_kw") or 1.2)))
        base_kw = cfg.get("consumo_base_kw")
        cfg["consumo_base_kw"] = max(0.0, min(10.0, float(0.3 if base_kw is None else base_kw)))

        cfg["ultima_modifica_fissi"] = datetime.now().strftime("%Y-%m-%d")
        salva_config(cfg)
        _cache_meteo["timestamp"] = 0.0
        _cache_cfr["timestamp"] = 0.0
        dispositivi.invalida("meteo")
        get_servizio().ricalcola()    # zone e soglie nuove al ciclo subito, non tra 15 min
        cambiate = sorted(k for k in set(prima) | set(cfg)
                          if k != "ultima_modifica_fissi" and prima.get(k) != cfg.get(k))
        if cambiate:
            # Solo i nomi delle chiavi: i valori possono contenere segreti
            _registra_comando(f"Configurazione salvata: {', '.join(cambiate)}", dati={"chiavi": cambiate})
        return jsonify({"status": "ok", "messaggio": "Configurazione salvata"})
    return jsonify(maschera_segreti(carica_config()))


SEGRETO_MASCHERATO = "***"


def maschera_segreti(cfg: dict) -> dict:
    """Configurazione senza token, secret e password (sostituiti da '***')."""
    risultato = {}
    for k, v in cfg.items():
        sensibile = any(s in k.lower() for s in ("token", "secret", "password", "subscription_key"))
        risultato[k] = (SEGRETO_MASCHERATO if v else v) if sensibile else v
    return risultato


def _registra_comando(messaggio: str, *, oggetto: Optional[str] = None, dati=None, livello: str = "info") -> None:
    """Riga 'comando' del registro con l'utente che ha fatto l'azione."""
    utente = current_user.username if current_user and current_user.is_authenticated else None
    registro.scrivi("comando", messaggio, livello=livello, oggetto=oggetto, dati=dati, utente=utente)


def _nome_dispositivo(cfg: dict, tipo: str, ident: str) -> str:
    snap = dispositivi.snapshot(cfg)
    if tipo == "ac":
        return (snap["ac"].get(ident) or {}).get("nome") or "Condizionatore"
    if tipo == "stanza":
        return (snap["stanze"].get(ident) or {}).get("nome") or f"Stanza {ident}"
    return (snap.get("casa") or {}).get("name") or "Casa"


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
        # Termostati al programma e AC accesi da TermoPilota spenti, senza far
        # aspettare la risposta alle chiamate verso i dispositivi
        threading.Thread(target=servizio.rilascia_tutto, daemon=True, name="rilascio").start()
    _registra_comando("Automazione " + ("attivata" if attiva_ora else "disattivata"))
    return jsonify({"automazione_attiva": attiva_ora})


def _json_richiesto():
    """Corpo JSON della richiesta, o (None, risposta 415). Un form di un altro
    sito non puo' inviare application/json senza preflight CORS."""
    if not request.is_json:
        return None, (jsonify({"errore": "Serve una richiesta JSON"}), 415)
    dati = request.get_json(silent=True)
    if not isinstance(dati, dict):
        return None, (jsonify({"errore": "Corpo JSON non valido"}), 400)
    return dati, None


def _zona_configurata(cfg: dict, room_id: str) -> Optional[dict]:
    return next((z for z in cfg.get("zone", []) if room_id and z.get("room_id") == room_id), None)


def _pausa_dopo_comando(cfg: dict, zone: list, ore=None) -> Optional[dict]:
    """Un comando manuale su un dispositivo di zone automatizzate le mette in
    pausa, altrimenti il ciclo successivo annullerebbe la modifica."""
    if not cfg.get("automazione_attiva"):
        return None
    zone = [z for z in zone if z.get("automazione", True) is not False and z.get("room_id")]
    if not zone:
        return None
    ore = _limita(cfg.get("pausa_manuale_ore", 3.0) if ore is None else ore, 3.0, 0.0, 24.0)
    if ore <= 0:
        return None
    fine = get_servizio().imposta_pausa([z["room_id"] for z in zone], ore)
    return {"fino": fine, "zone": [z.get("nome", "Zona") for z in zone]}


@app.route("/api/automazione/zona/<room_id>/pausa", methods=["POST"])
@login_required
def api_zona_pausa(room_id):
    dati, errore = _json_richiesto()
    if errore:
        return errore
    zona = _zona_configurata(carica_config(), room_id)
    if not zona:
        return jsonify({"errore": "Stanza non trovata"}), 404
    ore = _limita(dati.get("ore"), -1, 0.0, 24.0)
    if ore < 0:
        return jsonify({"errore": "ore deve essere un numero tra 0 e 24"}), 400
    fine = get_servizio().imposta_pausa([room_id], ore)
    _registra_comando(f"Stanza in pausa per {ore:g} h" if ore > 0 else "Pausa annullata", oggetto=zona.get("nome"))
    return jsonify({"pausa_fino": fine})


@app.route("/api/automazione/zona/<room_id>/attiva", methods=["POST"])
@login_required
def api_zona_attiva(room_id):
    dati, errore = _json_richiesto()
    if errore:
        return errore
    cfg = carica_config()
    zona = _zona_configurata(cfg, room_id)
    if not zona:
        return jsonify({"errore": "Stanza non trovata"}), 404
    zona["automazione"] = bool(dati.get("attiva"))
    salva_config(cfg)
    get_servizio().zona_modificata(room_id, zona["automazione"])
    _registra_comando("Stanza inclusa nell'automazione" if zona["automazione"] else "Stanza esclusa dall'automazione",
                      oggetto=zona.get("nome"))
    return jsonify({"automazione": zona["automazione"]})


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


@app.route("/api/dispositivi/impianti")
@login_required
def api_dispositivi_impianti():
    return jsonify(scopri_impianti(carica_config()))


@app.route("/api/dispositivi/termostati")
@login_required
def api_dispositivi_termostati():
    return jsonify(scopri_termostati(carica_config()))


@app.route("/api/dispositivi/condizionatori")
@login_required
def api_dispositivi_condizionatori():
    return jsonify(scopri_condizionatori(carica_config()))


# ─── Pagina Dispositivi: stato completo e comandi manuali ────────────────────

@app.route("/api/dispositivi/stato")
@login_required
def api_dispositivi_stato():
    cfg = carica_config()
    riepilogo = dispositivi.riepilogo(dispositivi.snapshot(cfg))
    oggi = datetime.now().date().isoformat()
    kwh_oggi = {e["id"]: e["kwh"] for e in storico.energia_ac(oggi, oggi, "giornaliera")}
    for ac in riepilogo["ac"]:
        ac["kwh_oggi"] = kwh_oggi.get(ac["id"])
        ac["zone"] = [z.get("nome") for z in dispositivi.zone_collegate(cfg, "ac", ac["id"])]
    for st in riepilogo["stanze"]:
        st["zone"] = [z.get("nome") for z in dispositivi.zone_collegate(cfg, "stanza", st["id"])]
    modulo = dispositivi.modulo_meteo(dispositivi.snapshot(cfg), cfg.get("meteo_modulo_id", ""))
    for m in riepilogo["meteo"]:
        m["in_uso"] = bool(modulo) and m["id"] == modulo["id"]
    return jsonify(riepilogo)


@app.route("/api/dispositivi/<tipo>/<ident>")
@login_required
def api_dispositivo(tipo, ident):
    if tipo not in TIPI_DISPOSITIVO:
        return jsonify({"errore": "Tipo di dispositivo non valido"}), 404
    cfg = carica_config()
    snap = dispositivi.snapshot(cfg)
    decisioni = {z.get("room_id"): z for z in get_servizio().stato()["zone"]}
    zone = dispositivi.zone_collegate(cfg, tipo, ident)
    # L'ora dell'ultima lettura riuscita della parte che contiene il dispositivo
    parte = {"ac": "ac", "meteo": "meteo"}.get(tipo, "netatmo")
    letto_alle = (snap.get("letti_alle") or {}).get(parte)
    risposta = {"tipo": tipo, "id": ident, "errori": snap["errori"], "letto_alle": letto_alle,
                "zone": [{"nome": z.get("nome"), "room_id": z.get("room_id"),
                          "automazione": z.get("automazione", True) is not False,
                          "decisione": decisioni.get(z.get("room_id"))} for z in zone],
                "is_admin": bool(current_user.is_admin)}
    if tipo == "ac":
        ac = snap["ac"].get(ident)
        if not ac:
            return jsonify({"errore": "Condizionatore non trovato", **risposta}), 404
        risposta.update({"nome": ac["nome"], "stato": ac["stato"], "grezzo": ac["grezzo"],
                         "ocf": ac["ocf"], "errore": ac["errore"],
                         "controlli": dispositivi.controlli_ac(cfg, ident, current_user.is_admin)})
    elif tipo == "stanza":
        st = snap["stanze"].get(ident)
        if not st:
            return jsonify({"errore": "Stanza non trovata", **risposta}), 404
        risposta.update({"nome": st["nome"],
                         "stato": {k: v for k, v in st.items() if k not in ("grezzo", "moduli", "_campi")},
                         "moduli": [{k: v for k, v in m.items() if k != "grezzo"} for m in st["moduli"]],
                         "grezzo": {"stanza": st["grezzo"], "moduli": [m["grezzo"] for m in st["moduli"]]},
                         "casa": {k: v for k, v in (snap["casa"] or {}).items() if k != "grezzo"}})
    elif tipo == "meteo":
        m = (snap.get("meteo") or {}).get(ident)
        if not m:
            return jsonify({"errore": "Modulo esterno non trovato", **risposta}), 404
        in_uso = dispositivi.modulo_meteo(snap, cfg.get("meteo_modulo_id", ""))
        risposta.update({"nome": m["nome"], "errore": m["errore"],
                         "stato": {k: v for k, v in m.items() if k not in ("grezzo", "_campi")}
                                  | {"in_uso": bool(in_uso) and in_uso["id"] == ident},
                         "grezzo": m["grezzo"],
                         "cfr": {"id": cfg.get("cfr_station_id", ""), "nome": cfg.get("cfr_station_name", "")}})
    else:
        casa = snap["casa"]
        if not casa or casa.get("id") != ident:
            return jsonify({"errore": "Casa non trovata", **risposta}), 404
        risposta.update({"nome": casa.get("name") or "Casa",
                         "stato": {k: v for k, v in casa.items() if k != "grezzo"},
                         "grezzo": casa["grezzo"]})
    return jsonify(risposta)


@app.route("/api/dispositivi/<tipo>/<ident>/storico")
@login_required
def api_dispositivo_storico(tipo, ident):
    if tipo not in ("ac", "stanza", "meteo"):
        return jsonify({"errore": "Storico disponibile solo per condizionatori, stanze e stazione meteo"}), 404
    oggi = datetime.now().date()
    da = request.args.get("da", (oggi - timedelta(days=1)).isoformat())
    a = request.args.get("a", oggi.isoformat())
    risoluzione = request.args.get("risoluzione", "grezza")
    if risoluzione not in ("grezza", "oraria", "giornaliera"):
        return jsonify({"errore": "risoluzione deve essere 'grezza', 'oraria' o 'giornaliera'"}), 400
    try:
        datetime.strptime(da, "%Y-%m-%d")
        datetime.strptime(a, "%Y-%m-%d")
    except ValueError:
        return jsonify({"errore": "date nel formato YYYY-MM-DD"}), 400
    punti = storico.leggi_letture(tipo, ident, da, a, risoluzione)
    risposta = {"da": da, "a": a, "risoluzione": risoluzione, "punti": punti}
    if tipo == "ac" and risoluzione == "grezza":
        risposta["energia"] = storico.energia_ac(da, a, "oraria", ident)
    station_id = carica_config().get("cfr_station_id", "")
    if tipo == "meteo" and station_id:
        # La stazione CFR, per il confronto con il modulo esterno
        risposta["cfr"] = storico.leggi_letture("meteo", f"cfr:{station_id}", da, a, risoluzione)
    return jsonify(risposta)


def _esito_comando(funzione, descrizione: Optional[str] = None, oggetto: Optional[str] = None, dati=None):
    """Esegue un comando; con `descrizione` lo registra (riuscito o fallito)."""
    try:
        esito = funzione()
    except dispositivi.ErroreComando as e:
        if descrizione:
            _registra_comando(f"{descrizione}: non riuscito ({e})", oggetto=oggetto, dati=dati, livello="warning")
        return None, (jsonify({"errore": str(e)}), e.codice)
    except Exception as e:
        logger.warning("Comando dispositivo fallito: %s", e)
        if descrizione:
            _registra_comando(f"{descrizione}: non riuscito ({e})", oggetto=oggetto, dati=dati, livello="warning")
        return None, (jsonify({"errore": f"Dispositivo non raggiungibile: {e}"}), 502)
    if descrizione:
        _registra_comando(descrizione, oggetto=oggetto, dati=dati)
    return esito, None


@app.route("/api/dispositivi/ac/<ident>/comando", methods=["POST"])
@login_required
def api_comando_ac(ident):
    dati, errore = _json_richiesto()
    if errore:
        return errore
    cfg = carica_config()
    esito, errore = _esito_comando(lambda: dispositivi.comando_ac(
        cfg, ident, str(dati.get("chiave", "")), dati.get("valore"), current_user.is_admin),
        f"Comando {dati.get('chiave')} = {dati.get('valore')}", _nome_dispositivo(cfg, "ac", ident),
        {"device_id": ident})
    if errore:
        return errore
    pausa = _pausa_dopo_comando(cfg, dispositivi.zone_collegate(cfg, "ac", ident), dati.get("pausa_ore"))
    return jsonify({"status": "ok", "comando": esito, "pausa": pausa})


@app.route("/api/dispositivi/stanza/<ident>/setpoint", methods=["POST"])
@login_required
def api_setpoint_stanza(ident):
    dati, errore = _json_richiesto()
    if errore:
        return errore
    cfg = carica_config()
    fine, errore = _esito_comando(lambda: dispositivi.setpoint_stanza(
        cfg, ident, dati.get("temp"), dati.get("durata_min", 180)),
        f"Temperatura manuale {dati.get('temp')} °C per {dati.get('durata_min', 180)} min",
        _nome_dispositivo(cfg, "stanza", ident), {"room_id": ident})
    if errore:
        return errore
    pausa = _pausa_dopo_comando(cfg, dispositivi.zone_collegate(cfg, "stanza", ident),
                                max(0.25, (fine - time.time()) / 3600))
    return jsonify({"status": "ok", "fine": fine, "pausa": pausa})


@app.route("/api/dispositivi/stanza/<ident>/ripristina", methods=["POST"])
@login_required
def api_ripristina_stanza(ident):
    _, errore = _json_richiesto()
    if errore:
        return errore
    cfg = carica_config()
    _, errore = _esito_comando(lambda: dispositivi.ripristina_stanza(cfg, ident), "Ritorno al programma",
                               _nome_dispositivo(cfg, "stanza", ident), {"room_id": ident})
    if errore:
        return errore
    pausa = _pausa_dopo_comando(cfg, dispositivi.zone_collegate(cfg, "stanza", ident))
    return jsonify({"status": "ok", "pausa": pausa})


@app.route("/api/dispositivi/casa/modalita", methods=["POST"])
@login_required
def api_modalita_casa():
    dati, errore = _json_richiesto()
    if errore:
        return errore
    cfg = carica_config()
    _, errore = _esito_comando(lambda: dispositivi.modalita_casa(
        cfg, str(dati.get("modalita", "")), dati.get("durata_min")),
        f"Modalità casa {dati.get('modalita')}", _nome_dispositivo(cfg, "casa", ""))
    if errore:
        return errore
    return jsonify({"status": "ok"})


@app.route("/api/dispositivi/casa/programma", methods=["POST"])
@login_required
def api_programma_casa():
    if not current_user.is_admin:
        return jsonify({"errore": "Solo gli amministratori possono cambiare programma"}), 403
    dati, errore = _json_richiesto()
    if errore:
        return errore
    cfg = carica_config()
    _, errore = _esito_comando(lambda: dispositivi.programma_casa(cfg, str(dati.get("schedule_id", ""))),
                               "Cambio programma", _nome_dispositivo(cfg, "casa", ""),
                               {"schedule_id": dati.get("schedule_id")})
    if errore:
        return errore
    return jsonify({"status": "ok"})


@app.route("/api/dispositivi/stanza/<ident>/boost", methods=["POST"])
@login_required
def api_boost_stanza(ident):
    dati, errore = _json_richiesto()
    if errore:
        return errore
    cfg = carica_config()
    fine, errore = _esito_comando(lambda: dispositivi.boost_stanza(cfg, ident, dati.get("durata_min", 30)),
                                  f"Boost per {dati.get('durata_min', 30)} min",
                                  _nome_dispositivo(cfg, "stanza", ident), {"room_id": ident})
    if errore:
        return errore
    pausa = _pausa_dopo_comando(cfg, dispositivi.zone_collegate(cfg, "stanza", ident),
                                max(0.25, (fine - time.time()) / 3600))
    return jsonify({"status": "ok", "fine": fine, "pausa": pausa})


@app.route("/api/dispositivi/ac/<ident>/avanzati")
@login_required
def api_comandi_avanzati_ac(ident):
    if not current_user.is_admin:
        return jsonify({"errore": "Comandi avanzati riservati agli amministratori"}), 403
    comandi, errore = _esito_comando(lambda: dispositivi.comandi_avanzati_ac(carica_config(), ident))
    if errore:
        return errore
    return jsonify({"comandi": comandi})


@app.route("/api/dispositivi/ac/<ident>/avanzato", methods=["POST"])
@login_required
def api_comando_avanzato_ac(ident):
    if not current_user.is_admin:
        return jsonify({"errore": "Comandi avanzati riservati agli amministratori"}), 403
    dati, errore = _json_richiesto()
    if errore:
        return errore
    cfg = carica_config()
    esito, errore = _esito_comando(lambda: dispositivi.comando_avanzato_ac(
        cfg, ident, str(dati.get("capability", "")), str(dati.get("comando", "")), dati.get("argomenti", [])),
        f"Comando avanzato {dati.get('capability')}.{dati.get('comando')}{dati.get('argomenti', [])}",
        _nome_dispositivo(cfg, "ac", ident), {"device_id": ident})
    if errore:
        return errore
    pausa = _pausa_dopo_comando(cfg, dispositivi.zone_collegate(cfg, "ac", ident), dati.get("pausa_ore"))
    return jsonify({"status": "ok", "comando": esito, "pausa": pausa})


# ─── Aggiornamenti live (webhook) ────────────────────────────────────────────

def _ricalcolo_per(cfg: dict, tipo: str, idents: list):
    """Funzione che anticipa il ciclo, se gli eventi toccano zone automatizzate."""
    if not cfg.get("automazione_attiva"):
        return None
    campo = "ac_device_id" if tipo == "ac" else "room_id"
    if any(z.get(campo) in idents and z.get("automazione", True) is not False for z in cfg.get("zone", [])):
        return get_servizio().ricalcola
    return None


@app.route("/api/live")
@login_required
def api_live():
    return jsonify(live.stato())


@app.route("/api/webhook/netatmo", methods=["POST"])
def api_webhook_netatmo():
    """Eventi Netatmo (pubblico, firmato con il client secret: X-Netatmo-secret)."""
    cfg = carica_config()
    corpo = request.get_data(cache=False)
    firma = request.headers.get("X-Netatmo-secret")
    try:
        evento = json.loads(corpo or b"{}")
    except ValueError:
        evento = {}
    if not isinstance(evento, dict):
        evento = {"corpo": corpo[:2000].decode("utf-8", "replace")}
    if not netatmo_firma_valida(cfg.get("legrand_client_secret", ""), corpo, firma):
        live.rifiutato("netatmo", "firma non valida" if firma else "firma assente")
        registro.scrivi("sistema", "Webhook Netatmo rifiutato: " + ("firma non valida" if firma else "firma assente"),
                        livello="warning", dati={"content_type": request.content_type, "byte": len(corpo),
                                                 "header_firma": bool(firma), "corpo": evento})
        return jsonify({"errore": "firma non valida"}), 403
    home_id = evento.get("home_id") or (evento.get("home") or {}).get("id")
    if home_id and home_id != cfg.get("legrand_plant_id"):
        registro.scrivi("evento", "Webhook Netatmo di un'altra casa, ignorato", livello="debug", dati=evento)
        return jsonify({"status": "ignorato"})
    tipo = evento.get("event_type") or evento.get("push_type") or "evento"
    if tipo == "webhook_activation":
        adesso = time.time()
        def conferma(c):
            # La conferma arriva solo dopo una registrazione: il webhook e' attivo
            w = c.setdefault("netatmo_webhook", {})
            w.update({"attivo": True, "dal": w.get("dal") or adesso, "confermato": adesso})
        aggiorna_config_atomico(CONFIG_FILE, conferma)
        registro.scrivi("evento", "Netatmo ha confermato la registrazione delle notifiche",
                        dati={"sorgente": "webhook", **evento})
        return jsonify({"status": "ok"})
    idents = stanze_evento_netatmo(evento)
    nomi = {rid: (dispositivi.snapshot(cfg)["stanze"].get(rid) or {}).get("nome", rid) for rid in idents}
    registro.scrivi("evento", f"Netatmo (webhook): {tipo}", oggetto=", ".join(nomi.values()) or None,
                    dati={"sorgente": "webhook", **evento})
    live.notifica("netatmo", idents or [home_id or "casa"], lambda: dispositivi.invalida("netatmo"),
                  _ricalcolo_per(cfg, "stanza", idents) if idents else None)
    return jsonify({"status": "ok"})


def stanze_evento_netatmo(evento: dict) -> list:
    """room_id citati in un evento Netatmo, anche in campi annidati (rooms, home.rooms)."""
    trovate = []

    def aggiungi(valore):
        if isinstance(valore, (str, int)) and str(valore) not in trovate:
            trovate.append(str(valore))
    aggiungi(evento.get("room_id"))
    for contenitore in (evento, evento.get("home") or {}):
        stanza = contenitore.get("room")
        if isinstance(stanza, dict):
            aggiungi(stanza.get("id"))
        for s in contenitore.get("rooms") or []:
            if isinstance(s, dict):
                aggiungi(s.get("id"))
    return trovate


def _host_smartthings(url: str) -> bool:
    parti = urlparse(url or "")
    return parti.scheme == "https" and (parti.hostname or "").endswith(".smartthings.com")


@app.route("/api/webhook/smartthings/<token>", methods=["POST"])
def api_webhook_smartthings(token):
    """Eventi delle sottoscrizioni SmartThings (pubblico: protetto dal token nell'URL
    e dall'installedAppId; i dati si rileggono comunque dall'API)."""
    cfg = carica_config()
    atteso = cfg.get("smartthings_webhook_token") or ""
    if not atteso or not hmac.compare_digest(token, atteso):
        return jsonify({"errore": "non trovato"}), 404
    dati = request.get_json(force=True, silent=True) or {}
    tipo = dati.get("messageType") or dati.get("lifecycle")
    if tipo == "CONFIRMATION":
        url = (dati.get("confirmationData") or {}).get("confirmationUrl", "")
        if not _host_smartthings(url):
            live.rifiutato("smartthings", "URL di conferma non SmartThings")
            registro.scrivi("sistema", "Conferma SmartThings rifiutata: URL non SmartThings",
                            livello="warning", dati={"url": url})
            return jsonify({"errore": "URL di conferma non valido"}), 400
        try:
            requests.get(url, timeout=10).raise_for_status()
        except Exception as e:
            live.rifiutato("smartthings", f"conferma fallita: {e}")
            registro.scrivi("sistema", f"Conferma del Target URL SmartThings fallita: {e}", livello="warning")
            return jsonify({"errore": "conferma fallita"}), 502
        registro.scrivi("evento", "Target URL SmartThings confermato", dati={"sorgente": "webhook"})
        return jsonify({"targetUrl": request.base_url})
    if tipo == "PING":
        registro.scrivi("evento", "Ping SmartThings", livello="debug")
        return jsonify({"pingData": dati.get("pingData")})
    if tipo != "EVENT":
        registro.scrivi("evento", f"Messaggio SmartThings ignorato: {tipo}", livello="debug", dati=dati)
        return jsonify({"status": "ignorato"})
    evento = dati.get("eventData") or {}
    installata = ((evento.get("installedApp") or {}).get("installedAppId")
                  or (dati.get("installedApp") or {}).get("installedAppId"))
    nostra = (cfg.get("smartthings_token_data") or {}).get("installed_app_id")
    if nostra and installata and installata != nostra:
        live.rifiutato("smartthings", "installedAppId diverso")
        registro.scrivi("sistema", "Evento SmartThings rifiutato: installedAppId diverso", livello="warning")
        return jsonify({"errore": "app non riconosciuta"}), 403
    ac = dispositivi.snapshot(cfg)["ac"]
    per_dispositivo: dict = {}
    for e in evento.get("events", []) or []:
        d = e.get("deviceEvent") or {}
        if d.get("deviceId") in ac:
            per_dispositivo.setdefault(d["deviceId"], []).append(d)
    da_aggiornare, comandati = [], []
    for device_id, cambi in per_dispositivo.items():
        tipo = tipo_eventi_smartthings(cambi)
        valori = ", ".join(f"{c.get('attribute')} {_valore_evento(c.get('value'))}"
                           f"{(' ' + c['unit']) if c.get('unit') else ''}" for c in cambi[:12])
        registro.scrivi("evento", f"SmartThings: {valori}", oggetto=ac[device_id].get("nome"),
                        livello="info" if tipo == "comando" else "debug",
                        dati={"sorgente": "webhook", "tipo": tipo, "eventi": [
                            {k: c.get(k) for k in ("capability", "attribute", "value", "unit", "componentId",
                                                   "stateChange")} for c in cambi]})
        if tipo != "altro":
            da_aggiornare.append(device_id)
        if tipo == "comando":
            comandati.append(device_id)
    if da_aggiornare:
        # Solo un cambio di comando (accensione, modalita', temperatura...) puo' far
        # ripartire l'automazione; le misure aggiornano solo le pagine
        live.notifica("smartthings", sorted(da_aggiornare), lambda: dispositivi.invalida("ac"),
                      _ricalcolo_per(cfg, "ac", comandati) if comandati else None)
    return jsonify({"eventData": {}})


# Attributi SmartThings che riflettono un comando (dall'app, dal telecomando o
# nostro) e misure che vale la pena mostrare subito; il resto (avanzamento
# della pulizia, diagnostica...) va nel registro come debug e basta
ATTRIBUTI_COMANDO = {
    "switch", "airConditionerMode", "coolingSetpoint", "fanMode", "fanOscillationMode", "acOptionalMode",
    "lighting", "autoCleaningMode", "beep", "volume", "airConditionerOdorControllerState",
    "acTropicalNightModeLevel", "alarmThreshold",
}
ATTRIBUTI_MISURA = {"temperature", "humidity", "powerConsumption", "dustFilterStatus"}


def tipo_eventi_smartthings(cambi: list) -> str:
    """'comando', 'misura' o 'altro' per un gruppo di eventi dello stesso AC."""
    attributi = {c.get("attribute") for c in cambi}
    if attributi & ATTRIBUTI_COMANDO:
        return "comando"
    if attributi & ATTRIBUTI_MISURA:
        return "misura"
    return "altro"


def _valore_evento(valore) -> str:
    """Valore leggibile nel messaggio: i dict (es. powerConsumption) riassunti."""
    if isinstance(valore, dict):
        if "energy" in valore:
            return f"{valore.get('energy')} Wh (potenza {valore.get('power')} W)"
        return "{…}"
    return str(valore)


def _url_webhook_smartthings(cfg: dict) -> str:
    token = cfg.get("smartthings_webhook_token")
    if not token:
        token = secrets.token_urlsafe(24)
        aggiorna_config_atomico(CONFIG_FILE, lambda c: c.setdefault("smartthings_webhook_token", token))
        token = carica_config().get("smartthings_webhook_token", token)
    return request.url_root.rstrip("/") + f"/api/webhook/smartthings/{token}"


@app.route("/api/live/configurazione")
@login_required
def api_live_configurazione():
    if not current_user.is_admin:
        return jsonify({"errore": "Solo gli amministratori"}), 403
    cfg = carica_config()
    st = get_heatpump("smartthings", cfg)
    oauth = bool(st and getattr(st, "installed_app_id", ""))
    return jsonify({
        "netatmo_url": request.url_root.rstrip("/") + "/api/webhook/netatmo",
        "smartthings_url": _url_webhook_smartthings(cfg),
        "smartthings_app_id": (cfg.get("smartthings_token_data") or {}).get("app_id"),
        "smartthings_oauth": oauth,
        "stato_netatmo": stato_notifiche_netatmo(cfg),
        "stato_smartthings": stato_sottoscrizioni_smartthings(cfg, st) if oauth else None,
        "chiamate": conteggio_chiamate(),
        **live.stato(),
    })


def stato_notifiche_netatmo(cfg: dict) -> dict:
    """Netatmo non permette di sapere se il webhook e' registrato: vale quello che
    abbiamo fatto noi (attivo, da quando) e la conferma che Netatmo manda."""
    w = cfg.get("netatmo_webhook") or {}
    confermato = w.get("confermato")
    return {"attivo": bool(w.get("attivo")), "dal": w.get("dal"),
            "confermato": confermato if confermato and confermato >= (w.get("dal") or 0) - 5 else None}


def stato_sottoscrizioni_smartthings(cfg: dict, st) -> dict:
    """Sottoscrizioni attive lette dall'API: lo stato vero, non quello ricordato."""
    ac = set(dispositivi.snapshot(cfg)["ac"])
    try:
        sottoscritti = {(s.get("device") or {}).get("deviceId") for s in st.sottoscrizioni()}
    except Exception as e:
        return {"attivo": None, "errore": str(e), "sottoscritti": 0, "condizionatori": len(ac)}
    coperti = len(ac & sottoscritti)
    return {"attivo": bool(ac) and coperti == len(ac), "parziale": 0 < coperti < len(ac),
            "sottoscritti": coperti, "condizionatori": len(ac), "errore": None}


@app.route("/api/live/<sorgente>/<azione>", methods=["POST"])
@login_required
def api_live_azione(sorgente, azione):
    if not current_user.is_admin:
        return jsonify({"errore": "Solo gli amministratori"}), 403
    if sorgente not in ("netatmo", "smartthings") or azione not in ("attiva", "disattiva"):
        return jsonify({"errore": "Azione non valida"}), 404
    _, errore = _json_richiesto()
    if errore:
        return errore
    cfg = carica_config()

    def esegui():
        if sorgente == "netatmo":
            bt = get_thermostat("netatmo", cfg)
            if not bt or not bt.autenticato:
                raise dispositivi.ErroreComando("Netatmo non collegato", 409)
            try:
                if azione == "attiva":
                    bt.registra_webhook(request.url_root.rstrip("/") + "/api/webhook/netatmo")
                else:
                    bt.rimuovi_webhook()
            except ErroreNetatmo as e:
                raise dispositivi.ErroreComando(f"Netatmo: {e}", 502)
            stato = {"attivo": azione == "attiva", "dal": time.time() if azione == "attiva" else None,
                     "confermato": None}
            aggiorna_config_atomico(CONFIG_FILE, lambda c: c.update({"netatmo_webhook": stato}))
            return None
        st = get_heatpump("smartthings", cfg)
        if not st or not getattr(st, "installed_app_id", ""):
            raise dispositivi.ErroreComando("Serve il collegamento OAuth a SmartThings", 409)
        if azione == "disattiva":
            st.rimuovi_sottoscrizioni()
            return None
        st.rimuovi_sottoscrizioni()     # niente doppioni
        for ac_id in dispositivi.snapshot(cfg)["ac"]:
            st.sottoscrivi_dispositivo(ac_id)
        return len(st.sottoscrizioni())

    esito, errore = _esito_comando(
        esegui, f"Notifiche {sorgente} " + ("attivate" if azione == "attiva" else "disattivate"))
    if errore:
        return errore
    return jsonify({"status": "ok", "sottoscrizioni": esito})


@app.route("/api/live/smartthings/rigenera-token", methods=["POST"])
@login_required
def api_live_rigenera_token():
    """Nuovo token per l'URL del webhook SmartThings: il vecchio smette di funzionare
    e il Target URL dell'app va reimpostato con la CLI."""
    if not current_user.is_admin:
        return jsonify({"errore": "Solo gli amministratori"}), 403
    _, errore = _json_richiesto()
    if errore:
        return errore
    nuovo = secrets.token_urlsafe(24)
    aggiorna_config_atomico(CONFIG_FILE, lambda c: c.update({"smartthings_webhook_token": nuovo}))
    _registra_comando("Token del webhook SmartThings rigenerato: reimpostare il Target URL")
    return jsonify({"status": "ok", "smartthings_url": request.url_root.rstrip("/")
                    + f"/api/webhook/smartthings/{nuovo}"})


# ─── Stanze (zone): pagina, storico combinato, gestione ─────────────────────

SETPOINT_TERMOSTATO_ESCLUSIVA = 7.0


def _calcolo_stanza(cfg: dict, zona: dict, stanza: dict) -> dict:
    """I setpoint che l'automazione usa per questa stanza, spiegati."""
    target = stanza.get("target")
    if target is None:
        target = stanza.get("setpoint")
    offset = zona.get("offset_ac", 0.0) or 0.0
    riserva = zona.get("riserva_gas_delta", 1.5)
    riserva = 1.5 if riserva is None else riserva
    affiancata = zona.get("modalita") == "affiancata"
    acid = zona.get("ac_device_id", "")
    condivisa = [z.get("nome", "Stanza") for z in cfg.get("zone", [])
                 if acid and z.get("ac_device_id") == acid and z.get("room_id") != zona.get("room_id")]
    return {
        "target": target,
        "offset_ac": offset,
        "setpoint_ac_previsto": round(target + offset, 1) if target is not None else None,
        "modalita": "affiancata" if affiancata else "esclusiva",
        "riserva_gas_delta": riserva,
        "setpoint_termostato_in_ac": (max(SETPOINT_TERMOSTATO_ESCLUSIVA, round((target - riserva) * 2) / 2)
                                      if affiancata and target is not None else SETPOINT_TERMOSTATO_ESCLUSIVA),
        "condiviso_con": condivisa,
    }


def _opzioni_stanze(cfg: dict, snap: dict, escludi_room: str = "") -> dict:
    """Stanze Netatmo con termostato e condizionatori per creare o modificare una stanza."""
    usate = {z.get("room_id"): z.get("nome") for z in cfg.get("zone", []) if z.get("room_id") != escludi_room}
    return {
        "termostati": [{"id": rid, "nome": s.get("nome") or rid, "usata_da": usate.get(rid)}
                       for rid, s in snap.get("stanze", {}).items()],
        "condizionatori": [{"id": acid, "nome": a.get("nome") or acid,
                            "usato_da": [z.get("nome") for z in cfg.get("zone", [])
                                         if z.get("ac_device_id") == acid and z.get("room_id") != escludi_room]}
                           for acid, a in snap.get("ac", {}).items()],
    }


@app.route("/api/stanze/<room_id>")
@login_required
def api_stanza(room_id):
    cfg = carica_config()
    zona = _zona_configurata(cfg, room_id)
    if not zona:
        return jsonify({"errore": "Stanza non trovata"}), 404
    snap = dispositivi.snapshot(cfg)
    stanza = {k: v for k, v in (snap["stanze"].get(room_id) or {}).items() if k not in ("grezzo", "_campi", "moduli")}
    acid = zona.get("ac_device_id", "")
    ac_snap = snap["ac"].get(acid) if acid else None
    servizio = get_servizio().stato()
    decisione = next((z for z in servizio["zone"] if z.get("room_id") == room_id), None)
    oggetti = [o for o in dict.fromkeys([zona.get("nome"), stanza.get("nome"),
                                         (ac_snap or {}).get("nome")]) if o]
    zona = normalizza_zone([zona])[0]       # valori di default per le zone vecchie
    risposta = {
        "zona": zona,
        "stanza": stanza or None,
        "ac": ({"id": acid, "nome": ac_snap["nome"], "stato": ac_snap["stato"], "errore": ac_snap["errore"],
                "controlli": dispositivi.controlli_ac(cfg, acid, current_user.is_admin)} if ac_snap else None),
        "decisione": decisione,
        "pausa_fino": servizio.get("pause", {}).get(room_id),
        "automazione_attiva": servizio["attiva"],
        "calcolo": _calcolo_stanza(cfg, zona, stanza),
        "differenza_sensori": storico.differenza_sensori(room_id, acid),
        "oggetti_registro": oggetti,
        "errori": snap["errori"],
        "letto_alle": snap["letto_alle"],
        "letti_alle": snap.get("letti_alle", {}),
        "is_admin": bool(current_user.is_admin),
    }
    if current_user.is_admin:
        risposta["opzioni"] = _opzioni_stanze(cfg, snap, room_id)
    return jsonify(risposta)


@app.route("/api/stanze/<room_id>/storico")
@login_required
def api_stanza_storico(room_id):
    zona = _zona_configurata(carica_config(), room_id)
    if not zona:
        return jsonify({"errore": "Stanza non trovata"}), 404
    oggi = datetime.now().date()
    da = request.args.get("da", (oggi - timedelta(days=1)).isoformat())
    a = request.args.get("a", oggi.isoformat())
    risoluzione = request.args.get("risoluzione", "grezza")
    if risoluzione not in ("grezza", "oraria", "giornaliera"):
        return jsonify({"errore": "risoluzione deve essere 'grezza', 'oraria' o 'giornaliera'"}), 400
    try:
        datetime.strptime(da, "%Y-%m-%d")
        datetime.strptime(a, "%Y-%m-%d")
    except ValueError:
        return jsonify({"errore": "date nel formato YYYY-MM-DD"}), 400
    acid = zona.get("ac_device_id", "")
    return jsonify({
        "da": da, "a": a, "risoluzione": risoluzione,
        "stanza": storico.leggi_letture("stanza", room_id, da, a, risoluzione),
        "ac": storico.leggi_letture("ac", acid, da, a, risoluzione) if acid else [],
    })


def _richiesta_admin_json():
    if not current_user.is_admin:
        return None, (jsonify({"errore": "Solo gli amministratori possono gestire le stanze"}), 403)
    return _json_richiesto()


def _valida_dispositivi_stanza(cfg: dict, snap: dict, room_id: str, ac_id: str, escludi_room: str = ""):
    """Messaggio d'errore, o None se termostato e condizionatore sono validi."""
    if not room_id or room_id not in snap.get("stanze", {}):
        return "Termostato non trovato tra le stanze Netatmo"
    if any(z.get("room_id") == room_id and room_id != escludi_room for z in cfg.get("zone", [])):
        return "Questo termostato è già usato da un'altra stanza"
    if ac_id and ac_id not in snap.get("ac", {}):
        return "Condizionatore non trovato"
    return None


@app.route("/api/stanze", methods=["POST"])
@login_required
def api_crea_stanza():
    dati, errore = _richiesta_admin_json()
    if errore:
        return errore
    cfg = carica_config()
    snap = dispositivi.snapshot(cfg)
    room_id, ac_id = str(dati.get("room_id") or ""), str(dati.get("ac_device_id") or "")
    problema = _valida_dispositivi_stanza(cfg, snap, room_id, ac_id)
    if problema:
        return jsonify({"errore": problema}), 400
    nome = str(dati.get("nome") or "").strip() or snap["stanze"][room_id].get("nome") or "Stanza"
    [zona] = normalizza_zone([{**dati, "nome": nome, "room_id": room_id, "ac_device_id": ac_id}])
    cfg["zone"] = (cfg.get("zone") or []) + [zona]
    salva_config(cfg)
    _registra_comando("Stanza creata", oggetto=zona["nome"],
                      dati={"room_id": room_id, "ac_device_id": ac_id})
    get_servizio().ricalcola()
    return jsonify({"status": "ok", "room_id": room_id})


@app.route("/api/stanze/<room_id>", methods=["POST"])
@login_required
def api_modifica_stanza(room_id):
    dati, errore = _richiesta_admin_json()
    if errore:
        return errore
    cfg = carica_config()
    zona = _zona_configurata(cfg, room_id)
    if not zona:
        return jsonify({"errore": "Stanza non trovata"}), 404
    snap = dispositivi.snapshot(cfg)
    nuovo_room = str(dati.get("room_id") or room_id)
    nuovo_ac = str(dati["ac_device_id"] or "") if "ac_device_id" in dati else zona.get("ac_device_id", "")
    if nuovo_room != room_id or nuovo_ac != zona.get("ac_device_id", ""):
        problema = _valida_dispositivi_stanza(cfg, snap, nuovo_room, nuovo_ac, escludi_room=room_id)
        if problema:
            return jsonify({"errore": problema}), 400
    [aggiornata] = normalizza_zone([{**zona, **dati, "room_id": nuovo_room, "ac_device_id": nuovo_ac}])
    vecchio_ac = zona.get("ac_device_id", "")
    cfg["zone"] = [aggiornata if z is zona else z for z in cfg["zone"]]
    salva_config(cfg)
    cambiate = sorted(k for k in aggiornata if aggiornata.get(k) != zona.get(k))
    if nuovo_room != room_id or nuovo_ac != vecchio_ac:
        # Il vecchio termostato (o il vecchio AC) non e' piu' di questa stanza: si restituisce
        get_servizio().rilascia_zona(room_id if nuovo_room != room_id else "", vecchio_ac if nuovo_ac != vecchio_ac else "",
                                     any(z.get("ac_device_id") == vecchio_ac for z in cfg["zone"]))
    if cambiate:
        _registra_comando(f"Impostazioni della stanza modificate: {', '.join(cambiate)}", oggetto=aggiornata["nome"],
                          dati={k: aggiornata[k] for k in cambiate})
    get_servizio().ricalcola()
    return jsonify({"status": "ok", "room_id": nuovo_room, "zona": aggiornata})


@app.route("/api/stanze/<room_id>", methods=["DELETE"])
@login_required
def api_elimina_stanza(room_id):
    if not current_user.is_admin:
        return jsonify({"errore": "Solo gli amministratori possono gestire le stanze"}), 403
    cfg = carica_config()
    zona = _zona_configurata(cfg, room_id)
    if not zona:
        return jsonify({"errore": "Stanza non trovata"}), 404
    cfg["zone"] = [z for z in cfg["zone"] if z is not zona]
    salva_config(cfg)
    acid = zona.get("ac_device_id", "")
    get_servizio().rilascia_zona(room_id, acid, any(z.get("ac_device_id") == acid for z in cfg["zone"]))
    _registra_comando("Stanza eliminata", oggetto=zona.get("nome"), dati={"room_id": room_id})
    return jsonify({"status": "ok"})


@app.route("/api/stanze/opzioni")
@login_required
def api_opzioni_stanze():
    if not current_user.is_admin:
        return jsonify({"errore": "Solo gli amministratori"}), 403
    cfg = carica_config()
    return jsonify(_opzioni_stanze(cfg, dispositivi.snapshot(cfg)))


# ─── Registro eventi ─────────────────────────────────────────────────────────

@app.route("/registro")
@login_required
def pagina_registro():
    return render_template("registro.html", pagina_registro=True)


@app.route("/api/registro")
@login_required
def api_registro():
    categorie = [c for c in request.args.get("categorie", "").split(",") if c in registro.CATEGORIE]
    try:
        prima_di = float(request.args["prima_di"]) if request.args.get("prima_di") else None
        limite = int(request.args.get("limite", 100))
    except ValueError:
        return jsonify({"errore": "prima_di e limite devono essere numeri"}), 400
    righe = registro.leggi(categorie=categorie or None, livello_min=request.args.get("livello", "info"),
                           oggetto=[o for o in request.args.getlist("oggetto") if o] or None,
                           testo=request.args.get("q") or None,
                           prima_di=prima_di, limite=limite)
    if not current_user.is_admin:
        for r in righe:     # i dettagli tecnici (corpi dei webhook, ID) solo agli admin
            r["dati"] = None
    return jsonify({"righe": righe, "oggetti": registro.oggetti(), "categorie": registro.CATEGORIE,
                    "livelli": registro.LIVELLI})


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
        try:
            plant_id = imposta_plant_id_se_unico(bt)
            if plant_id:
                flash("Impianto Netatmo selezionato automaticamente.", "success")
        except Exception as e:
            logger.warning("Plant ID Netatmo non impostato automaticamente: %s", e)
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
    return render_template("admin/settings.html", cfg=cfg,
                           potenza_misurata=storico.potenza_media_ac(),
                           ventole_ac=VENTOLE_AC, modalita_notte_ac=MODALITA_NOTTE_AC)


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


# ─── Avvio servizi in background (compatibile gunicorn --preload) ────────────

def raccomandazione_ora_corrente(cfg: dict) -> Optional[dict]:
    """Riga dell'ora corrente del motore delle raccomandazioni (con la
    temperatura esterna misurata e il pannello), piu' i prezzi: la usano
    storico e automazione. None se i dati non sono disponibili."""
    try:
        prezzi = calcola_prezzi(cfg)
        raccomandazioni = raccomandazioni_con_temp(cfg, prezzi, temperatura_esterna_attuale(cfg))
    except Exception as e:
        logger.warning("Raccomandazione dell'ora corrente non disponibile: %s", e)
        return None
    ora_str = datetime.now().strftime("%Y-%m-%dT%H:00")
    attuale = next((r for r in raccomandazioni if r["ora"] == ora_str), None)
    if attuale is None:
        return None
    return {**attuale, "prezzi": prezzi}


def _letture_dispositivi() -> list:
    """Letture per lo storico (ogni 15 min): niente se non ci sono dispositivi."""
    cfg = carica_config()
    righe = dispositivi.letture_per_storico(dispositivi.snapshot(cfg))
    cfr = misure_temp_esterna(cfg)["cfr"]
    if cfr:
        # Anche la CFR, per confrontarla con il modulo esterno Netatmo
        righe.append(dispositivi.riga_meteo(f"cfr:{cfr['id']}", cfr["nome"], cfr["temp"],
                                            extra={"ora_misura": cfr["ts"].isoformat(timespec="minutes")}))
    return righe


def _campione_corrente() -> Optional[dict]:
    """Produce il campione dell'ora corrente per lo storico (o None se i dati
    non sono disponibili — il campionatore ritentera' al prossimo giro)."""
    attuale = raccomandazione_ora_corrente(carica_config())
    if attuale is None:
        return None
    prezzi = attuale["prezzi"]
    return {
        "temp_esterna": attuale["temp_esterna"],
        "fonte_temp": attuale["fonte_temp"],
        "cop": attuale["cop"],
        "costo_gas_kwh": attuale["costo_gas_kwh"],
        "costo_ac_kwh": attuale["costo_ac_kwh"],
        "gas_totale_smc": prezzi["gas_totale_smc"],
        "luce_totale_kwh": prezzi["luce_totale_kwh"],
        "raccomandazione": attuale["raccomandazione"],
    }


_servizi_avviati = False


def _avvia_servizi():
    global _servizi_avviati
    if not _servizi_avviati:
        if not os.path.exists(CONFIG_FILE):
            salva_config(DEFAULT_CONFIG)
        # I test importano l'app senza thread in background ne' chiamate di rete
        get_servizio().imposta_fornitore(raccomandazione_ora_corrente)
        # Tabelle dello storico (anche le nuove su un DB esistente): Home e
        # Dispositivi le leggono anche senza campionatore
        storico.inizializza_db()
        if os.environ.get("TERMOPILOTA_SENZA_SERVIZI") != "1":
            registro.collega_logging()
            registro.scrivi("sistema", f"Avvio di TermoPilota {VERSIONE}")
            avvia_se_attiva()
            storico.avvia_campionatore(_campione_corrente, _letture_dispositivi)
            osservatore.avvia(carica_config, _ricalcolo_per)
        _servizi_avviati = True


_avvia_servizi()

# Registra Google OAuth se le credenziali sono configurate. Cambiarle richiede
# un riavvio dell'app.
_cfg_iniziale = carica_config()
setup_google_oauth(
    app,
    _cfg_iniziale.get("google_client_id", ""),
    _cfg_iniziale.get("google_client_secret", ""),
)
