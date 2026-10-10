# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Persistenza storica su SQLite: campioni orari di prezzi, temperatura e
raccomandazione. Alimenta la pagina /storico e il contatore risparmi.

Il campionatore gira in un thread daemon indipendente dall'automazione:
ogni CONTROLLO_SECONDI verifica se l'ora corrente e' gia' stata registrata
e in caso contrario chiede un campione alla callback passata da app.py
(cosi' questo modulo non importa app.py e non ci sono import circolari).

Lo stesso thread registra ogni LETTURE_SECONDI anche le letture dei
dispositivi (tabella letture_dispositivi): temperature, umidita', setpoint e
il contatore di energia cumulativo dei condizionatori.

Il risparmio conta solo il calore che i condizionatori hanno davvero prodotto
in riscaldamento: ogni kWh termico dell'AC e' un kWh che la caldaia non ha
dovuto dare, quindi vale (costo_gas - costo_ac) di quell'ora (negativo se
l'AC era acceso quando conveniva il gas). Se tutto e' spento non si risparmia
nulla, qualunque fosse il consiglio. Vale uguale in modalita' esclusiva e
affiancata: il gas bruciato non si misura, conta solo il calore dell'AC.
- MISURATO: calore = kWh elettrici dal contatore × COP di tabella;
- STIMATO (AC senza contatore): calore = potenza termica configurata × quota
  dell'ora con almeno un AC acceso in riscaldamento (dalle letture).
"""

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Callable, Optional
from termopilota.percorsi import DATA_DIR

logger = logging.getLogger(__name__)

DB_FILE = os.path.join(DATA_DIR, "storico.db")

CONTROLLO_SECONDI = 300      # ogni 5 min controlla se l'ora corrente manca
LETTURE_SECONDI = 900        # letture dei dispositivi ogni 15 min
RITENZIONE_GIORNI = 730      # ~2 stagioni termiche

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campioni (
    ora TEXT PRIMARY KEY,        -- "YYYY-MM-DDTHH:00" (ora locale)
    temp_esterna REAL,
    fonte_temp TEXT,             -- 'netatmo' | 'cfr' | 'previsione'
    cop REAL,
    costo_gas_kwh REAL,          -- €/kWh termico caldaia
    costo_ac_kwh REAL,           -- €/kWh termico pompa di calore
    gas_totale_smc REAL,
    luce_totale_kwh REAL,
    raccomandazione TEXT         -- 'gas' | 'ac'
);
CREATE TABLE IF NOT EXISTS letture_dispositivi (
    ts TEXT NOT NULL,            -- "YYYY-MM-DDTHH:MM" (ora locale)
    tipo TEXT NOT NULL,          -- 'ac' | 'stanza' | 'meteo' (id 'cfr:<stazione>' = CFR)
    id TEXT NOT NULL,
    nome TEXT,
    t_ambiente REAL,
    umidita REAL,
    setpoint REAL,
    attivo INTEGER,              -- AC acceso | stanza con richiesta di calore
    modalita TEXT,
    energia_wh REAL,             -- contatore cumulativo (solo AC)
    potenza_w REAL,
    extra TEXT,                  -- JSON con i valori secondari
    PRIMARY KEY (ts, tipo, id)
);
CREATE INDEX IF NOT EXISTS idx_letture_dispositivo ON letture_dispositivi (tipo, id, ts);
"""


@contextmanager
def _connetti():
    """Connessione con commit/rollback a fine blocco e chiusura garantita.

    `with sqlite3.connect()` da solo non chiude: il file resta aperto (su Windows
    non si puo' nemmeno cancellare) finche' il garbage collector non interviene.
    """
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        with conn:
            yield conn
    finally:
        conn.close()


def inizializza_db() -> None:
    from termopilota import registro     # import qui: registro usa _connetti di questo modulo
    with _connetti() as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(registro.SCHEMA)


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
        conn.execute("DELETE FROM letture_dispositivi WHERE ts < ?", (limite,))


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
                          SUM(CASE WHEN raccomandazione = 'ac' THEN 1 ELSE 0 END) AS ore_ac
                   FROM campioni
                   WHERE ora BETWEEN :inizio AND :fine
                   GROUP BY giorno ORDER BY giorno""",
                {"inizio": inizio, "fine": fine},
            ).fetchall()
            stime: dict = {}
            for ora, s in _stime_orarie(conn, inizio, fine, potenza_kw).items():
                stime[ora[:10]] = stime.get(ora[:10], 0.0) + s["eur"]
            misurati = consumi_misurati(da, a)
            punti = []
            for r in righe:
                punto = dict(r)
                punto["risparmio_eur"] = round(stime.get(punto["giorno"], 0.0), 2)
                m = misurati.get(punto["giorno"])
                punto["energia_ac_kwh"] = m["kwh"] if m else None
                punto["costo_ac_eur"] = round(m["costo_eur"], 2) if m else None
                punto["risparmio_reale_eur"] = round(m["risparmio_eur"], 2) if m else None
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
            stime = _stime_orarie(conn, inizio, fine, potenza_kw)
        return [{**dict(r), "risparmio_eur": round(stime.get(r["ora"], {}).get("eur", 0.0), 3)} for r in righe]


def _quote_ac_riscaldamento(conn, inizio: str, fine: str) -> dict:
    """{ora: quota 0-1 delle letture dell'ora con almeno un AC acceso in riscaldamento}."""
    righe = conn.execute(
        """SELECT substr(ts, 1, 13) || ':00' AS ora, COUNT(DISTINCT ts) AS letture,
                  COUNT(DISTINCT CASE WHEN attivo = 1 AND modalita = 'heat' THEN ts END) AS accese
           FROM letture_dispositivi
           WHERE tipo = 'ac' AND ts BETWEEN ? AND ?
           GROUP BY ora""",
        (inizio, fine),
    ).fetchall()
    return {r["ora"]: r["accese"] / r["letture"] for r in righe if r["letture"] and r["accese"]}


def _stime_orarie(conn, inizio: str, fine: str, potenza_kw: float) -> dict:
    """{ora: {eur, quota}}: risparmio STIMATO delle ore con un AC acceso in
    riscaldamento (potenza termica configurata × quota dell'ora × differenza
    di costo). Le ore con tutto spento non contano."""
    quote = _quote_ac_riscaldamento(conn, inizio, fine)
    if not quote:
        return {}
    prezzi = conn.execute(
        """SELECT ora, costo_gas_kwh, costo_ac_kwh FROM campioni
           WHERE ora BETWEEN ? AND ? AND costo_gas_kwh IS NOT NULL AND costo_ac_kwh IS NOT NULL""",
        (inizio, fine),
    ).fetchall()
    return {p["ora"]: {"eur": (p["costo_gas_kwh"] - p["costo_ac_kwh"]) * potenza_kw * quote[p["ora"]],
                       "quota": quote[p["ora"]]}
            for p in prezzi if p["ora"] in quote}


def _inizio_stagione(oggi: date) -> date:
    """La stagione termica va dal 1° ottobre al 30 settembre successivo."""
    if oggi.month >= 10:
        return date(oggi.year, 10, 1)
    return date(oggi.year - 1, 10, 1)


def calcola_risparmi(potenza_kw: float) -> dict:
    """Risparmi in € (oggi/7gg/stagione) dal calore prodotto dai condizionatori
    in riscaldamento: misurati dal contatore e stimati dalla potenza configurata.
    `*_principale_eur` e' il misurato se ci sono letture del contatore, altrimenti
    la stima: e' il numero da mostrare."""
    oggi = date.today()
    inizio_stagione = _inizio_stagione(oggi)

    with _connetti() as conn:
        stime = _stime_orarie(conn, f"{inizio_stagione.isoformat()}T00:00", f"{oggi.isoformat()}T23:59", potenza_kw)

    def _somma(da: date, campo: str = "eur") -> float:
        return sum(s[campo] for ora, s in stime.items() if ora >= da.isoformat())

    eur_oggi, eur_settimana, eur_stagione = _somma(oggi), _somma(oggi - timedelta(days=6)), _somma(inizio_stagione)
    ore_ac = _somma(inizio_stagione, "quota")

    misurati = consumi_misurati(inizio_stagione.isoformat(), oggi.isoformat())

    def _somma_misurati(da: date, campo: str) -> float:
        return round(sum(g[campo] for giorno, g in misurati.items() if giorno >= da.isoformat()), 2)

    principale = ((lambda da: _somma_misurati(da, "risparmio_eur")) if misurati
                  else (lambda da: round(_somma(da), 2)))
    return {
        "fonte": "misurato" if misurati else "stimato",
        "oggi_principale_eur": principale(oggi),
        "settimana_principale_eur": principale(oggi - timedelta(days=6)),
        "stagione_principale_eur": principale(inizio_stagione),
        "oggi_eur": round(eur_oggi, 2),
        "settimana_eur": round(eur_settimana, 2),
        "stagione_eur": round(eur_stagione, 2),
        "ore_ac_stagione": round(ore_ac, 1),
        "potenza_kw": potenza_kw,
        "inizio_stagione": inizio_stagione.isoformat(),
        # Dal contatore dei condizionatori (None finche' non ci sono letture)
        "misure_disponibili": bool(misurati),
        "oggi_reale_eur": _somma_misurati(oggi, "risparmio_eur") if misurati else None,
        "settimana_reale_eur": _somma_misurati(oggi - timedelta(days=6), "risparmio_eur") if misurati else None,
        "stagione_reale_eur": _somma_misurati(inizio_stagione, "risparmio_eur") if misurati else None,
        "kwh_ac_oggi": _somma_misurati(oggi, "kwh") if misurati else None,
        "kwh_ac_stagione": _somma_misurati(inizio_stagione, "kwh") if misurati else None,
        "costo_ac_stagione_eur": _somma_misurati(inizio_stagione, "costo_eur") if misurati else None,
    }


# ─── Letture dei dispositivi ──────────────────────────────────────────────────

def registra_letture(ts: str, righe: list) -> None:
    """Registra le letture di un istante ("YYYY-MM-DDTHH:MM")."""
    if not righe:
        return
    valori = [{
        "ts": ts, "tipo": r["tipo"], "id": r["id"], "nome": r.get("nome"),
        "t_ambiente": r.get("t_ambiente"), "umidita": r.get("umidita"), "setpoint": r.get("setpoint"),
        "attivo": r.get("attivo"), "modalita": r.get("modalita"),
        "energia_wh": r.get("energia_wh"), "potenza_w": r.get("potenza_w"),
        "extra": json.dumps(r.get("extra") or {}, ensure_ascii=False),
    } for r in righe]
    with _connetti() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO letture_dispositivi
               (ts, tipo, id, nome, t_ambiente, umidita, setpoint, attivo, modalita,
                energia_wh, potenza_w, extra)
               VALUES (:ts, :tipo, :id, :nome, :t_ambiente, :umidita, :setpoint, :attivo,
                       :modalita, :energia_wh, :potenza_w, :extra)""",
            valori,
        )


_LUNGHEZZA_PERIODO = {"oraria": 13, "giornaliera": 10}


def leggi_letture(tipo: str, ident: str, da: str, a: str, risoluzione: str = "grezza") -> list[dict]:
    """Letture di un dispositivo in [da, a] (date "YYYY-MM-DD" incluse).

    'grezza': ogni lettura; 'oraria'/'giornaliera': medie per periodo, con
    attivo_pct (quota di letture con AC acceso o richiesta di calore) e, per
    gli AC, i kWh consumati nel periodo.
    """
    inizio, fine = f"{da}T00:00", f"{a}T23:59"
    with _connetti() as conn:
        if risoluzione == "grezza":
            righe = conn.execute(
                """SELECT ts AS periodo, t_ambiente, umidita, setpoint, attivo, modalita,
                          energia_wh, potenza_w, extra
                   FROM letture_dispositivi
                   WHERE tipo = ? AND id = ? AND ts BETWEEN ? AND ? ORDER BY ts""",
                (tipo, ident, inizio, fine),
            ).fetchall()
            punti = []
            for r in righe:
                punto = dict(r)
                try:
                    punto["extra"] = json.loads(punto["extra"] or "{}")
                except ValueError:
                    punto["extra"] = {}
                punti.append(punto)
            return punti
        n = _LUNGHEZZA_PERIODO[risoluzione]
        righe = conn.execute(
            f"""SELECT substr(ts, 1, {n}) AS periodo,
                       ROUND(AVG(t_ambiente), 1) AS t_ambiente,
                       ROUND(AVG(umidita), 0) AS umidita,
                       ROUND(AVG(setpoint), 1) AS setpoint,
                       ROUND(AVG(attivo) * 100, 0) AS attivo_pct
                FROM letture_dispositivi
                WHERE tipo = ? AND id = ? AND ts BETWEEN ? AND ?
                GROUP BY periodo ORDER BY periodo""",
            (tipo, ident, inizio, fine),
        ).fetchall()
    punti = [dict(r) for r in righe]
    if tipo == "ac":
        kwh = {e["periodo"]: e["kwh"] for e in energia_ac(da, a, risoluzione, ident)}
        for p in punti:
            p["kwh"] = kwh.get(p["periodo"], 0.0)
    return punti


def _delta_energia(conn, da: str, a: str, ident: Optional[str] = None,
                   solo_riscaldamento: bool = False) -> list[tuple]:
    """[(ts, id, nome, kWh)] dalle differenze del contatore tra letture consecutive.

    Il consumo tra due letture si attribuisce all'istante della seconda; un
    contatore che cala (sostituzione, reset) non produce consumo. La lettura
    precedente a `da` (fino a un giorno prima) da' il consumo del primo periodo.
    `solo_riscaldamento`: solo i tratti con l'AC in riscaldamento in una delle
    due letture (il raffrescamento non sostituisce la caldaia).
    """
    prima = (datetime.strptime(da, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%dT00:00")
    inizio, fine = f"{da}T00:00", f"{a}T23:59"
    filtro, parametri = "", [prima, fine]
    if ident:
        filtro, parametri = " AND id = ?", [prima, fine, ident]
    righe = conn.execute(
        f"""SELECT ts, id, nome, energia_wh, attivo, modalita FROM letture_dispositivi
            WHERE tipo = 'ac' AND energia_wh IS NOT NULL AND ts BETWEEN ? AND ?{filtro}
            ORDER BY id, ts""",
        parametri,
    ).fetchall()
    delta, precedente = [], {}

    def scalda(r) -> bool:
        return bool(r["attivo"]) and r["modalita"] == "heat"

    for r in righe:
        ultimo = precedente.get(r["id"])
        if (ultimo is not None and r["ts"] >= inizio and r["energia_wh"] > ultimo["energia_wh"]
                and (not solo_riscaldamento or scalda(r) or scalda(ultimo))):
            delta.append((r["ts"], r["id"], r["nome"], (r["energia_wh"] - ultimo["energia_wh"]) / 1000.0))
        precedente[r["id"]] = r
    return delta


def energia_ac(da: str, a: str, risoluzione: str = "giornaliera",
               ident: Optional[str] = None) -> list[dict]:
    """kWh consumati da ogni condizionatore per periodo: [{periodo, id, nome, kwh}]."""
    n = _LUNGHEZZA_PERIODO.get(risoluzione, 10)
    somme: dict = {}
    with _connetti() as conn:
        for ts, i, nome, kwh in _delta_energia(conn, da, a, ident):
            chiave = (ts[:n], i)
            voce = somme.setdefault(chiave, {"periodo": ts[:n], "id": i, "nome": nome, "kwh": 0.0})
            voce["kwh"] += kwh
            voce["nome"] = nome or voce["nome"]
    return [{**v, "kwh": round(v["kwh"], 3)} for _, v in sorted(somme.items())]


def consumi_misurati(da: str, a: str) -> dict:
    """{giorno: {kwh, costo_eur, risparmio_eur}} dal contatore di tutti gli AC,
    solo in riscaldamento.

    Per ogni ora: costo = kWh × prezzo effettivo della luce (costo_ac_kwh × COP,
    cioe' con lo stesso sconto del pannello del motore); risparmio = calore
    prodotto (kWh × COP) × (costo_gas - costo_ac). Le ore senza campione di
    prezzi contano nei kWh ma non nei €.
    """
    with _connetti() as conn:
        per_ora: dict = {}
        for ts, _, _, kwh in _delta_energia(conn, da, a, solo_riscaldamento=True):
            ora = ts[:13] + ":00"
            per_ora[ora] = per_ora.get(ora, 0.0) + kwh
        if not per_ora:
            return {}
        prezzi = {r["ora"]: r for r in conn.execute(
            """SELECT ora, cop, costo_gas_kwh, costo_ac_kwh FROM campioni
               WHERE ora BETWEEN ? AND ?""",
            (f"{da}T00:00", f"{a}T23:59"),
        ).fetchall()}
    giorni: dict = {}
    for ora, kwh in per_ora.items():
        g = giorni.setdefault(ora[:10], {"kwh": 0.0, "costo_eur": 0.0, "risparmio_eur": 0.0})
        g["kwh"] += kwh
        p = prezzi.get(ora)
        if p and p["cop"] and p["costo_ac_kwh"] is not None and p["costo_gas_kwh"] is not None:
            calore = kwh * p["cop"]
            g["costo_eur"] += calore * p["costo_ac_kwh"]
            g["risparmio_eur"] += calore * (p["costo_gas_kwh"] - p["costo_ac_kwh"])
    return {giorno: {k: round(v, 3) for k, v in g.items()} for giorno, g in giorni.items()}


LETTURE_MINIME_DIFFERENZA = 8    # ~2 ore di AC acceso, prima di suggerire una correzione


def differenza_sensori(room_id: str, ac_id: str, giorni: int = 7) -> dict:
    """Quanto il sensore del condizionatore legge piu' (o meno) del termostato,
    con l'AC acceso in riscaldamento: media di (T AC - T termostato) sulle letture
    allineate a 15 minuti degli ultimi `giorni`.

    `suggerita` e' la correzione del setpoint dell'AC (offset_ac) che compensa la
    differenza: arrotondata a 0,5 °C, tra -3 e +3; None con poche letture."""
    vuoto = {"media": None, "letture": 0, "suggerita": None}
    if not room_id or not ac_id:
        return vuoto
    inizio = (datetime.now() - timedelta(days=giorni)).strftime("%Y-%m-%dT%H:%M")
    with _connetti() as conn:
        riga = conn.execute(
            """SELECT AVG(a.t_ambiente - s.t_ambiente) AS media, COUNT(*) AS letture
               FROM letture_dispositivi a
               JOIN letture_dispositivi s ON s.ts = a.ts AND s.tipo = 'stanza' AND s.id = ?
               WHERE a.tipo = 'ac' AND a.id = ? AND a.ts >= ? AND a.attivo = 1 AND a.modalita = 'heat'
                 AND a.t_ambiente IS NOT NULL AND s.t_ambiente IS NOT NULL""",
            (room_id, ac_id, inizio)).fetchone()
    if not riga or not riga["letture"]:
        return vuoto
    media = round(riga["media"], 2)
    suggerita = None
    if riga["letture"] >= LETTURE_MINIME_DIFFERENZA:
        suggerita = max(-3.0, min(3.0, round(media * 2) / 2))
    return {"media": media, "letture": riga["letture"], "suggerita": suggerita}


def potenza_media_ac(giorni: int = 30) -> Optional[float]:
    """Assorbimento medio (kW) dei condizionatori quando sono accesi, dalle letture
    consecutive entrambe ad AC acceso e distanti al massimo un'ora. None se
    ci sono meno di due ore di dati utili."""
    da = (datetime.now() - timedelta(days=giorni)).strftime("%Y-%m-%dT%H:%M")
    with _connetti() as conn:
        righe = conn.execute(
            """SELECT ts, id, attivo, energia_wh FROM letture_dispositivi
               WHERE tipo = 'ac' AND energia_wh IS NOT NULL AND ts >= ?
               ORDER BY id, ts""",
            (da,),
        ).fetchall()
    kwh = ore = 0.0
    precedente: dict = {}
    for r in righe:
        p = precedente.get(r["id"])
        if p and p["attivo"] and r["attivo"]:
            dt = (datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M")
                  - datetime.strptime(p["ts"], "%Y-%m-%dT%H:%M")).total_seconds() / 3600
            if 0 < dt <= 1 and r["energia_wh"] >= p["energia_wh"]:
                kwh += (r["energia_wh"] - p["energia_wh"]) / 1000.0
                ore += dt
        precedente[r["id"]] = r
    if ore < 2:
        return None
    return round(kwh / ore, 2)


# ─── Campionatore ─────────────────────────────────────────────────────────────

class CampionatoreStorico:
    """Thread daemon che registra un campione per ogni ora e, se c'e' la
    callback, le letture dei dispositivi ogni LETTURE_SECONDI."""

    def __init__(self, produci_campione: Callable[[], Optional[dict]],
                 produci_letture: Optional[Callable[[], list]] = None):
        self._produci_campione = produci_campione
        self._produci_letture = produci_letture
        self._ultime_letture = 0.0
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
                if time.time() - ultima_pulizia > 86400:
                    _pulisci_vecchi()
                    from termopilota import registro
                    registro.pulisci()
                    ultima_pulizia = time.time()
            except Exception as e:
                logger.warning("Errore campionatore storico: %s", e)
            try:
                self._leggi_dispositivi()
            except Exception as e:
                logger.warning("Errore letture dispositivi: %s", e)
            self._stop_event.wait(timeout=min(CONTROLLO_SECONDI, LETTURE_SECONDI))

    def _leggi_dispositivi(self) -> None:
        # Una lettura a ogni quarto d'ora (ts allineato: 00, 15, 30, 45)
        if not self._produci_letture or time.time() - self._ultime_letture < LETTURE_SECONDI - 30:
            return
        adesso = datetime.now()
        righe = self._produci_letture()
        self._ultime_letture = time.time()
        if righe:
            ts = adesso.replace(minute=adesso.minute - adesso.minute % 15).strftime("%Y-%m-%dT%H:%M")
            registra_letture(ts, righe)

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


def avvia_campionatore(produci_campione: Callable[[], Optional[dict]],
                       produci_letture: Optional[Callable[[], list]] = None) -> None:
    """Avvia il campionatore globale (idempotente). Chiamato da app.py all'avvio."""
    global _campionatore
    if _campionatore is None:
        _campionatore = CampionatoreStorico(produci_campione, produci_letture)
    _campionatore.avvia()
