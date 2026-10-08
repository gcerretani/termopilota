# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""La licenza deve restare coerente: testo ufficiale e intestazione in ogni sorgente."""

import hashlib
import os

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPDX = "SPDX-License-Identifier: GPL-3.0-or-later"
# SHA-256 del testo ufficiale della GNU GPL v3 (gpl-3.0.txt, 29 giugno 2007)
SHA256_GPL3 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"
CARTELLE_ESCLUSE = {"venv", ".git", "__pycache__", "vendor", "node_modules", "build", "dist"}


def _file(estensioni):
    for cartella, sottocartelle, files in os.walk(RADICE):
        sottocartelle[:] = [d for d in sottocartelle if d not in CARTELLE_ESCLUSE and not d.startswith(".")]
        for nome in files:
            if nome.endswith(estensioni):
                yield os.path.join(cartella, nome)


def test_license_e_il_testo_ufficiale_della_gpl3():
    with open(os.path.join(RADICE, "LICENSE"), "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == SHA256_GPL3


def test_ogni_sorgente_ha_l_identificatore_spdx():
    senza = []
    for percorso in _file((".py", ".js", ".css")):
        if os.path.getsize(percorso) == 0:
            continue
        with open(percorso, encoding="utf-8") as f:
            testa = f.read(300)  # puo' seguire un shebang
        if SPDX not in testa:
            senza.append(os.path.relpath(percorso, RADICE))
    assert not senza, f"manca {SPDX} in: {senza}"


def test_librerie_vendorizzate_mantengono_l_avviso_mit():
    # La MIT impone di conservare l'avviso di copyright insieme al codice
    vendor = os.path.join(RADICE, "src", "termopilota", "static", "vendor")
    for cartella, _, files in os.walk(vendor):
        for nome in files:
            if nome.endswith((".css", ".js")):
                with open(os.path.join(cartella, nome), encoding="utf-8") as f:
                    testa = f.read(500)
                assert "MIT" in testa and ("Copyright" in testa or "(c)" in testa), nome


def test_readme_cita_la_licenza():
    with open(os.path.join(RADICE, "README.md"), encoding="utf-8") as f:
        assert "GNU General Public License" in f.read()
