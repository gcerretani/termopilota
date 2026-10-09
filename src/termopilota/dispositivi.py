# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Fotografia unica dei dispositivi (condizionatori SmartThings e stanze Netatmo)
condivisa da dashboard, pagina Dispositivi, storico e automazione, piu' i
comandi manuali validati.

La fotografia resta in cache SNAPSHOT_TTL secondi, cosi' le pagine aperte e
il campionatore non moltiplicano le chiamate alle API. Ogni comando la
invalida. Contiene sia i valori normalizzati usati da TermoPilota sia lo
stato grezzo, che la pagina Dispositivi mostra per intero.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

from termopilota import live, registro
from termopilota.providers import LimiteChiamate, get_heatpump, get_thermostat
from termopilota.providers.netatmo import (
    ErroreNetatmo, descrivi_errore_modulo, normalizza_stanza, ora_casa, programma_attivo, setpoint_programmato,
)
from termopilota.providers.smartthings import (
    CAPABILITY_ESCLUSE, CONTROLLI_AC, comandi_avanzati, controlli_disponibili, normalizza_stato,
    valida_comando, valida_comando_avanzato,
)

logger = logging.getLogger(__name__)

SNAPSHOT_TTL = 60
SETPOINT_MIN, SETPOINT_MAX = 5.0, 30.0
DURATA_MIN_MINUTI, DURATA_MAX_MINUTI = 5, 24 * 60

# Due parti con scadenze indipendenti: un evento SmartThings rilegge solo gli AC,
# uno Netatmo (o il polling) solo casa e stanze. Cosi' le chiamate a Netatmo
# restano lontane dal limite di 500 l'ora.
PARTI = ("ac", "netatmo")
_cache: dict = {}
_lock = threading.Lock()
_stato_netatmo = {"in_errore_dal": None}     # un solo avviso per disservizio


def azzera() -> None:
    """Svuota la cache (test)."""
    with _lock:
        _cache.clear()
        _cache.update(dati=None, ts={p: 0.0 for p in PARTI}, errori={p: [] for p in PARTI},
                      letti_alle={p: None for p in PARTI})
    _stato_netatmo["in_errore_dal"] = None


azzera()


def invalida(parte: Optional[str] = None) -> None:
    """Fa rileggere una parte ('ac' o 'netatmo') o tutto alla prossima richiesta."""
    with _lock:
        for p in ([parte] if parte else PARTI):
            _cache["ts"][p] = 0.0


def snapshot(cfg: dict, forza: bool = False) -> dict:
    """{ac: {id: ...}, casa: {...} | None, stanze: {room_id: ...}, errori: [...], letto_alle, letti_alle}.

    `letti_alle` = {ac, netatmo}: l'ultima lettura riuscita di ciascuna parte
    (resta quella vecchia se l'ultima e' fallita), per l'ora sulle pagine."""
    adesso = time.time()
    with _lock:
        precedente = _cache["dati"]
        da_leggere = [p for p in PARTI
                      if forza or precedente is None or adesso - _cache["ts"][p] >= SNAPSHOT_TTL]
        if not da_leggere:
            return precedente
    letto, errori = {}, {}
    if "ac" in da_leggere:
        errori["ac"] = []
        letto["ac"] = _leggi_ac(cfg, errori["ac"])
    if "netatmo" in da_leggere:
        errori["netatmo"] = []
        letto.update(_leggi_netatmo(cfg, errori["netatmo"]))
    with _lock:
        base = _cache["dati"] or {"ac": {}, "casa": None, "stanze": {}}
        _cache["errori"].update(errori)
        adesso_iso = datetime.now().isoformat(timespec="seconds")
        for p in da_leggere:
            if not errori[p]:
                _cache["letti_alle"][p] = adesso_iso
        _cache["dati"] = {**base, **letto,
                          "errori": [e for p in PARTI for e in _cache["errori"][p]],
                          "letto_alle": adesso_iso, "letti_alle": dict(_cache["letti_alle"])}
        for p in da_leggere:
            _cache["ts"][p] = time.time()
        return _cache["dati"]


def aggiorna_netatmo(cfg: dict) -> tuple:
    """Rilegge solo la parte Netatmo (polling): restituisce ({casa, stanze}, errori).
    Se riesce aggiorna la fotografia in cache, senza toccare gli AC."""
    errori: list = []
    nuovo = _leggi_netatmo(cfg, errori)
    if not errori:
        with _lock:
            if _cache["dati"] is not None:
                _cache["errori"]["netatmo"] = []
                _cache["letti_alle"]["netatmo"] = datetime.now().isoformat(timespec="seconds")
                _cache["dati"] = {**_cache["dati"], **nuovo,
                                  "errori": list(_cache["errori"]["ac"]),
                                  "letti_alle": dict(_cache["letti_alle"])}
                _cache["ts"]["netatmo"] = time.time()
    return nuovo, errori


def _netatmo_fallito(errore: Exception) -> None:
    """Avviso nel registro solo all'inizio di un disservizio (non per ogni lettore)."""
    if _stato_netatmo["in_errore_dal"] is None:
        _stato_netatmo["in_errore_dal"] = time.time()
        limite = isinstance(errore, LimiteChiamate)
        registro.scrivi("sistema", ("Netatmo: " if limite else "Netatmo non risponde: ") + str(errore),
                        livello="warning", dati={"errore": str(errore), "limite": limite})
    else:
        logger.info("Netatmo ancora non disponibile: %s", errore)


def _netatmo_riuscito() -> None:
    inizio = _stato_netatmo["in_errore_dal"]
    if inizio is not None:
        _stato_netatmo["in_errore_dal"] = None
        registro.scrivi("sistema", f"Netatmo risponde di nuovo (dopo {max(1, round((time.time() - inizio) / 60))} min)")


def _leggi_ac(cfg: dict, errori: list) -> dict:
    st = get_heatpump("smartthings", cfg)
    if not st or not st.configurato:
        return {}
    try:
        elenco = st.lista_dispositivi_ac()
    except Exception as e:
        errori.append(f"SmartThings: {e}")
        return {}
    risultato = {}
    for d in elenco:
        voce = {
            "id": d["device_id"],
            "nome": d.get("label") or "Condizionatore",
            "capability": d.get("capability", {}),
            "ocf": d.get("ocf", {}),
            "stato": {},
            "grezzo": {},
            "errore": None,
        }
        try:
            voce["grezzo"] = st.stato_completo(d["device_id"])
            voce["stato"] = normalizza_stato(voce["grezzo"])
        except Exception as e:
            voce["errore"] = str(e)
            errori.append(f"SmartThings {voce['nome']}: {e}")
        risultato[voce["id"]] = voce
    return risultato


_CHIAVI_CASA = ("therm_mode", "therm_mode_endtime", "temperature_control_mode", "cooling_mode",
                "therm_setpoint_default_duration", "timezone", "name", "id")


def _leggi_netatmo(cfg: dict, errori: list) -> dict:
    vuoto = {"casa": None, "stanze": {}}
    bt = get_thermostat("netatmo", cfg)
    home_id = cfg.get("legrand_plant_id", "")
    if not bt or not bt.autenticato or not home_id:
        return vuoto
    completo = None
    for tentativo in range(2):    # un secondo tentativo per gli errori di rete passeggeri
        try:
            completo = bt.stato_casa(home_id)
            break
        except Exception as e:
            # Il primo tentativo fallito non e' un problema: solo nel log del container
            if tentativo or isinstance(e, LimiteChiamate):
                errori.append(f"Netatmo: {e}")
                _netatmo_fallito(e)
                return vuoto
            logger.info("Lettura Netatmo fallita (tentativo 1, riprovo): %s", e)
            time.sleep(2)
    _netatmo_riuscito()
    dati = completo.get("dati") or {}
    stato = completo.get("stato") or {}
    adesso = ora_casa(dati.get("timezone"))
    programma = programma_attivo(dati)

    # Nell'homestatus la modalita' della casa puo' essere piu' aggiornata dell'homesdata
    casa = {k: dati.get(k) for k in _CHIAVI_CASA}
    for k in ("therm_mode", "therm_mode_endtime", "temperature_control_mode", "cooling_mode"):
        if stato.get(k) is not None:
            casa[k] = stato[k]
    casa["programma_attivo"] = programma.get("name") if programma else None
    casa["programmi"] = [{"id": s.get("id"), "nome": s.get("name"), "selezionato": bool(s.get("selected")),
                          "tipo": s.get("type", "therm")}
                         for s in dati.get("schedules", []) or []]
    casa["ora_locale"] = adesso.strftime("%H:%M")
    coordinate = dati.get("coordinates")    # [lon, lat] in homesdata
    casa["coordinate"] = ({"lat": coordinate[1], "lon": coordinate[0]}
                          if isinstance(coordinate, list) and len(coordinate) == 2 else None)
    casa["grezzo"] = {"dati": dati, "stato": {k: v for k, v in stato.items() if k not in ("rooms", "modules")}}

    moduli_stato = {m.get("id"): m for m in stato.get("modules", []) or []}
    stato_stanze = {r.get("id"): r for r in stato.get("rooms", []) or []}
    # Moduli in errore (es. termostato offline): homestatus non riporta ne' loro ne' la stanza
    errori_moduli = {e.get("id"): e.get("code") for e in completo.get("errori", []) or [] if e.get("id")}
    dati_per_target = {**dati, "therm_mode": casa.get("therm_mode")}
    stanze = {}
    for room in dati.get("rooms", []) or []:
        rid = room.get("id")
        moduli_dati = [m for m in dati.get("modules", []) or [] if m.get("room_id") == rid]
        if rid not in stato_stanze and not moduli_dati:
            continue    # stanze senza termostato (es. 'outdoor')
        grezzo = stato_stanze.get(rid) or {"id": rid, "reachable": False}
        voce = normalizza_stanza(grezzo)
        moduli = []
        for m in moduli_dati:
            ms = moduli_stato.get(m.get("id"), {})
            codice = errori_moduli.get(m.get("id"))
            moduli.append({
                "id": m.get("id"), "nome": m.get("name"), "tipo": m.get("type"),
                "wifi": ms.get("wifi_strength"), "firmware": ms.get("firmware_revision"),
                "caldaia_accesa": ms.get("boiler_status"), "raffrescamento_acceso": ms.get("cooler_status"),
                "raggiungibile": codice is None and ms.get("reachable", True) is not False,
                "errore": descrivi_errore_modulo(codice) if codice is not None else None,
                "grezzo": ms or ({"errore_netatmo": codice} if codice is not None else {}),
            })
        errore = next((m["errore"] for m in moduli if m["errore"]), None)
        if rid not in stato_stanze:
            errore = errore or "Netatmo non riporta lo stato di questa stanza"
        if errore:
            voce["raggiungibile"] = False
        voce.update({
            "id": rid,
            "nome": room.get("name") or rid,
            "tipo": room.get("type"),
            "target": setpoint_programmato(dati_per_target, rid, adesso),
            "caldaia_accesa": any(m["caldaia_accesa"] for m in moduli),
            "moduli": moduli,
            "errore": errore,
            "grezzo": grezzo,
        })
        stanze[rid] = voce
    return {"casa": casa, "stanze": stanze}


# ── Comandi ──────────────────────────────────────────────────────────────────

class ErroreComando(Exception):
    """Comando rifiutato (valore non ammesso) o fallito: il messaggio va all'utente."""

    def __init__(self, messaggio: str, codice: int = 400):
        super().__init__(messaggio)
        self.codice = codice


def _client_ac(cfg: dict):
    st = get_heatpump("smartthings", cfg)
    if not st or not st.configurato:
        raise ErroreComando("SmartThings non configurato", 409)
    return st


def _client_netatmo(cfg: dict):
    bt = get_thermostat("netatmo", cfg)
    home_id = cfg.get("legrand_plant_id", "")
    if not bt or not bt.autenticato or not home_id:
        raise ErroreComando("Netatmo non configurato", 409)
    return bt, home_id


def definizioni_ac(st, ac: dict) -> dict:
    """{capability: definizione} per le capability dei controlli presenti sul dispositivo."""
    versioni = ac.get("capability", {})
    capability = {c["capability"] for c in CONTROLLI_AC if c["capability"] in ac.get("grezzo", {})}
    return {cap: st.definizione_capability(cap, versioni.get(cap, 1)) for cap in capability}


def controlli_ac(cfg: dict, device_id: str, is_admin: bool) -> list:
    ac = snapshot(cfg)["ac"].get(device_id)
    if not ac or not ac.get("grezzo"):
        return []
    st = get_heatpump("smartthings", cfg)
    if not st:
        return []
    return controlli_disponibili(ac["grezzo"], definizioni_ac(st, ac), is_admin)


def comandi_avanzati_ac(cfg: dict, device_id: str) -> list:
    """Comandi della console admin: tutte le capability del dispositivo (definizioni
    lette una volta per processo)."""
    ac = snapshot(cfg)["ac"].get(device_id)
    st = get_heatpump("smartthings", cfg)
    if not ac or not ac.get("grezzo") or not st:
        return []
    versioni = ac.get("capability", {})
    definizioni = {cap: st.definizione_capability(cap, versioni.get(cap, 1))
                   for cap in ac["grezzo"] if cap not in CAPABILITY_ESCLUSE}
    return comandi_avanzati(ac["grezzo"], definizioni)


def comando_avanzato_ac(cfg: dict, device_id: str, capability: str, comando: str, argomenti) -> dict:
    """Esegue un comando della console admin, validato sullo schema della definizione."""
    st = _client_ac(cfg)
    if device_id not in snapshot(cfg)["ac"]:
        raise ErroreComando("Condizionatore non trovato", 404)
    try:
        argomenti = valida_comando_avanzato(comandi_avanzati_ac(cfg, device_id), capability, comando, argomenti)
    except ValueError as e:
        raise ErroreComando(str(e))
    live.comando_nostro(device_id)
    ok = st.esegui_comando(device_id, capability, comando, argomenti)
    invalida("ac")
    if not ok:
        raise ErroreComando("Il condizionatore ha rifiutato il comando", 502)
    return {"capability": capability, "comando": comando, "argomenti": argomenti}


def comando_ac(cfg: dict, device_id: str, chiave: str, valore, is_admin: bool) -> dict:
    """Esegue un controllo della whitelist; restituisce {capability, comando, argomenti}."""
    st = _client_ac(cfg)
    if device_id not in snapshot(cfg)["ac"]:
        raise ErroreComando("Condizionatore non trovato", 404)
    controlli = controlli_ac(cfg, device_id, is_admin)
    if not is_admin and any(c["chiave"] == chiave and c.get("solo_admin") for c in CONTROLLI_AC):
        raise ErroreComando("Comando riservato agli amministratori", 403)
    try:
        capability, comando, argomenti = valida_comando(controlli, chiave, valore)
    except ValueError as e:
        raise ErroreComando(str(e))
    live.comando_nostro(device_id)
    ok = st.esegui_comando(device_id, capability, comando, argomenti)
    invalida("ac")
    if not ok:
        raise ErroreComando("Il condizionatore ha rifiutato il comando", 502)
    return {"capability": capability, "comando": comando, "argomenti": argomenti}


def _comando_netatmo(invia) -> bool:
    """Esegue un comando Netatmo; il rifiuto arriva all'utente col messaggio di Netatmo."""
    try:
        return invia()
    except ErroreNetatmo as e:
        invalida("netatmo")
        raise ErroreComando(f"Netatmo: {e}", 502)


def _fine(durata_min) -> int:
    try:
        durata = int(float(durata_min))
    except (TypeError, ValueError):
        raise ErroreComando("Durata non valida")
    durata = max(DURATA_MIN_MINUTI, min(DURATA_MAX_MINUTI, durata))
    return int(time.time()) + durata * 60


def setpoint_stanza(cfg: dict, room_id: str, temp, durata_min) -> int:
    """Stanza in manuale a `temp` per `durata_min`; restituisce la fine (epoch)."""
    bt, home_id = _client_netatmo(cfg)
    try:
        temp = round(float(temp) * 2) / 2
    except (TypeError, ValueError):
        raise ErroreComando("Temperatura non valida")
    if not SETPOINT_MIN <= temp <= SETPOINT_MAX:
        raise ErroreComando(f"Temperatura fuori intervallo ({SETPOINT_MIN:g}–{SETPOINT_MAX:g} °C)")
    fine = _fine(durata_min)
    live.comando_nostro(room_id)
    ok = _comando_netatmo(lambda: bt.imposta_modalita(home_id, room_id, "manual", setpoint=temp, fine=fine))
    invalida("netatmo")
    if not ok:
        raise ErroreComando("Netatmo ha rifiutato il comando", 502)
    return fine


def boost_stanza(cfg: dict, room_id: str, durata_min) -> int:
    """Stanza al massimo (boost, `max`) per `durata_min`; restituisce la fine (epoch)."""
    bt, home_id = _client_netatmo(cfg)
    fine = _fine(durata_min)
    live.comando_nostro(room_id)
    ok = _comando_netatmo(lambda: bt.imposta_modalita(home_id, room_id, "max", fine=fine))
    invalida("netatmo")
    if not ok:
        raise ErroreComando("Netatmo ha rifiutato il comando", 502)
    return fine


def ripristina_stanza(cfg: dict, room_id: str) -> None:
    bt, home_id = _client_netatmo(cfg)
    live.comando_nostro(room_id)
    ok = _comando_netatmo(lambda: bt.imposta_modalita(home_id, room_id, "home"))
    invalida("netatmo")
    if not ok:
        raise ErroreComando("Netatmo ha rifiutato il comando", 502)


def modalita_casa(cfg: dict, modo: str, durata_min=None) -> None:
    bt, home_id = _client_netatmo(cfg)
    if modo not in ("schedule", "away", "hg"):
        raise ErroreComando("Modalità non valida")
    fine = _fine(durata_min) if durata_min and modo != "schedule" else None
    ok = _comando_netatmo(lambda: bt.imposta_modalita_casa(home_id, modo, fine))
    invalida("netatmo")
    if not ok:
        raise ErroreComando("Netatmo ha rifiutato il comando", 502)


def programma_casa(cfg: dict, schedule_id: str) -> None:
    bt, home_id = _client_netatmo(cfg)
    casa = snapshot(cfg).get("casa") or {}
    if schedule_id not in {p["id"] for p in casa.get("programmi", [])}:
        raise ErroreComando("Programma non trovato", 404)
    ok = _comando_netatmo(lambda: bt.cambia_programma(home_id, schedule_id))
    invalida("netatmo")
    if not ok:
        raise ErroreComando("Netatmo ha rifiutato il comando", 502)


# ── Supporto ─────────────────────────────────────────────────────────────────

def zone_collegate(cfg: dict, tipo: str, ident: str) -> list:
    """Zone configurate che usano questa stanza o questo condizionatore."""
    campo = "ac_device_id" if tipo == "ac" else "room_id"
    return [z for z in cfg.get("zone", []) or [] if ident and z.get(campo) == ident]


def letture_per_storico(snap: dict) -> list:
    """Righe per la tabella letture_dispositivi dello storico."""
    righe = []
    for ac in snap.get("ac", {}).values():
        s = ac.get("stato") or {}
        if not s:
            continue
        righe.append({
            "tipo": "ac", "id": ac["id"], "nome": ac["nome"],
            "t_ambiente": s.get("temperatura_ambiente"), "umidita": s.get("umidita"),
            "setpoint": s.get("setpoint_riscaldamento"), "attivo": 1 if s.get("acceso") else 0,
            "modalita": s.get("modalita"), "energia_wh": s.get("energia_wh"), "potenza_w": s.get("potenza_w"),
            "extra": {k: s.get(k) for k in ("ventola", "modalita_opzionale", "filtro_uso_h", "energia_fine")},
        })
    for st in snap.get("stanze", {}).values():
        if st.get("errore") and st.get("temperatura_attuale") is None:
            continue    # termostato offline: nessuna lettura
        richiesta = st.get("richiesta_calore_pct")
        righe.append({
            "tipo": "stanza", "id": st["id"], "nome": st["nome"],
            "t_ambiente": st.get("temperatura_attuale"), "umidita": st.get("umidita"),
            "setpoint": st.get("setpoint"), "attivo": 1 if (richiesta or 0) > 0 else 0,
            "modalita": st.get("modalita"), "energia_wh": None, "potenza_w": None,
            "extra": {"richiesta_calore_pct": richiesta, "target": st.get("target"),
                      "finestra_aperta": st.get("finestra_aperta"), "caldaia_accesa": st.get("caldaia_accesa")},
        })
    return righe


def riepilogo(snap: dict) -> dict:
    """Fotografia senza i dati grezzi (per elenchi e dashboard)."""
    return {
        "ac": [{k: v for k, v in ac.items() if k not in ("grezzo", "capability", "ocf")}
               for ac in snap.get("ac", {}).values()],
        "stanze": [{k: v for k, v in s.items() if k not in ("grezzo", "_campi")}
                   | {"moduli": [{k: v for k, v in m.items() if k != "grezzo"} for m in s.get("moduli", [])]}
                   for s in snap.get("stanze", {}).values()],
        "casa": ({k: v for k, v in snap["casa"].items() if k != "grezzo"} if snap.get("casa") else None),
        "errori": snap.get("errori", []),
        "letto_alle": snap.get("letto_alle"),
        "letti_alle": snap.get("letti_alle", {}),
    }


def ac_da_id(snap: dict, device_id: str) -> Optional[dict]:
    return snap.get("ac", {}).get(device_id)
