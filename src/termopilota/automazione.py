# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Servizio di automazione riscaldamento.

Ogni N minuti (default 15):
1. Prende i costi dell'ora corrente dallo stesso motore delle raccomandazioni
   (pannello solare compreso) e la fotografia dei dispositivi.
2. Pianifica ogni zona con `pianifica` (funzione pura, testabile):
   - zona esclusa, in pausa, in manuale dall'utente, casa in raffrescamento:
     non si tocca nulla;
   - finestra aperta o dati mancanti: niente AC, termostato restituito al programma;
   - AC conveniente e stanza sotto il target (il setpoint del programma
     Netatmo, non quello corrente, che durante un nostro override vale 7 °C):
       esclusiva  → termostato in manuale a 7 °C (valvola chiusa) + AC acceso;
       affiancata → termostato in manuale a target - riserva_gas_delta (la
                    caldaia interviene solo se l'AC non ce la fa) + AC acceso;
   - altrimenti gas: il termostato torna al programma, l'AC si spegne se
     l'avevamo acceso noi e nessun'altra zona lo vuole.
3. Invia solo i comandi che cambiano qualcosa. I manuali Netatmo hanno una
   scadenza breve, rinnovata a ogni ciclo: se TermoPilota si ferma, i
   termostati tornano da soli al programma.

Lo stato di runtime (override nostri, pause, AC accesi da noi) e' in
data/automazione_stato.json. In simulazione il ciclo pianifica e registra le
decisioni senza inviare comandi.
"""

import copy
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from termopilota import dispositivi, live, registro
from termopilota.percorsi import CONFIG_FILE, STATO_AUTOMAZIONE_FILE
from termopilota.providers.netatmo import ora_casa
from termopilota.providers import scrivi_json_atomico

logger = logging.getLogger(__name__)


ISTERESI = 0.5            # °C: si entra in AC sotto target-0,5, si esce sopra target+0,5
SETPOINT_ESCLUSIVA = 7.0  # termostato in manuale a 7 °C = valvola chiusa
DURATA_OVERRIDE_MIN_S = 3600
STATI_BLOCCATI = ("pausa", "manuale", "raffrescamento")


def _motivo_dati_mancanti(bticino, home_id: str, room_id: str, stati: dict) -> str:
    """Perche' mancano temperatura/setpoint di una stanza (per il log eventi)."""
    if not bticino:
        return "Netatmo non configurato (client ID/secret mancanti)"
    if not bticino.autenticato:
        return "Netatmo non collegato: rifare il collegamento da Credenziali"
    if not home_id:
        return "ID impianto Netatmo (Plant ID) non configurato"
    if not stati:
        return "Netatmo non ha restituito nessuna stanza per questo impianto"
    stanza = stati.get(room_id)
    if stanza is None:
        return f"Stanza {room_id or '(vuota)'} non presente nella risposta Netatmo (stanze: {', '.join(stati)})"
    mancanti = [nome for nome, chiave in (("temperatura", "temperatura_attuale"), ("setpoint", "setpoint"))
                if stanza.get(chiave) is None]
    campi = ", ".join(stanza.get("_campi", [])) or "nessuno"
    return f"Netatmo non fornisce {' e '.join(mancanti)} (campi ricevuti: {campi})"


def stato_vuoto() -> dict:
    return {"stanze": {}, "ac": {}}


def in_fascia_notte(ora: int, inizio: int, fine: int) -> bool:
    if inizio == fine:
        return False
    if inizio > fine:
        return ora >= inizio or ora < fine
    return inizio <= ora < fine


def _num(valore, predefinito: float) -> float:
    try:
        return float(valore)
    except (TypeError, ValueError):
        return predefinito


def _fmt(t) -> str:
    return "?" if t is None else f"{t:.1f}"


# ─── Pianificazione (pura) ───────────────────────────────────────────────────

def pianifica(zone: list, contesto: dict, stato: dict, simulazione: bool = False) -> dict:
    """Decide cosa fare per ogni zona.

    contesto: adesso (epoch), ora (datetime locale), t_ext, costo_gas, costo_ac
      (€/kWh termico), soglia, t_min_ac, intervallo_min, stanze {room_id: stanza
      normalizzata con 'target'}, ac {device_id: stato normalizzato}, casa (dict
      o None), diagnosi {room_id: motivo dati mancanti}, ac_ventola,
      ac_modalita_notte, notte_inizio, notte_fine, pausa_manuale_ore.
    stato: stato persistito (non viene modificato: si restituisce la copia nuova).
    simulazione: non confronta lo stato con i dispositivi reali (i comandi
      simulati non sono mai stati inviati).

    Restituisce {zone: [stato per la UI], netatmo: [azioni], ac: [azioni],
    stato: stato nuovo, eventi: [(zona, azione, dettaglio)]}.
    """
    stato = copy.deepcopy(stato) if stato else stato_vuoto()
    stato.setdefault("stanze", {})
    stato.setdefault("ac", {})
    adesso = contesto["adesso"]
    stanze = contesto.get("stanze") or {}
    reali_ac = contesto.get("ac") or {}
    casa = contesto.get("casa") or {}
    t_ext = contesto["t_ext"]
    costo_gas, costo_ac = contesto["costo_gas"], contesto["costo_ac"]
    conviene = (t_ext is not None and t_ext >= contesto["t_min_ac"]
                and (costo_gas - costo_ac) > contesto["soglia"])
    durata = max(3 * contesto["intervallo_min"] * 60, DURATA_OVERRIDE_MIN_S)
    pausa_s = contesto.get("pausa_manuale_ore", 3) * 3600

    azioni_netatmo, eventi, zone_ui = [], [], []
    richieste_ac: dict = {}     # device_id -> [setpoint richiesti]
    ac_bloccati: set = set()    # AC di zone in pausa/manuale: non si toccano
    ac_zone: dict = {}          # device_id -> [room_id] (per le pause automatiche)

    # Un AC acceso da noi e poi spento o cambiato a mano: l'utente ha deciso,
    # le sue zone vanno in pausa (altrimenti il ciclo annullerebbe la modifica)
    for zona in zone:
        if zona.get("ac_device_id"):
            ac_zone.setdefault(zona["ac_device_id"], []).append(zona.get("room_id", ""))
    if not simulazione:
        for acid, stanze_ac in ac_zone.items():
            sa, reale = stato["ac"].get(acid) or {}, reali_ac.get(acid)
            motivo = _ac_modificato_da_utente(sa, reale)
            if motivo:
                sa["acceso_da_noi"] = False
                stato["ac"][acid] = sa
                for rid in stanze_ac:
                    stato["stanze"].setdefault(rid, {})["pausa_fino"] = adesso + pausa_s
                eventi.append((acid, "pausa", f"{motivo}: automazione in pausa sulle sue zone"))

    for zona in zone:
        nome = zona.get("nome", "Zona")
        rid = zona.get("room_id", "")
        acid = zona.get("ac_device_id", "")
        modalita = "affiancata" if zona.get("modalita") == "affiancata" else "esclusiva"
        sz = stato["stanze"].setdefault(rid, {})
        st = stanze.get(rid)
        ui = {
            "nome": nome, "room_id": rid, "ac_device_id": acid, "modalita": modalita,
            "automazione": zona.get("automazione", True) is not False,
            "t_stanza": st.get("temperatura_attuale") if st else None,
            "setpoint": st.get("setpoint") if st else None,
            "t_ext": t_ext, "costo_gas": round(costo_gas, 4), "costo_ac": round(costo_ac, 4),
            "pausa_fino": None, "target": None,
        }

        # L'override registrato vale solo se la stanza e' ancora come l'abbiamo lasciata
        override = sz.get("override")
        if override and not simulazione:
            if (not st or override.get("fine", 0) <= adesso or st.get("modalita") != "manual"
                    or abs(_num(st.get("setpoint"), -99) - override["setpoint"]) > 0.05):
                override = sz["override"] = None
        nostro = bool(override)

        def rilascia():
            if sz.get("override"):
                azioni_netatmo.append({"room_id": rid, "zona": nome, "tipo": "home"})
                sz["override"] = None

        if st and st.get("target") is not None:
            sz["ultimo_target"] = st["target"]
        elif st and not nostro and st.get("modalita") != "manual" and st.get("setpoint") is not None:
            sz["ultimo_target"] = st["setpoint"]
        target = sz.get("ultimo_target")
        ui["target"] = target

        pausa_fino = sz.get("pausa_fino")
        if pausa_fino and pausa_fino <= adesso:
            pausa_fino = sz["pausa_fino"] = None
        manuale_utente = (st is not None and not nostro and st.get("modalita") in ("manual", "max"))

        categoria = None
        if not ui["automazione"]:
            esito, fonte, motivo = "esclusa", None, "Zona esclusa dall'automazione"
            rilascia()
        elif pausa_fino:
            esito, fonte = "pausa", None
            motivo = "In pausa fino alle " + datetime.fromtimestamp(pausa_fino).strftime("%H:%M")
            ui["pausa_fino"] = pausa_fino
        elif casa.get("temperature_control_mode") == "cooling":
            esito, fonte, motivo = "raffrescamento", None, "Impianto in raffrescamento: automazione inattiva"
            rilascia()
        elif manuale_utente:
            esito, fonte = "manuale", None
            fine = st.get("setpoint_fine")
            motivo = "Termostato in manuale" + (
                f" fino alle {datetime.fromtimestamp(fine).strftime('%H:%M')}" if fine else "")
        elif st and not st.get("raggiungibile", True):
            esito, fonte = "errore", "gas"
            motivo = st.get("errore") or "Termostato non raggiungibile"
            categoria = "irraggiungibile"
            rilascia()
        elif not rid or not st or st.get("temperatura_attuale") is None or target is None:
            esito, fonte, categoria = "errore", "gas", "dati"
            motivo = "Dati Netatmo non disponibili: " + (
                (contesto.get("diagnosi") or {}).get(rid) or "stanza non configurata o setpoint non noto")
            rilascia()
        elif st.get("finestra_aperta"):
            esito, fonte, motivo = "finestra", "gas", "Finestra aperta: niente AC"
            rilascia()
        else:
            t = st["temperatura_attuale"]
            in_ac = sz.get("fonte") == "ac"
            vuole = conviene and bool(acid) and (
                t < target - ISTERESI or (in_ac and t < target + ISTERESI))
            if vuole:
                fonte = "ac"
                esito = modalita if modalita == "affiancata" else "ac"
                sp = (SETPOINT_ESCLUSIVA if modalita == "esclusiva"
                      else max(SETPOINT_ESCLUSIVA, round((target - _num(zona.get("riserva_gas_delta"), 1.5)) * 2) / 2))
                da_rinnovare = (not nostro or abs(override["setpoint"] - sp) > 0.05
                                or override.get("fine", 0) - adesso < durata / 2)
                if da_rinnovare:
                    fine = int(adesso + durata)
                    azioni_netatmo.append({"room_id": rid, "zona": nome, "tipo": "manual",
                                           "setpoint": sp, "fine": fine})
                    sz["override"] = {"setpoint": sp, "fine": fine}
                richieste_ac.setdefault(acid, []).append(target + _num(zona.get("offset_ac"), 0.0))
                motivo = (f"T stanza {_fmt(t)}°C, target {_fmt(target)}°C | "
                          f"gas={costo_gas:.3f} > ac={costo_ac:.3f} €/kWh_th"
                          + (f" | caldaia di riserva a {sp:g}°C" if modalita == "affiancata" else ""))
            else:
                esito, fonte = "gas", "gas"
                rilascia()
                if not acid:
                    categoria, motivo = "senza_ac", "Nessun condizionatore associato alla zona"
                elif t >= target:
                    categoria, motivo = "target", f"Target {_fmt(target)}°C raggiunto (T={_fmt(t)}°C)"
                elif t_ext is None or t_ext < contesto["t_min_ac"]:
                    categoria = "freddo"
                    motivo = f"T esterna {_fmt(t_ext)}°C sotto il limite AC ({contesto['t_min_ac']}°C)"
                elif not conviene:
                    categoria = "gas_conviene"
                    motivo = f"Gas conviene (gas={costo_gas:.3f} ≤ ac={costo_ac:.3f} €/kWh_th)"
                else:
                    categoria = "vicino"
                    motivo = f"T stanza {_fmt(t)}°C vicina al target {_fmt(target)}°C"

        if esito in STATI_BLOCCATI and acid:
            ac_bloccati.add(acid)
        precedente = (sz.get("stato"), sz.get("motivo_breve"))
        motivo_breve = categoria or esito
        if precedente != (esito, motivo_breve):
            eventi.append((nome, _azione_evento(esito), motivo))
        sz.update({"stato": esito, "fonte": fonte, "motivo_breve": motivo_breve})
        ui.update({"stato": esito, "fonte": fonte, "motivo": motivo})
        zone_ui.append(ui)

    azioni_ac = _pianifica_ac(ac_zone, richieste_ac, ac_bloccati, contesto, stato, simulazione)
    return {"zone": zone_ui, "netatmo": azioni_netatmo, "ac": azioni_ac, "stato": stato, "eventi": eventi}


def _azione_evento(esito: str) -> str:
    return {"ac": "→ AC", "affiancata": "→ AC + caldaia di riserva", "gas": "→ Gas",
            "errore": "→ Gas", "finestra": "→ Gas"}.get(esito, esito)


def _ac_modificato_da_utente(sa: dict, reale: Optional[dict]) -> Optional[str]:
    """Motivo per cui un AC acceso da noi risulta cambiato a mano (o None)."""
    if not sa.get("acceso_da_noi") or not reale:
        return None
    if reale.get("acceso") is False:
        return "Condizionatore spento a mano"
    if reale.get("modalita") and reale["modalita"] != "heat":
        return f"Modalità del condizionatore cambiata a mano ({reale['modalita']})"
    sp = reale.get("setpoint_riscaldamento")
    if sp is not None and sa.get("setpoint") is not None and abs(sp - sa["setpoint"]) > 0.5:
        return f"Temperatura del condizionatore cambiata a mano ({sp:g}°C)"
    return None


def _pianifica_ac(ac_zone: dict, richieste: dict, bloccati: set, contesto: dict, stato: dict,
                 simulazione: bool = False) -> list:
    azioni = []
    notte = in_fascia_notte(contesto["ora"].hour, contesto["notte_inizio"], contesto["notte_fine"])
    for acid in ac_zone:
        if acid in bloccati:
            continue
        sa = stato["ac"].setdefault(acid, {})
        reale = (contesto.get("ac") or {}).get(acid) or {}
        if richieste.get(acid):
            minimo = reale.get("setpoint_min") or 16
            massimo = reale.get("setpoint_max") or 30
            sp = int(max(minimo, min(massimo, round(max(richieste[acid])))))
            ventola = contesto.get("ac_ventola") if reale.get("ventola") is not None else None
            opzionale = None
            if reale.get("modalita_opzionale") is not None:
                desiderata = contesto.get("ac_modalita_notte") or "off"
                opzionale = desiderata if notte else "off"
            nuovo = {"acceso_da_noi": True, "setpoint": sp, "ventola": ventola, "opzionale": opzionale}
            spento = reale.get("acceso") is False and not simulazione
            if any(sa.get(k) != v for k, v in nuovo.items()) or spento:
                azioni.append({"device_id": acid, "tipo": "accendi", "setpoint": sp,
                               "ventola": ventola, "opzionale": opzionale})
            sa.update(nuovo)
        elif sa.get("acceso_da_noi"):
            azioni.append({"device_id": acid, "tipo": "spegni"})
            sa.update({"acceso_da_noi": False, "setpoint": None, "ventola": None, "opzionale": None})
    return azioni


def rilascio(stato: dict) -> dict:
    """Azioni per restituire tutto: stanze al programma, AC accesi da noi spenti."""
    stato = copy.deepcopy(stato) if stato else stato_vuoto()
    netatmo = []
    for rid, sz in stato.get("stanze", {}).items():
        if sz.get("override"):
            netatmo.append({"room_id": rid, "zona": rid, "tipo": "home"})
            sz["override"] = None
        sz["stato"] = sz["fonte"] = sz["motivo_breve"] = None
    ac = []
    for acid, sa in stato.get("ac", {}).items():
        if sa.get("acceso_da_noi"):
            ac.append({"device_id": acid, "tipo": "spegni"})
        stato["ac"][acid] = {"acceso_da_noi": False}
    return {"netatmo": netatmo, "ac": ac, "stato": stato}


# ─── Servizio ────────────────────────────────────────────────────────────────

class AutomazioneRiscaldamento:
    """Loop di controllo automatico. Avviato come thread separato."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._sveglia = threading.Event()      # ciclo subito (zona inclusa/esclusa, pausa)
        self.stato_zone: list = []       # stato corrente per zona
        self._lock = threading.Lock()
        self._lock_stato = threading.RLock()   # file di stato: ciclo e API
        self._stato_simulato: Optional[dict] = None
        # Riga dell'ora corrente del motore delle raccomandazioni (fornita da app.py,
        # che possiede meteo, CFR e prezzi): cosi' i costi coincidono con la dashboard
        self._fornitore: Optional[Callable[[dict], Optional[dict]]] = None

    def imposta_fornitore(self, fornitore: Callable[[dict], Optional[dict]]) -> None:
        self._fornitore = fornitore

    # ── Ciclo principale ──────────────────────────────────────────────────────

    def avvia(self) -> None:
        if self._thread and self._thread.is_alive():
            if not self._stop_event.is_set():
                return
            # Spenta e riaccesa subito: si attende la fine del thread vecchio
            self._thread.join(timeout=30)
        self._stop_event.clear()
        self._sveglia.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="automazione")
        self._thread.start()
        logger.info("Automazione avviata")

    def ferma(self) -> None:
        self._stop_event.set()
        self._sveglia.set()
        logger.info("Automazione fermata")

    def ricalcola(self) -> None:
        """Anticipa il prossimo ciclo (senza attendere l'intervallo)."""
        self._sveglia.set()

    @property
    def attiva(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._sveglia.clear()
            try:
                self._ciclo()
            except Exception as e:
                logger.exception("Errore nel ciclo automazione: %s", e)
                self._log_evento("sistema", "errore", str(e))
            cfg = self._carica_config()
            intervallo_min = max(1.0, min(1440.0, cfg.get("intervallo_controllo_minuti") or 15.0))
            self._sveglia.wait(timeout=intervallo_min * 60)

    def _contesto(self, cfg: dict, attuale: dict, snap: dict) -> dict:
        from termopilota.providers import get_thermostat
        stanze = snap.get("stanze", {})
        diagnosi = {}
        for zona in cfg.get("zone", []):
            rid = zona.get("room_id", "")
            st = stanze.get(rid)
            if not st or st.get("temperatura_attuale") is None:
                diagnosi[rid] = _motivo_dati_mancanti(
                    get_thermostat("netatmo", cfg), cfg.get("legrand_plant_id", ""), rid, stanze)
        return {
            "adesso": time.time(),
            "ora": ora_casa((snap.get("casa") or {}).get("timezone")),
            "t_ext": attuale.get("temp_esterna"),
            "costo_gas": attuale["costo_gas_kwh"],
            "costo_ac": attuale["costo_ac_kwh"],
            "soglia": _num(cfg.get("soglia_delta_risparmio"), 0.01),
            "t_min_ac": _num(cfg.get("temperatura_minima_ac"), -10),
            "intervallo_min": max(1.0, min(1440.0, _num(cfg.get("intervallo_controllo_minuti"), 15.0))),
            "stanze": stanze,
            "ac": {i: a.get("stato") or {} for i, a in snap.get("ac", {}).items()},
            "casa": snap.get("casa"),
            "diagnosi": diagnosi,
            "ac_ventola": cfg.get("ac_ventola") or "auto",
            "ac_modalita_notte": cfg.get("ac_modalita_notte") or "off",
            "notte_inizio": int(_num(cfg.get("notte_inizio"), 22)),
            "notte_fine": int(_num(cfg.get("notte_fine"), 7)),
            "pausa_manuale_ore": _num(cfg.get("pausa_manuale_ore"), 3),
        }

    def _ciclo(self) -> None:
        cfg = self._carica_config()
        if not cfg.get("automazione_attiva", False):
            return
        zone = cfg.get("zone", [])
        if not zone:
            return
        attuale = self._fornitore(cfg) if self._fornitore else None
        if attuale is None:
            self._log_evento("sistema", "warning", "Costi dell'ora corrente non disponibili, ciclo saltato")
            return
        snap = dispositivi.snapshot(cfg, forza=True)
        errore_netatmo = next((e for e in snap.get("errori", []) if e.startswith("Netatmo")), None)
        if errore_netatmo and not snap.get("stanze"):
            # Lettura fallita: meglio non decidere nulla (lo stato resta com'e', gli
            # override nostri scadono da soli) che mandare tutte le zone in errore
            self._log_evento("sistema", "warning", f"{errore_netatmo}: ciclo saltato, riprovo al prossimo")
            return
        contesto = self._contesto(cfg, attuale, snap)
        simulazione = bool(cfg.get("automazione_simulazione"))

        with self._lock_stato:
            if simulazione:
                if self._stato_simulato is None:
                    self._stato_simulato = self.leggi_stato()
                stato = self._stato_simulato
                # Le pause si impostano sul file anche in simulazione
                for rid, sz in self.leggi_stato().get("stanze", {}).items():
                    stato.setdefault("stanze", {}).setdefault(rid, {})["pausa_fino"] = sz.get("pausa_fino")
            else:
                self._stato_simulato = None
                stato = self.leggi_stato()
            piano = pianifica(zone, contesto, stato, simulazione)
            nuovo = piano["stato"]
            errori_ac = {} if simulazione else self._esegui(cfg, piano, nuovo)
            if simulazione:
                self._stato_simulato = nuovo
            else:
                self._salva_stato(nuovo)

        prefisso = "[simulazione] " if simulazione else ""
        nomi_ac = {a["id"]: a["nome"] for a in snap.get("ac", {}).values()}
        for zona, azione, dettaglio in piano["eventi"]:
            self._log_evento(nomi_ac.get(zona, zona), prefisso + azione, dettaglio)
        for a in piano["netatmo"]:
            desc = (f"termostato in manuale a {a['setpoint']:g}°C fino alle "
                    f"{datetime.fromtimestamp(a['fine']).strftime('%H:%M')}" if a["tipo"] == "manual"
                    else "termostato restituito al programma")
            self._log_evento(a["zona"], prefisso + "comando", desc, {"room_id": a["room_id"]})
        for a in piano["ac"]:
            desc = (f"acceso a {a['setpoint']}°C" if a["tipo"] == "accendi" else "spento")
            self._log_evento(nomi_ac.get(a["device_id"], "Condizionatore"), prefisso + "comando", desc,
                             {"device_id": a["device_id"]})
        adesso_str = datetime.now().strftime("%H:%M")
        for z in piano["zone"]:
            z["aggiornato"] = adesso_str
            z["simulazione"] = simulazione
            if z["ac_device_id"] in errori_ac:
                z["errore_ac"] = errori_ac[z["ac_device_id"]]
        with self._lock:
            self.stato_zone = piano["zone"]

    def _esegui(self, cfg: dict, piano: dict, stato: dict) -> dict:
        """Invia i comandi del piano; su errore corregge lo stato per ritentare."""
        from termopilota.providers import get_heatpump, get_thermostat
        errori_ac = {}
        if piano["netatmo"]:
            bt = get_thermostat("netatmo", cfg)
            home_id = cfg.get("legrand_plant_id", "")
            for a in piano["netatmo"]:
                sz = stato["stanze"].setdefault(a["room_id"], {})
                try:
                    if not bt or not home_id:
                        raise RuntimeError("Netatmo non configurato")
                    live.comando_nostro(a["room_id"])
                    if a["tipo"] == "manual":
                        ok = bt.imposta_modalita(home_id, a["room_id"], "manual",
                                                 setpoint=a["setpoint"], fine=a["fine"])
                    else:
                        ok = bt.imposta_modalita(home_id, a["room_id"], "home")
                    if not ok:
                        raise RuntimeError("comando rifiutato")
                except Exception as e:
                    logger.error("Comando Netatmo %s su %s fallito: %s", a["tipo"], a["room_id"], e)
                    self._log_evento(a["zona"], "errore", f"Termostato: {e}")
                    if a["tipo"] == "manual":
                        sz["override"] = None
        if piano["ac"]:
            st = get_heatpump("smartthings", cfg)
            for a in piano["ac"]:
                sa = stato["ac"].setdefault(a["device_id"], {})
                try:
                    if not st:
                        raise RuntimeError("SmartThings non configurato")
                    live.comando_nostro(a["device_id"])
                    if a["tipo"] == "accendi":
                        ok = st.accendi_ac(a["device_id"], setpoint=a["setpoint"], ventola=a["ventola"],
                                           modalita_opzionale=a["opzionale"])
                    else:
                        ok = st.spegni_ac(a["device_id"])
                    if not ok:
                        raise RuntimeError("comando rifiutato")
                except Exception as e:
                    logger.error("Comando AC %s su %s fallito: %s", a["tipo"], a["device_id"], e)
                    errori_ac[a["device_id"]] = str(e)
                    if a["tipo"] == "accendi":
                        sa.update({"acceso_da_noi": False, "setpoint": None})
                    else:
                        sa["acceso_da_noi"] = True
        if piano["netatmo"] or piano["ac"]:
            dispositivi.invalida()
        return errori_ac

    def rilascia_tutto(self) -> None:
        """Restituisce stanze e AC (automazione spenta): eseguito in un thread a parte."""
        cfg = self._carica_config()
        with self._lock_stato:
            self._stato_simulato = None
            piano = rilascio(self.leggi_stato())
            if piano["netatmo"] or piano["ac"]:
                self._esegui(cfg, piano, piano["stato"])
                self._log_evento("sistema", "rilascio",
                                 "Termostati restituiti al programma e condizionatori spenti")
            self._salva_stato(piano["stato"])
        with self._lock:
            self.stato_zone = []

    # ── Pause ─────────────────────────────────────────────────────────────────

    def imposta_pausa(self, room_ids: list, ore: float) -> Optional[float]:
        """Mette in pausa (ore > 0) o riattiva (ore = 0) le zone indicate."""
        fine = time.time() + ore * 3600 if ore > 0 else None
        with self._lock_stato:
            stato = self.leggi_stato()
            for rid in room_ids:
                stato["stanze"].setdefault(rid, {})["pausa_fino"] = fine
                if self._stato_simulato is not None:
                    self._stato_simulato.setdefault("stanze", {}).setdefault(rid, {})["pausa_fino"] = fine
            self._salva_stato(stato)
        with self._lock:
            for z in self.stato_zone:
                if z.get("room_id") in room_ids:
                    z["pausa_fino"] = fine
                    if fine:
                        z["stato"], z["fonte"] = "pausa", None
                        z["motivo"] = "In pausa fino alle " + datetime.fromtimestamp(fine).strftime("%H:%M")
                    elif z.get("stato") == "pausa":
                        z["stato"], z["fonte"], z["motivo"] = None, None, "Aggiornamento in corso…"
        self.ricalcola()
        return fine

    def zona_modificata(self, room_id: str, inclusa: bool) -> None:
        """Zona inclusa o esclusa: la UI lo mostra subito e il ciclo riparte."""
        with self._lock:
            for z in self.stato_zone:
                if z.get("room_id") == room_id:
                    z["automazione"] = inclusa
                    if inclusa:
                        z["stato"], z["fonte"], z["motivo"] = None, None, "Aggiornamento in corso…"
                    else:
                        z["stato"], z["fonte"], z["motivo"] = "esclusa", None, "Zona esclusa dall'automazione"
        self.ricalcola()

    # ── Utilità ───────────────────────────────────────────────────────────────

    @staticmethod
    def leggi_stato() -> dict:
        try:
            with open(STATO_AUTOMAZIONE_FILE, encoding="utf-8") as f:
                stato = json.load(f)
            if isinstance(stato, dict):
                stato.setdefault("stanze", {})
                stato.setdefault("ac", {})
                return stato
        except (OSError, ValueError):
            pass
        return stato_vuoto()

    @staticmethod
    def _salva_stato(stato: dict) -> None:
        os.makedirs(os.path.dirname(STATO_AUTOMAZIONE_FILE), exist_ok=True)
        scrivi_json_atomico(STATO_AUTOMAZIONE_FILE, stato)

    @staticmethod
    def _log_evento(zona: str, azione: str, dettaglio: str, dati: Optional[dict] = None) -> None:
        """Riga 'automazione' del registro; `zona` 'sistema' = nessun oggetto."""
        livello = ("errore" if "errore" in azione else "warning" if "warning" in azione else "info")
        registro.scrivi("automazione", dettaglio, livello=livello,
                        oggetto=None if zona == "sistema" else zona, dati={"azione": azione, **(dati or {})})

    @staticmethod
    def ultimi_eventi(limite: int = 20) -> list:
        """Ultime righe 'automazione' e 'comando' nel formato della pagina Automazione."""
        eventi = []
        for r in registro.leggi(["automazione", "comando"], limite=limite):
            azione = (r.get("dati") or {}).get("azione") if isinstance(r.get("dati"), dict) else None
            eventi.append({
                "ts": datetime.fromtimestamp(r["ts"]).strftime("%d/%m %H:%M"),
                "zona": r.get("oggetto") or "sistema",
                "azione": azione or r["categoria"],
                "dettaglio": r["messaggio"] + (f" ({r['utente']})" if r.get("utente") else ""),
                "livello": r["livello"],
            })
        return eventi

    def stato(self) -> dict:
        pause = {rid: sz.get("pausa_fino")
                 for rid, sz in self.leggi_stato().get("stanze", {}).items()
                 if sz.get("pausa_fino") and sz["pausa_fino"] > time.time()}
        with self._lock:
            return {
                "attiva": self.attiva,
                "zone": list(self.stato_zone),
                "log": self.ultimi_eventi(20),
                "pause": pause,
            }

    @staticmethod
    def _carica_config() -> dict:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        return {}


# Istanza globale (usata da app.py)
_servizio: Optional[AutomazioneRiscaldamento] = None


def get_servizio() -> AutomazioneRiscaldamento:
    global _servizio
    if _servizio is None:
        _servizio = AutomazioneRiscaldamento()
    return _servizio


def avvia_se_attiva() -> None:
    """Chiamato all'avvio di app.py: avvia il loop se automazione_attiva=true in config."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("automazione_attiva", False):
            get_servizio().avvia()
