# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scelta della temperatura esterna attuale tra le fonti misurate.

Fonti: il modulo esterno della stazione meteo Netatmo ('netatmo') e la stazione
CFR Toscana ('cfr'). Vale la prima, nell'ordine di priorita', con una misura
piu' recente di `max_eta_min`; se nessuna e' valida il motore usa la previsione
Open-Meteo dell'ora corrente.
"""

from datetime import datetime
from typing import Optional

FONTI = ("netatmo", "cfr")
NOMI_FONTI = {"netatmo": "Stazione Netatmo", "cfr": "Stazione CFR", "previsione": "Previsione Open-Meteo"}
MAX_ETA_DEFAULT_MIN = 30
MAX_ETA_MIN, MAX_ETA_MAX = 10, 180


def ordine(prima: str) -> list:
    """Le fonti con `prima` in testa (le altre fanno da riserva)."""
    if prima not in FONTI:
        prima = FONTI[0]
    return [prima] + [f for f in FONTI if f != prima]


def eta_minuti(misura: Optional[dict], adesso: datetime) -> Optional[float]:
    """Minuti trascorsi dalla misura (`ts` datetime naive, ora locale)."""
    if not misura or misura.get("ts") is None:
        return None
    return (adesso - misura["ts"]).total_seconds() / 60


def valida(misura: Optional[dict], max_eta_min: float, adesso: datetime) -> bool:
    """Misura presente, con temperatura e non piu' vecchia di `max_eta_min`.

    Una misura 'dal futuro' oltre 5 minuti (orologio sbagliato) non vale."""
    if not misura or misura.get("temp") is None:
        return False
    eta = eta_minuti(misura, adesso)
    return eta is not None and -5 <= eta <= max_eta_min


def scegli(misure: dict, priorita: list, max_eta_min: float, adesso: datetime) -> Optional[dict]:
    """Prima misura valida nell'ordine di `priorita`: {temp, ts, fonte, nome} o None.

    `misure` = {fonte: {temp, ts, nome?} | None}."""
    for fonte in priorita:
        misura = misure.get(fonte)
        if valida(misura, max_eta_min, adesso):
            return {"temp": misura["temp"], "ts": misura["ts"], "fonte": fonte,
                    "nome": misura.get("nome") or NOMI_FONTI.get(fonte, fonte)}
    return None
