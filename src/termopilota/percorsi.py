# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Percorsi dei dati persistenti: unica definizione per tutti i moduli.

TERMOPILOTA_DATA_DIR sposta tutto (volume Docker, test); altrimenti si usa
`data/` nella cartella da cui parte l'app.
"""

import os


DATA_DIR = os.environ.get("TERMOPILOTA_DATA_DIR") or os.path.join(os.getcwd(), "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
# Stato di runtime dell'automazione (override in corso, pause, AC accesi da noi)
STATO_AUTOMAZIONE_FILE = os.path.join(DATA_DIR, "automazione_stato.json")
