"""Shim di compatibilita' — il codice reale e' in providers/netatmo.py"""

from providers.netatmo import NetatmoClient, client_da_config  # noqa: F401

LegrandClient = NetatmoClient
