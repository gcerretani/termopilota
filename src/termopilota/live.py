# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Aggiornamenti live: notifiche dai webhook di Netatmo e SmartThings.

Principio "notifica → rilettura": un evento non viene mai preso per buono come
dato. Invalida la fotografia dei dispositivi, incrementa un contatore di
versione (che le pagine aperte interrogano per ricaricarsi) e, se riguarda una
zona automatizzata, anticipa il ciclo dell'automazione. Cosi' un evento falso
puo' al massimo causare una rilettura.

Gli eventi che seguono di poco un comando nostro sullo stesso dispositivo non
fanno ripartire il ciclo (altrimenti ogni comando ne genererebbe un altro).
"""

import threading
import time
from typing import Callable, Optional

FINESTRA_COMANDO_NOSTRO_S = 30
DEBOUNCE_RICALCOLO_S = 60
SORGENTI = ("netatmo", "smartthings")

_lock = threading.Lock()
_stato: dict = {}
_comandi_nostri: dict = {}      # ident -> epoch dell'ultimo comando inviato da noi
_ultimo_ricalcolo = 0.0


def _vuoto() -> dict:
    return {"versione": 0, **{s: {"eventi": 0, "rifiutati": 0, "ultimo": None, "ultimo_rifiuto": None}
                              for s in SORGENTI}}


def azzera() -> None:
    """Solo per i test."""
    global _ultimo_ricalcolo
    with _lock:
        _stato.clear()
        _stato.update(_vuoto())
        _comandi_nostri.clear()
        _ultimo_ricalcolo = 0.0


azzera()


def comando_nostro(ident: str) -> None:
    """Da chiamare prima di inviare un comando a una stanza o a un AC."""
    if ident:
        with _lock:
            _comandi_nostri[ident] = time.time()


def recente_nostro(ident: str, adesso: Optional[float] = None) -> bool:
    adesso = adesso or time.time()
    with _lock:
        return adesso - _comandi_nostri.get(ident, 0) < FINESTRA_COMANDO_NOSTRO_S


def rifiutato(sorgente: str, motivo: str) -> None:
    with _lock:
        _stato[sorgente]["rifiutati"] += 1
        _stato[sorgente]["ultimo_rifiuto"] = {"ts": time.time(), "motivo": motivo}


def notifica(sorgente: str, idents: list, invalida: Callable[[], None],
             ricalcola: Optional[Callable[[], None]] = None) -> bool:
    """Registra un evento sui dispositivi `idents` (room_id o device_id).

    `ricalcola` (se indicato) viene chiamato al massimo una volta ogni
    DEBOUNCE_RICALCOLO_S e mai per eventi causati da un nostro comando recente.
    Restituisce True se il ciclo e' stato anticipato."""
    global _ultimo_ricalcolo
    adesso = time.time()
    invalida()
    nostri = all(recente_nostro(i, adesso) for i in idents) if idents else False
    with _lock:
        _stato["versione"] += 1
        _stato[sorgente]["eventi"] += 1
        _stato[sorgente]["ultimo"] = {"ts": adesso, "dispositivi": list(idents)[:10]}
        anticipa = (ricalcola is not None and not nostri
                    and adesso - _ultimo_ricalcolo >= DEBOUNCE_RICALCOLO_S)
        if anticipa:
            _ultimo_ricalcolo = adesso
    if anticipa:
        ricalcola()
    return anticipa


def stato() -> dict:
    with _lock:
        return {"versione": _stato["versione"],
                **{s: dict(_stato[s]) for s in SORGENTI}}
