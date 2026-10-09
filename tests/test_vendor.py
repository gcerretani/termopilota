# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""src/termopilota/static/vendor/ non e' nel repository: si genera da package.json con
`npm ci && npm run vendor`. Questi test garantiscono che la cartella esista e
corrisponda esattamente alle versioni bloccate in package-lock.json."""

import json
import os
import re

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(RADICE, "src", "termopilota", "static", "vendor")
ISTRUZIONI = "esegui `npm ci && npm run vendor`"

FILE_ATTESI = [
    "bootstrap/bootstrap.min.css",
    "bootstrap/bootstrap.bundle.min.js",
    "bootstrap-icons/bootstrap-icons.min.css",
    "bootstrap-icons/fonts/bootstrap-icons.woff2",
    "bootstrap-icons/fonts/bootstrap-icons.woff",
    "chartjs/chart.umd.min.js",
    "leaflet/leaflet.js",
    "leaflet/leaflet.css",
    "leaflet/images/marker-icon.png",
    "leaflet/images/marker-icon-2x.png",
    "leaflet/images/marker-shadow.png",
]


def _versioni_lock():
    with open(os.path.join(RADICE, "package-lock.json"), encoding="utf-8") as f:
        pacchetti = json.load(f)["packages"]
    return {nome: pacchetti[f"node_modules/{nome}"]["version"]
            for nome in ("bootstrap", "bootstrap-icons", "chart.js", "leaflet")}


def _intestazione(percorso):
    with open(os.path.join(VENDOR, percorso), encoding="utf-8") as f:
        return f.read(400)


def test_vendor_generato_con_tutti_i_file():
    mancanti = [f for f in FILE_ATTESI if not os.path.isfile(os.path.join(VENDOR, f))]
    assert not mancanti, f"file mancanti in src/termopilota/static/vendor/: {mancanti}: {ISTRUZIONI}"


def test_versioni_vendor_coincidono_con_il_lock():
    versioni = _versioni_lock()
    controlli = {
        "bootstrap": "bootstrap/bootstrap.min.css",
        "bootstrap-icons": "bootstrap-icons/bootstrap-icons.min.css",
        "chart.js": "chartjs/chart.umd.min.js",
        "leaflet": "leaflet/leaflet.js",
    }
    for pacchetto, file in controlli.items():
        # "v5.3.8" (Bootstrap, Chart.js) oppure "Leaflet 1.9.4"
        trovata = re.search(r"(?:v|Leaflet )(\d+\.\d+\.\d+)", _intestazione(file))
        assert trovata, f"versione non trovata nell'intestazione di {file}"
        assert trovata.group(1) == versioni[pacchetto], (
            f"{pacchetto}: file {trovata.group(1)} ma package-lock {versioni[pacchetto]}: {ISTRUZIONI}")


def test_vendor_non_e_nel_repository():
    # La cartella e' generata: committarla rimetterebbe le librerie fuori dalla portata di Dependabot
    with open(os.path.join(RADICE, ".gitignore"), encoding="utf-8") as f:
        righe = [r.strip() for r in f]
    assert "src/termopilota/static/vendor/" in righe and "node_modules/" in righe


def test_package_json_blocca_versioni_esatte():
    # Niente ^ o ~: l'aggiornamento passa solo da una PR di Dependabot
    with open(os.path.join(RADICE, "package.json"), encoding="utf-8") as f:
        dipendenze = json.load(f)["dependencies"]
    for nome, versione in dipendenze.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", versione), f"{nome}: {versione} non e' esatta"
