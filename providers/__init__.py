"""
Architettura modulare a provider per termostati e pompe di calore.

Per aggiungere un nuovo provider:
1. Creare un modulo in providers/ che implementi ThermostatProvider o HeatPumpProvider
2. Registrarlo con register_thermostat() o register_heatpump() a livello di modulo
3. Importare il modulo (vedi fondo di questo file)
"""

import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from typing import Optional

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
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


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
    def stato_tutte_stanze(self, home_id: str) -> dict:
        """Restituisce {room_id: {temperatura_attuale, setpoint, modalita, sta_riscaldando}} per tutte le stanze."""
        ...

    @abstractmethod
    def imposta_modalita(self, home_id: str, room_id: str, mode: str, setpoint: float = 7.0) -> bool:
        """Imposta modalita' termostato. mode: 'OFF' (manual bassa T) o 'AUTOMATIC' (schedule)."""
        ...


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

from providers import netatmo  # noqa: E402, F401
from providers import smartthings  # noqa: E402, F401
