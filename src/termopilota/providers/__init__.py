# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Architettura modulare a provider per termostati e pompe di calore.

Per aggiungere un nuovo provider:
1. Creare un modulo in providers/ che implementi ThermostatProvider o HeatPumpProvider
2. Registrarlo con register_thermostat() o register_heatpump() a livello di modulo
3. Importare il modulo (vedi fondo di questo file)
"""

import json
import logging
import os
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from typing import Optional

import requests

_scrittura_lock = threading.Lock()


def scrivi_json_atomico(path: str, dati: dict) -> None:
    """Scrive un dict in JSON in modo atomico (tempfile + os.replace).

    Garantisce che il file di destinazione non venga mai lasciato troncato
    in caso di crash / kill durante la scrittura. Thread-safe via lock di modulo.
    """
    with _scrittura_lock:
        directory = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(dati, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            # Su Windows la destinazione puo' essere bloccata per un attimo
            # (antivirus, indicizzazione, un lettore concorrente): si riprova
            for tentativo in range(5):
                try:
                    os.replace(tmp_path, path)
                    break
                except PermissionError:
                    if tentativo == 4:
                        raise
                    time.sleep(0.05 * (tentativo + 1))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# ── Chiamate alle API dei dispositivi: conteggio e limite superato ───────────
# Netatmo concede 500 richieste l'ora per utente (e 50 ogni 10 s); oltre risponde
# 403 con codice 26 o 429. Le chiamate passano tutte da qui: si contano (ultima
# ora, mostrate in Credenziali API) e, se il limite e' superato, per PAUSA_LIMITE_S
# non se ne fanno altre invece di insistere.

PAUSA_LIMITE_S = 600
FINESTRA_CONTEGGIO_S = 3600
_log = logging.getLogger(__name__)
_chiamate: dict = {}          # servizio -> deque di epoch
_pausa_fino: dict = {}        # servizio -> epoch
_lock_chiamate = threading.Lock()


class LimiteChiamate(RuntimeError):
    """Il servizio ha segnalato il limite di richieste: si aspetta prima di riprovare."""

    def __init__(self, servizio: str, fino: float):
        super().__init__(f"limite di richieste {servizio} superato, nuove chiamate dalle "
                         f"{time.strftime('%H:%M', time.localtime(fino))}")
        self.servizio, self.fino = servizio, fino


def _limite_superato(resp) -> bool:
    if resp.status_code == 429:
        return True
    if resp.status_code == 403:
        try:
            errore = resp.json().get("error") or {}
            return isinstance(errore, dict) and errore.get("code") == 26
        except ValueError:
            return False
    return False


def chiamata(servizio: str, metodo: str, url: str, **parametri):
    """requests.<metodo>(url, ...) contata per `servizio`; LimiteChiamate in pausa."""
    adesso = time.time()
    with _lock_chiamate:
        if _pausa_fino.get(servizio, 0) > adesso:
            raise LimiteChiamate(servizio, _pausa_fino[servizio])
        coda = _chiamate.setdefault(servizio, deque())
        coda.append(adesso)
        while coda and coda[0] < adesso - FINESTRA_CONTEGGIO_S:
            coda.popleft()
    resp = getattr(requests, metodo)(url, **parametri)
    if _limite_superato(resp):
        fino = time.time() + PAUSA_LIMITE_S
        with _lock_chiamate:
            gia_in_pausa = _pausa_fino.get(servizio, 0) > adesso
            _pausa_fino[servizio] = fino
        if not gia_in_pausa:
            _log.warning("%s: limite di richieste superato (%s chiamate nell'ultima ora), pausa di %d minuti",
                         servizio, conteggio_chiamate().get(servizio, 0), PAUSA_LIMITE_S // 60)
        raise LimiteChiamate(servizio, fino)
    return resp


def conteggio_chiamate() -> dict:
    """{servizio: chiamate nell'ultima ora}, piu' 'pausa_fino' per i servizi in pausa."""
    adesso = time.time()
    with _lock_chiamate:
        risultato = {s: sum(1 for t in coda if t >= adesso - FINESTRA_CONTEGGIO_S) for s, coda in _chiamate.items()}
        risultato["pausa_fino"] = {s: t for s, t in _pausa_fino.items() if t > adesso}
    return risultato


def azzera_chiamate() -> None:
    """Solo per i test."""
    with _lock_chiamate:
        _chiamate.clear()
        _pausa_fino.clear()


def aggiorna_config_atomico(path: str, mutator) -> None:
    """Carica il config, applica `mutator(cfg)` e riscrive in modo atomico.

    Usato dai provider per aggiornare un singolo campo (es. token OAuth) senza
    sovrascrivere il resto del file di configurazione.
    """
    with _scrittura_lock:
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        mutator(cfg)
    scrivi_json_atomico(path, cfg)


# ── Interfacce astratte ──────────────────────────────────────────────────────

class ThermostatProvider(ABC):
    """Interfaccia per provider di termostati (lettura temperature, setpoint, controllo modalita')."""

    @property
    @abstractmethod
    def autenticato(self) -> bool:
        """True se il provider ha credenziali valide."""
        ...

    @abstractmethod
    def lista_impianti(self) -> list:
        """Restituisce [{id, name}, ...] degli impianti/case disponibili."""
        ...

    @abstractmethod
    def lista_moduli(self, home_id: str) -> list:
        """Restituisce [{id, name}, ...] dei termostati nell'impianto."""
        ...

    @abstractmethod
    def imposta_modalita(self, home_id: str, room_id: str, mode: str, setpoint: float = 7.0,
                         fine: Optional[int] = None) -> bool:
        """Imposta modalita' termostato. mode: 'OFF' (manuale a `setpoint` fino a `fine`,
        epoch) o 'AUTOMATIC' (torna al programma)."""
        ...

    # Facoltativi: servono alla pagina Dispositivi e ai comandi avanzati.

    def stato_casa(self, home_id: str) -> dict:
        """Dati completi dell'impianto: {'dati': configurazione, 'stato': stato corrente}."""
        raise NotImplementedError

    def imposta_modalita_casa(self, home_id: str, mode: str, fine: Optional[int] = None) -> bool:
        raise NotImplementedError

    def cambia_programma(self, home_id: str, schedule_id: str) -> bool:
        raise NotImplementedError


class HeatPumpProvider(ABC):
    """Interfaccia per provider di pompe di calore / condizionatori."""

    @property
    @abstractmethod
    def configurato(self) -> bool:
        """True se il provider ha credenziali configurate."""
        ...

    @abstractmethod
    def lista_dispositivi_ac(self) -> list:
        """Restituisce [{device_id, label, location_id}, ...] dei condizionatori."""
        ...

    @abstractmethod
    def stato_ac(self, device_id: str) -> dict:
        """Restituisce {device_id, acceso, modalita, setpoint_riscaldamento, temperatura_ambiente}."""
        ...

    @abstractmethod
    def accendi_ac(self, device_id: str, setpoint: float = 21.0) -> bool:
        """Accende il condizionatore in riscaldamento al setpoint indicato."""
        ...

    @abstractmethod
    def spegni_ac(self, device_id: str) -> bool:
        """Spegne il condizionatore."""
        ...

    # Facoltativi: servono alla pagina Dispositivi e ai comandi avanzati.

    def stato_completo(self, device_id: str) -> dict:
        """Stato grezzo del dispositivo, cosi' come lo restituisce l'API."""
        raise NotImplementedError

    def esegui_comando(self, device_id: str, capability: str, comando: str,
                       argomenti: Optional[list] = None) -> bool:
        raise NotImplementedError


# ── Registry ─────────────────────────────────────────────────────────────────

_thermostat_factories: dict[str, callable] = {}
_heatpump_factories: dict[str, callable] = {}


def register_thermostat(name: str, factory):
    """Registra una factory function: factory(cfg) -> ThermostatProvider | None."""
    _thermostat_factories[name] = factory


def register_heatpump(name: str, factory):
    """Registra una factory function: factory(cfg) -> HeatPumpProvider | None."""
    _heatpump_factories[name] = factory


def get_thermostat(name: str, cfg: dict) -> Optional[ThermostatProvider]:
    """Crea un'istanza del provider termostato dal nome e config."""
    factory = _thermostat_factories.get(name)
    if factory:
        return factory(cfg)
    return None


def get_heatpump(name: str, cfg: dict) -> Optional[HeatPumpProvider]:
    """Crea un'istanza del provider pompa di calore dal nome e config."""
    factory = _heatpump_factories.get(name)
    if factory:
        return factory(cfg)
    return None


def available_thermostats() -> list[str]:
    return list(_thermostat_factories.keys())


def available_heatpumps() -> list[str]:
    return list(_heatpump_factories.keys())


# ── Import provider concreti (si auto-registrano) ────────────────────────────

from termopilota.providers import netatmo  # noqa: E402, F401
from termopilota.providers import smartthings  # noqa: E402, F401
