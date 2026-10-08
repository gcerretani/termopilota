# SPDX-License-Identifier: GPL-3.0-or-later
"""
Persistenza storica su SQLite: campioni orari di prezzi, temperatura e
raccomandazione. Alimenta la pagina /storico e il contatore risparmi.

Il campionatore gira in un thread daemon indipendente dall'automazione:
ogni CONTROLLO_SECONDI verifica se l'ora corrente e' gia' stata registrata
e in caso contrario chiede un campione alla callback passata da app.py
(cosi' questo modulo non importa app.py e non ci sono import circolari).

I risparmi sono una STIMA: per ogni ora in cui la pompa di calore era
consigliata (e faceva abbastanza freddo da riscaldare) si conta
(costo_gas - costo_ac) €/kWh_th × potenza_termica_kw configurata.
"""

import logging
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("TERMOPILOTA_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_FILE = os.path.join(DATA_DIR, "storico.db")

CONTROLLO_SECONDI = 300      # ogni 5 min controlla se l'ora corrente manca
RITENZIONE_GIORNI = 730      # ~2 stagioni termiche
TEMP_MAX_RISCALDAMENTO = 16.0  # sopra questa T esterna il riscaldamento è considerato spento

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campioni (
    ora TEXT PRIMARY KEY,        -- "YYYY-MM-DDTHH:00" (ora locale)
    temp_esterna REAL,
    fonte_temp TEXT,             -- 'cfr' | 'previsione'
    cop REAL,
    costo_gas_kwh REAL,          -- €/kWh termico caldaia
    costo_ac_kwh REAL,           -- €/kWh termico pompa di calore
    gas_totale_smc REAL,
    luce_totale_kwh REAL,
    raccomandazione TEXT         -- 'gas' | 'ac'
);
"""


def _connetti() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def inizializza_db() -> None:
    with _connetti() as conn:
        conn.executescript(_SCHEMA)


def registra_campione(campione: dict) -> None:
    """Inserisce (o sovrascrive) il campione dell'ora indicata."""
    with _connetti() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO campioni
               (ora, temp_esterna, fonte_temp, cop, costo_gas_kwh, costo_ac_kwh,
                gas_totale_smc, luce_totale_kwh, raccomandazione)
               VALUES (:ora, :temp_esterna, :fonte_temp, :cop, :costo_gas_kwh,
                       :costo_ac_kwh, :gas_totale_smc, :luce_totale_kwh,
                       :raccomandazione)""",
            campione,
        )


def _ora_registrata(ora: str) -> bool:
    with _connetti() as conn:
        row = conn.execute("SELECT 1 FROM campioni WHERE ora = ?", (ora,)).fetchone()
        return row is not None


def _pulisci_vecchi() -> None:
    limite = (datetime.now() - timedelta(days=RITENZIONE_GIORNI)).strftime("%Y-%m-%dT%H:00")
    with _connetti() as conn:
        conn.execute("DELETE FROM campioni WHERE ora < ?", (limite,))


# ─── Query per API ────────────────────────────────────────────────────────────

def leggi_campioni(da: str, a: str, risoluzione: str = "oraria",
                   potenza_kw: float = 4.0) -> list[dict]:
    """Campioni nell'intervallo [da, a] (date "YYYY-MM-DD" incluse).

    risoluzione 'oraria':      un punto per campione registrato.
    risoluzione 'giornaliera': aggregati per giorno (medie, ore gas/AC,
                               risparmio_eur stimato con potenza_kw —
                               vedi calcola_risparmi).
    """
    inizio = f"{da}T00:00"
    fine = f"{a}T23:59"
    with _connetti() as conn:
        if risoluzione == "giornaliera":
            righe = conn.execute(
                """SELECT substr(ora, 1, 10) AS giorno,
                          ROUND(AVG(temp_esterna), 1) AS temp_media,
                          ROUND(AVG(costo_gas_kwh), 4) AS costo_gas_medio,
                          ROUND(AVG(costo_ac_kwh), 4) AS costo_ac_medio,
                          SUM(CASE WHEN raccomandazione = 'gas' THEN 1 ELSE 0 END) AS ore_gas,
                          SUM(CASE WHEN raccomandazione = 'ac' THEN 1 ELSE 0 END) AS ore_ac,
                          SUM(CASE WHEN raccomandazione = 'ac'
                                        AND temp_esterna < :t_max
                                        AND costo_gas_kwh > costo_ac_kwh
                                   THEN costo_gas_kwh - costo_ac_kwh ELSE 0 END)
                              AS risparmio_kwh
                   FROM campioni
                   WHERE ora BETWEEN :inizio AND :fine
                   GROUP BY giorno ORDER BY giorno""",
                {"inizio": inizio, "fine": fine, "t_max": TEMP_MAX_RISCALDAMENTO},
            ).fetchall()
            punti = []
            for r in righe:
                punto = dict(r)
                punto["risparmio_eur"] = round(punto.pop("risparmio_kwh") * potenza_kw, 2)
                punti.append(punto)
            return punti
        else:
            righe = conn.execute(
                """SELECT ora, temp_esterna, cop, costo_gas_kwh, costo_ac_kwh,
                          raccomandazione
                   FROM campioni
                   WHERE ora BETWEEN ? AND ? ORDER BY ora""",
                (inizio, fine),
            ).fetchall()
        return [dict(r) for r in righe]


def _inizio_stagione(oggi: date) -> date:
    """La stagione termica va dal 1° ottobre al 30 settembre successivo."""
    if oggi.month >= 10:
        return date(oggi.year, 10, 1)
    return date(oggi.year - 1, 10, 1)


def calcola_risparmi(potenza_kw: float) -> dict:
    """Stima dei risparmi in € ottenuti seguendo i consigli (oggi/7gg/stagione).

    Conta solo le ore con riscaldamento plausibilmente acceso
    (temp < TEMP_MAX_RISCALDAMENTO) in cui la pompa di calore era consigliata.
    """
    oggi = date.today()
    inizio_stagione = _inizio_stagione(oggi)

    def _somma(conn, da: date) -> tuple[float, int]:
        row = conn.execute(
            """SELECT COALESCE(SUM(costo_gas_kwh - costo_ac_kwh), 0) AS delta,
                      COUNT(*) AS ore
               FROM campioni
               WHERE ora >= ? AND raccomandazione = 'ac'
                     AND temp_esterna < ? AND costo_gas_kwh > costo_ac_kwh""",
            (f"{da.isoformat()}T00:00", TEMP_MAX_RISCALDAMENTO),
        ).fetchone()
        return row["delta"] * potenza_kw, row["ore"]

    with _connetti() as conn:
        eur_oggi, _ = _somma(conn, oggi)
        eur_settimana, _ = _somma(conn, oggi - timedelta(days=6))
        eur_stagione, ore_ac = _somma(conn, inizio_stagione)

    return {
        "oggi_eur": round(eur_oggi, 2),
        "settimana_eur": round(eur_settimana, 2),
        "stagione_eur": round(eur_stagione, 2),
        "ore_ac_stagione": ore_ac,
        "potenza_kw": potenza_kw,
        "inizio_stagione": inizio_stagione.isoformat(),
    }


# ─── Campionatore ─────────────────────────────────────────────────────────────

class CampionatoreStorico:
    """Thread daemon che registra un campione per ogni ora."""

    def __init__(self, produci_campione: Callable[[], Optional[dict]]):
        self._produci_campione = produci_campione
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def avvia(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        inizializza_db()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="storico")
        self._thread.start()
        logger.info("Campionatore storico avviato")

    def ferma(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        ultima_pulizia = 0.0
        while not self._stop_event.is_set():
            try:
                self._campiona()
                import time
                if time.time() - ultima_pulizia > 86400:
                    _pulisci_vecchi()
                    ultima_pulizia = time.time()
            except Exception as e:
                logger.warning("Errore campionatore storico: %s", e)
            self._stop_event.wait(timeout=CONTROLLO_SECONDI)

    def _campiona(self) -> None:
        ora = datetime.now().strftime("%Y-%m-%dT%H:00")
        if _ora_registrata(ora):
            return
        campione = self._produci_campione()
        if campione is None:
            return
        campione["ora"] = ora
        registra_campione(campione)
        logger.info("Campione storico registrato per %s", ora)


_campionatore: Optional[CampionatoreStorico] = None


def avvia_campionatore(produci_campione: Callable[[], Optional[dict]]) -> None:
    """Avvia il campionatore globale (idempotente). Chiamato da app.py all'avvio."""
    global _campionatore
    if _campionatore is None:
        _campionatore = CampionatoreStorico(produci_campione)
    _campionatore.avvia()
