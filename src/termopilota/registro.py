# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Registro eventi persistente (tabella `registro` di data/storico.db): cosa e'
successo, quando, a quale oggetto (zona, stanza, condizionatore) e per mano di
chi. Alimenta la pagina /registro e gli "Ultimi eventi" dell'Automazione.

Categorie:
  automazione  decisioni e comandi del ciclo
  comando      azioni degli utenti (comandi manuali, zone, pause, configurazione)
  evento       notifiche dei dispositivi (webhook) e cambi rilevati dal polling
  sistema      avvio, warning ed errori dei moduli, rifiuti dei webhook

`scrivi` non solleva mai: un registro non disponibile non deve far fallire un
comando. I `dati` si ripuliscono dalle chiavi sensibili prima di salvarli.
"""

import json
import logging
import re
import time
from typing import Optional

from termopilota import storico

logger = logging.getLogger(__name__)

CATEGORIE = ("automazione", "comando", "evento", "sistema")
LIVELLI = ("debug", "info", "warning", "errore")
RITENZIONE_GIORNI = 30
RITENZIONE_DEBUG_GIORNI = 7
RIGHE_MASSIME = 200_000
DATI_MAX_BYTE = 8192
LIMITE_LETTURA = 500
_SENSIBILI = ("token", "secret", "password", "authorization", "cookie")

SCHEMA = """
CREATE TABLE IF NOT EXISTS registro (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,            -- epoch
    categoria TEXT NOT NULL,
    livello TEXT NOT NULL,
    oggetto TEXT,                -- zona, stanza o condizionatore (nome leggibile)
    messaggio TEXT NOT NULL,
    utente TEXT,
    dati TEXT                    -- JSON ripulito dai segreti
);
CREATE INDEX IF NOT EXISTS idx_registro_ts ON registro (ts);
CREATE INDEX IF NOT EXISTS idx_registro_categoria ON registro (categoria, ts);
"""

_LIVELLO_LOGGING = {"debug": logging.DEBUG, "info": logging.INFO,
                    "warning": logging.WARNING, "errore": logging.ERROR}


def ripulisci(valore, profondita: int = 0):
    """Copia di `valore` senza chiavi sensibili (token, secret, password...)."""
    if profondita > 8:
        return "…"
    if isinstance(valore, dict):
        return {str(k): ("***" if any(s in str(k).lower() for s in _SENSIBILI)
                         else ripulisci(v, profondita + 1))
                for k, v in valore.items()}
    if isinstance(valore, (list, tuple)):
        return [ripulisci(v, profondita + 1) for v in valore[:200]]
    if isinstance(valore, (str, int, float, bool)) or valore is None:
        return valore
    return str(valore)


def _serializza(dati) -> Optional[str]:
    if dati is None:
        return None
    testo = json.dumps(ripulisci(dati), ensure_ascii=False, default=str)
    if len(testo.encode("utf-8")) > DATI_MAX_BYTE:
        testo = json.dumps({"troncato": True, "inizio": testo[:DATI_MAX_BYTE // 2]}, ensure_ascii=False)
    return testo


# "503 Server Error: ... for url: https://host/percorso?query" di requests
_URL_ERRORE = re.compile(r"\s*for url:\s*https?://([^/\s?]+)\S*")
_URL = re.compile(r"https?://([^/\s?]+)[^\s]*")


def accorcia_url(messaggio: str) -> str:
    """Riduce gli URL di un messaggio al solo host: nel registro restano leggibili
    (le query string sono lunghe e possono contenere chiavi o coordinate)."""
    messaggio = _URL_ERRORE.sub(r" (\1)", messaggio)
    return _URL.sub(r"\1", messaggio)


def scrivi(categoria: str, messaggio: str, *, livello: str = "info", oggetto: Optional[str] = None,
           dati=None, utente: Optional[str] = None) -> None:
    """Aggiunge una riga al registro e la riporta nel log del processo."""
    if categoria not in CATEGORIE:
        categoria = "sistema"
    if livello not in LIVELLI:
        livello = "info"
    # Nel log del container (Portainer); il logger di questo modulo e' escluso
    # dal GestoreLogRegistro, quindi niente ricorsione
    logger.log(_LIVELLO_LOGGING[livello], "[%s] %s%s%s", categoria,
               f"{oggetto}: " if oggetto else "", messaggio, f" ({utente})" if utente else "")
    try:
        with storico._connetti() as conn:
            conn.execute(
                "INSERT INTO registro (ts, categoria, livello, oggetto, messaggio, utente, dati) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), categoria, livello, oggetto, str(messaggio)[:1000], utente, _serializza(dati)))
    except Exception as e:     # registro non disponibile: si perde la riga, non il comando
        logger.debug("Registro non scritto: %s", e)


def leggi(categorie: Optional[list] = None, livello_min: str = "info", oggetto=None,
          testo: Optional[str] = None, prima_di: Optional[float] = None, limite: int = 100) -> list[dict]:
    """Righe piu' recenti (in ordine decrescente) che rispettano i filtri."""
    condizioni, parametri = [], []
    if categorie:
        condizioni.append(f"categoria IN ({','.join('?' * len(categorie))})")
        parametri += list(categorie)
    if livello_min in LIVELLI and livello_min != "debug":
        ammessi = LIVELLI[LIVELLI.index(livello_min):]
        condizioni.append(f"livello IN ({','.join('?' * len(ammessi))})")
        parametri += list(ammessi)
    if oggetto:
        oggetti_filtro = [oggetto] if isinstance(oggetto, str) else [o for o in oggetto if o]
        if oggetti_filtro:
            condizioni.append(f"oggetto IN ({','.join('?' * len(oggetti_filtro))})")
            parametri += oggetti_filtro
    if testo:
        condizioni.append("(messaggio LIKE ? OR oggetto LIKE ? OR utente LIKE ?)")
        parametri += [f"%{testo}%"] * 3
    if prima_di:
        condizioni.append("ts < ?")
        parametri.append(float(prima_di))
    where = f"WHERE {' AND '.join(condizioni)}" if condizioni else ""
    limite = max(1, min(LIMITE_LETTURA, int(limite)))
    try:
        with storico._connetti() as conn:
            righe = conn.execute(f"SELECT * FROM registro {where} ORDER BY ts DESC, id DESC LIMIT ?",
                                 (*parametri, limite)).fetchall()
    except Exception as e:
        logger.warning("Registro non leggibile: %s", e)
        return []
    risultato = []
    for r in righe:
        voce = dict(r)
        try:
            voce["dati"] = json.loads(voce["dati"]) if voce["dati"] else None
        except ValueError:
            pass
        risultato.append(voce)
    return risultato


def oggetti() -> list[str]:
    """Oggetti presenti nel registro (per il filtro della pagina)."""
    try:
        with storico._connetti() as conn:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT oggetto FROM registro WHERE oggetto IS NOT NULL ORDER BY oggetto")]
    except Exception:
        return []


def pulisci(adesso: Optional[float] = None) -> None:
    adesso = adesso or time.time()
    with storico._connetti() as conn:
        conn.execute("DELETE FROM registro WHERE ts < ?", (adesso - RITENZIONE_GIORNI * 86400,))
        conn.execute("DELETE FROM registro WHERE livello = 'debug' AND ts < ?",
                     (adesso - RITENZIONE_DEBUG_GIORNI * 86400,))
        conn.execute("DELETE FROM registro WHERE id <= (SELECT MAX(id) FROM registro) - ?", (RIGHE_MASSIME,))


class GestoreLogRegistro(logging.Handler):
    """Porta nel registro (categoria 'sistema') i warning e gli errori dei moduli."""

    def __init__(self):
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__ or not record.name.startswith("termopilota"):
            return
        try:
            livello = "errore" if record.levelno >= logging.ERROR else "warning"
            dati = {"modulo": record.name}
            if record.exc_info:
                dati["eccezione"] = logging.Formatter().formatException(record.exc_info)[-2000:]
            scrivi("sistema", accorcia_url(record.getMessage()), livello=livello, dati=dati)
        except Exception:
            pass


def collega_logging() -> None:
    """Aggiunge il gestore al logger 'termopilota' (una volta sola)."""
    radice = logging.getLogger("termopilota")
    if not any(isinstance(h, GestoreLogRegistro) for h in radice.handlers):
        radice.addHandler(GestoreLogRegistro())
