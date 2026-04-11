"""
Servizio di automazione riscaldamento.

Ogni N minuti (default 15):
1. Legge temperatura esterna (stazione CFR configurata)
2. Calcola costo €/kWh_termico per gas vs AC
3. Per ogni zona configurata:
   - Se AC conviene più del gas (oltre la soglia delta):
       → spegne il/i termostato/i BTicino
       → accende l'AC Samsung corrispondente in modalità heat
   - Se gas conviene (o temperatura < limite AC):
       → spegne l'AC
       → rimette il termostato in AUTOMATIC
4. Salva un log degli ultimi 50 eventi
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "data", "config.json")
KWH_PER_SMC = 10.691

# COP Samsung AJ040TXJ2KG/EU — ancorato a 4.47@+7°C (EN14511 certificato)
COP_TABELLA = [
    (-15, 1.60), (-10, 1.95), (-7, 2.20), (-5, 2.40), (-2, 2.70),
    (0, 2.90), (2, 3.15), (5, 3.75), (7, 4.47), (10, 4.80),
    (15, 5.15), (20, 5.40),
]

ISTERESI = 0.5  # °C — AC si accende se T < setpoint-0.5, si spegne se T >= setpoint


def _cop(t_ext: float) -> float:
    """Interpola il COP del Samsung in funzione della temperatura esterna."""
    if t_ext <= COP_TABELLA[0][0]:
        return COP_TABELLA[0][1]
    if t_ext >= COP_TABELLA[-1][0]:
        return COP_TABELLA[-1][1]
    for i in range(len(COP_TABELLA) - 1):
        t1, c1 = COP_TABELLA[i]
        t2, c2 = COP_TABELLA[i + 1]
        if t1 <= t_ext <= t2:
            return c1 + (c2 - c1) * (t_ext - t1) / (t2 - t1)
    return COP_TABELLA[-1][1]


def _temp_cfr(station_id: str) -> Optional[float]:
    """Legge la temperatura dalla stazione CFR indicata."""
    if not station_id:
        return None
    import re
    cfr_url = (
        f"https://cfr.toscana.it/monitoraggio/dettaglio.php"
        f"?id={station_id}&type=termo&json=1"
    )
    try:
        resp = requests.get(cfr_url, timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        match = re.search(
            r'new Array\(\s*"[^"]*"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,',
            resp.text,
        )
        if match:
            return float(match.group(2).replace(",", "."))
    except Exception as e:
        logger.warning("CFR non disponibile: %s", e)
    return None


def _costo_gas(cfg: dict) -> float:
    """€/kWh termico con la caldaia."""
    from prezzi import calcola_prezzi
    try:
        prezzi = calcola_prezzi(cfg)
        gas_smc = prezzi["gas_totale_smc"]
    except Exception:
        gas_smc = cfg.get("gas_totale_smc_manuale", 1.09)
    eff = cfg.get("efficienza_caldaia", 0.99)
    return gas_smc / (KWH_PER_SMC * eff)


def _costo_ac(t_ext: float, cfg: dict) -> float:
    """€/kWh termico con l'AC (pompa di calore)."""
    from prezzi import calcola_prezzi
    try:
        prezzi = calcola_prezzi(cfg)
        luce_kwh = prezzi["luce_totale_kwh"]
    except Exception:
        luce_kwh = cfg.get("luce_totale_kwh_manuale", 0.246)
    cop = _cop(t_ext)
    return luce_kwh / cop


class AutomazioneRiscaldamento:
    """Loop di controllo automatico. Avviato come thread separato."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.log_eventi: list = []       # ultimi 50 eventi
        self.stato_zone: list = []       # stato corrente per zona
        self._lock = threading.Lock()

    # ── Ciclo principale ──────────────────────────────────────────────────────

    def avvia(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="automazione")
        self._thread.start()
        logger.info("Automazione avviata")

    def ferma(self) -> None:
        self._stop_event.set()
        logger.info("Automazione fermata")

    @property
    def attiva(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._ciclo()
            except Exception as e:
                logger.exception("Errore nel ciclo automazione: %s", e)
                self._log_evento("sistema", "errore", str(e))
            cfg = self._carica_config()
            intervallo = cfg.get("intervallo_controllo_minuti", 15) * 60
            self._stop_event.wait(timeout=intervallo)

    def _ciclo(self) -> None:
        cfg = self._carica_config()
        if not cfg.get("automazione_attiva", False):
            return

        zone = cfg.get("zone", [])
        if not zone:
            return

        # ── Lettura temperatura esterna ───────────────────────────────────────
        station_id = cfg.get("cfr_station_id", "")
        t_ext = _temp_cfr(station_id)
        if t_ext is None:
            self._log_evento("sistema", "warning", "Temperatura CFR non disponibile, ciclo saltato")
            return

        costo_gas = _costo_gas(cfg)
        costo_ac  = _costo_ac(t_ext, cfg)
        soglia    = cfg.get("soglia_delta_risparmio", 0.01)
        t_min_ac  = cfg.get("temperatura_minima_ac", -15)
        home_id   = cfg.get("legrand_plant_id", "")

        from providers import get_thermostat, get_heatpump

        bticino = get_thermostat("netatmo", cfg)
        samsung = get_heatpump("smartthings", cfg)

        # ── Lettura stati Netatmo in una sola chiamata ────────────────────────
        stati_netatmo = {}
        if bticino and home_id:
            try:
                stati_netatmo = bticino.stato_tutte_stanze(home_id)
            except Exception as e:
                self._log_evento("sistema", "errore", f"Netatmo non raggiungibile: {e}")
                return

        # ── Determina per ogni AC quante zone richiedono riscaldamento ────────
        # {ac_device_id: set di room_id che richiedono AC}
        richieste_ac: dict = {}
        for zona in zone:
            ac_id   = zona.get("ac_device_id", "")
            room_id = zona.get("room_id", "")
            if ac_id not in richieste_ac:
                richieste_ac[ac_id] = set()
            stato = stati_netatmo.get(room_id, {})
            t_stanza = stato.get("temperatura_attuale")
            setpoint = stato.get("setpoint")
            conviene_ac = (t_ext >= t_min_ac and (costo_gas - costo_ac) > soglia)
            if (t_stanza is not None and setpoint is not None
                    and t_stanza < (setpoint - ISTERESI)
                    and conviene_ac):
                richieste_ac[ac_id].add(room_id)

        # ── Ciclo per zona ────────────────────────────────────────────────────
        nuovi_stati = []
        for zona in zone:
            nome    = zona.get("nome", "Zona")
            room_id = zona.get("room_id", "")
            ac_id   = zona.get("ac_device_id", "")

            stato = stati_netatmo.get(room_id, {})
            t_stanza = stato.get("temperatura_attuale")
            setpoint = stato.get("setpoint")
            modalita_corrente = stato.get("modalita", "")

            conviene_ac = (t_ext >= t_min_ac and (costo_gas - costo_ac) > soglia)
            questa_zona_vuole_ac = room_id in richieste_ac.get(ac_id, set())
            # L'AC va acceso se almeno una zona con quell'AC vuole riscaldamento
            ac_deve_essere_acceso = bool(richieste_ac.get(ac_id))

            stato_zona = {
                "nome":      nome,
                "fonte":     "ac" if questa_zona_vuole_ac else "gas",
                "t_stanza":  t_stanza,
                "setpoint":  setpoint,
                "t_ext":     t_ext,
                "costo_gas": round(costo_gas, 4),
                "costo_ac":  round(costo_ac, 4),
                "aggiornato": datetime.now().strftime("%H:%M"),
            }

            if questa_zona_vuole_ac:
                # Zona ha bisogno di calore e AC conviene
                # → blocca termostato Netatmo (manual a 7°C = valvola chiusa)
                if bticino and home_id and modalita_corrente != "manual":
                    try:
                        bticino.imposta_modalita(home_id, room_id, "OFF", setpoint=7.0)
                    except Exception as e:
                        logger.error("Errore blocco termostato %s: %s", room_id, e)

                # → accendi AC (con setpoint letto da Netatmo)
                if samsung and ac_id and ac_deve_essere_acceso:
                    try:
                        samsung.accendi_ac(ac_id, setpoint=setpoint or 21.0)
                    except Exception as e:
                        logger.error("Errore accensione AC %s: %s", ac_id, e)
                        stato_zona["errore_ac"] = str(e)

                self._log_evento(
                    nome, "→ AC",
                    f"T stanza {t_stanza:.1f}°C < setpoint {setpoint:.1f}°C | "
                    f"gas={costo_gas:.3f} > ac={costo_ac:.3f} €/kWh_th"
                )

            else:
                # Zona soddisfatta o gas conviene
                # → ripristina termostato Netatmo in schedule
                if bticino and home_id and modalita_corrente == "manual":
                    try:
                        bticino.imposta_modalita(home_id, room_id, "AUTOMATIC")
                    except Exception as e:
                        logger.error("Errore ripristino termostato %s: %s", room_id, e)

                # → spegni AC solo se NESSUNA zona con quell'AC ha ancora bisogno
                if samsung and ac_id and not ac_deve_essere_acceso:
                    try:
                        samsung.spegni_ac(ac_id)
                    except Exception as e:
                        logger.error("Errore spegnimento AC %s: %s", ac_id, e)
                        stato_zona["errore_ac"] = str(e)

                if t_stanza is not None and setpoint is not None:
                    if t_stanza >= setpoint:
                        motivo = f"Setpoint {setpoint:.1f}°C raggiunto (T={t_stanza:.1f}°C)"
                    elif not conviene_ac:
                        motivo = f"Gas conviene (gas={costo_gas:.3f} ≤ ac={costo_ac:.3f} €/kWh_th)"
                    else:
                        motivo = f"T={t_ext:.1f}°C sotto limite AC ({t_min_ac}°C)"
                else:
                    motivo = "Dati Netatmo non disponibili"
                self._log_evento(nome, "→ Gas", motivo)

            nuovi_stati.append(stato_zona)

        with self._lock:
            self.stato_zone = nuovi_stati

    # ── Utilità ───────────────────────────────────────────────────────────────

    def _log_evento(self, zona: str, azione: str, dettaglio: str) -> None:
        evento = {
            "ts": datetime.now().strftime("%d/%m %H:%M"),
            "zona": zona,
            "azione": azione,
            "dettaglio": dettaglio,
        }
        with self._lock:
            self.log_eventi.insert(0, evento)
            self.log_eventi = self.log_eventi[:50]
        logger.info("[%s] %s — %s", zona, azione, dettaglio)

    def stato(self) -> dict:
        with self._lock:
            return {
                "attiva": self.attiva,
                "zone": list(self.stato_zone),
                "log": list(self.log_eventi[:20]),
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
