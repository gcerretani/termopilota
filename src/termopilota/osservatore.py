# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Polling rapido di Netatmo: per i termostati Smarther "with Netatmo" il webhook
non manda gli eventi delle stanze, quindi ogni `netatmo_polling_secondi`
(default 120) si rilegge lo stato e lo si confronta con la lettura precedente.

Ogni cambio (setpoint, modalita', fine del manuale, raggiungibilita',
finestra, modalita' della casa, programma) finisce nel registro come
'evento', marcato "da TermoPilota" se segue un nostro comando o "esterno"
(app o termostato), e passa da live.notifica: le pagine si aggiornano e, per
un cambio esterno su una zona automatizzata, il ciclo dell'automazione riparte.
Il webhook Netatmo resta attivo in parallelo.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from termopilota import dispositivi, live, registro

logger = logging.getLogger(__name__)

INTERVALLO_DEFAULT_S = 120
INTERVALLO_MIN_S, INTERVALLO_MAX_S = 60, 900
VIVO_OGNI_S = 3600          # una riga di debug all'ora: il polling sta girando

CAMPI_STANZA = ("setpoint", "modalita", "setpoint_fine", "raggiungibile", "finestra_aperta")
CAMPI_CASA = ("therm_mode", "programma_attivo", "temperature_control_mode")

MODI = {"home": "programma", "manual": "manuale", "max": "boost", "off": "spento", "hg": "antigelo",
        "away": "assente", "schedule": "programma", "heating": "riscaldamento", "cooling": "raffrescamento"}


def intervallo(cfg: dict) -> int:
    try:
        valore = int(float(cfg.get("netatmo_polling_secondi") or INTERVALLO_DEFAULT_S))
    except (TypeError, ValueError):
        valore = INTERVALLO_DEFAULT_S
    return max(INTERVALLO_MIN_S, min(INTERVALLO_MAX_S, valore))


def differenze(prima: Optional[dict], dopo: dict) -> list:
    """Cambi tra due letture {casa, stanze}: [{tipo, id, nome, campo, prima, dopo}].
    La temperatura misurata e la richiesta di calore non contano (vanno nello storico)."""
    if not prima:
        return []
    cambi = []
    casa_p, casa_d = prima.get("casa") or {}, dopo.get("casa") or {}
    if casa_p and casa_d:
        for campo in CAMPI_CASA:
            if casa_p.get(campo) != casa_d.get(campo):
                cambi.append({"tipo": "casa", "id": casa_d.get("id"), "nome": casa_d.get("name") or "Casa",
                              "campo": campo, "prima": casa_p.get(campo), "dopo": casa_d.get(campo)})
    stanze_p, stanze_d = prima.get("stanze") or {}, dopo.get("stanze") or {}
    for rid, st in stanze_d.items():
        vecchia = stanze_p.get(rid)
        if vecchia is None:
            continue
        nome = st.get("nome") or rid
        if vecchia.get("raggiungibile") != st.get("raggiungibile"):
            # Termostato perso o ritrovato da Netatmo: setpoint e modalita' diventano
            # (o tornano da) "nessun dato", non sono cambi da segnalare
            cambi.append({"tipo": "stanza", "id": rid, "nome": nome, "campo": "raggiungibile",
                          "prima": vecchia.get("raggiungibile"), "dopo": st.get("raggiungibile")})
            continue
        for campo in CAMPI_STANZA:
            if vecchia.get(campo) != st.get(campo):
                cambi.append({"tipo": "stanza", "id": rid, "nome": nome,
                              "campo": campo, "prima": vecchia.get(campo), "dopo": st.get(campo)})
    return cambi


def _valore(campo: str, v) -> str:
    if v is None:
        return "—"
    if campo == "setpoint":
        return f"{v:g} °C" if isinstance(v, (int, float)) else str(v)
    if campo == "setpoint_fine":
        return datetime.fromtimestamp(v).strftime("%H:%M") if isinstance(v, (int, float)) else str(v)
    if campo in ("raggiungibile", "finestra_aperta"):
        return "sì" if v else "no"
    return MODI.get(str(v), str(v))


ETICHETTE = {"setpoint": "termostato", "modalita": "modalità", "setpoint_fine": "fine del manuale",
             "raggiungibile": "raggiungibile", "finestra_aperta": "finestra aperta",
             "therm_mode": "modalità casa", "programma_attivo": "programma",
             "temperature_control_mode": "impianto"}


def descrivi(cambi: list) -> str:
    if len(cambi) == 1 and cambi[0]["campo"] == "raggiungibile":
        return ("termostato di nuovo raggiungibile" if cambi[0]["dopo"]
                else "termostato non raggiungibile (segnalato da Netatmo)")
    return ", ".join(f"{ETICHETTE.get(c['campo'], c['campo'])} {_valore(c['campo'], c['prima'])} → "
                     f"{_valore(c['campo'], c['dopo'])}" for c in cambi)


class OsservatoreNetatmo:
    """Thread daemon del polling Netatmo."""

    def __init__(self, carica_config: Callable[[], dict],
                 ricalcolo_per: Callable[[dict, str, list], Optional[Callable[[], None]]]):
        self._carica_config = carica_config
        self._ricalcolo_per = ricalcolo_per
        self._precedente: Optional[dict] = None
        self._letture = 0
        self._ultimo_vivo = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def avvia(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="osservatore-netatmo")
        self._thread.start()
        logger.info("Polling Netatmo avviato")

    def ferma(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            cfg = self._carica_config()
            try:
                self.controlla(cfg)
            except Exception as e:     # il thread non deve morire
                logger.exception("Errore nel polling Netatmo: %s", e)
            self._stop.wait(intervallo(cfg))

    def controlla(self, cfg: dict, adesso: Optional[float] = None) -> list:
        """Una lettura: registra e notifica i cambi. Restituisce i cambi trovati."""
        adesso = adesso or time.time()
        if not cfg.get("legrand_plant_id"):
            return []
        nuovo, errori = dispositivi.aggiorna_netatmo(cfg)
        if errori or not nuovo.get("stanze"):
            return []        # l'avviso (uno per disservizio) lo scrive dispositivi
        self._letture += 1
        cambi = differenze(self._precedente, nuovo)
        self._precedente = nuovo
        finestra = intervallo(cfg) + live.FINESTRA_COMANDO_NOSTRO_S
        per_oggetto: dict = {}
        for c in cambi:
            per_oggetto.setdefault((c["tipo"], c["id"], c["nome"]), []).append(c)
        for (tipo, ident, nome), gruppo in per_oggetto.items():
            raggiungibilita = all(c["campo"] == "raggiungibile" for c in gruppo)
            nostro = tipo == "stanza" and not raggiungibilita and live.recente_nostro(ident, adesso, finestra)
            if raggiungibilita:
                origine, messaggio = "netatmo", descrivi(gruppo)
            else:
                origine = "termopilota" if nostro else "esterna"
                messaggio = f"{descrivi(gruppo)} — " + ("da TermoPilota" if nostro else "esterno (app o termostato)")
            registro.scrivi("evento", messaggio, oggetto=nome,
                            livello="warning" if raggiungibilita and not gruppo[0]["dopo"] else "info",
                            dati={"sorgente": "polling", "origine": origine, "cambi": gruppo})
            idents = [ident] if tipo == "stanza" else []
            # Le pagine si aggiornano sempre; l'automazione riparte solo per un cambio esterno
            ricalcolo = (None if nostro or raggiungibilita or not idents
                         else self._ricalcolo_per(cfg, "stanza", idents))
            live.notifica("netatmo_polling", idents or [ident or "casa"], lambda: None, ricalcolo,
                          nostri=nostro or raggiungibilita)
        if adesso - self._ultimo_vivo >= VIVO_OGNI_S:
            self._ultimo_vivo = adesso
            registro.scrivi("evento", f"Polling Netatmo attivo: {self._letture} letture, ogni {intervallo(cfg)} s",
                            livello="debug")
        return cambi


_osservatore: Optional[OsservatoreNetatmo] = None


def avvia(carica_config: Callable[[], dict], ricalcolo_per) -> OsservatoreNetatmo:
    global _osservatore
    if _osservatore is None:
        _osservatore = OsservatoreNetatmo(carica_config, ricalcolo_per)
    _osservatore.avvia()
    return _osservatore
