# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Una release non deve partire con numeri di versione incoerenti."""

import json
import os
import re

from versione import VERSIONE

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEMVER = r"\d+\.\d+\.\d+"


def _leggi(*percorso):
    with open(os.path.join(RADICE, *percorso), encoding="utf-8") as f:
        return f.read()


def test_versione_e_semver():
    assert re.fullmatch(SEMVER, VERSIONE)


def test_package_json_e_lock_hanno_la_stessa_versione():
    assert json.loads(_leggi("package.json"))["version"] == VERSIONE
    lock = json.loads(_leggi("package-lock.json"))
    assert lock["version"] == VERSIONE
    assert lock["packages"][""]["version"] == VERSIONE


def test_changelog_ha_la_versione_corrente_come_ultima_release():
    voci = re.findall(rf"^## \[({SEMVER})\] - (\d{{4}}-\d{{2}}-\d{{2}})$", _leggi("CHANGELOG.md"), re.M)
    assert voci, "nessuna release nel CHANGELOG.md"
    assert voci[0][0] == VERSIONE, f"ultima release nel changelog {voci[0][0]}, codice {VERSIONE}"


def test_changelog_ha_la_sezione_non_rilasciato_e_i_link():
    testo = _leggi("CHANGELOG.md")
    assert "## [Non rilasciato]" in testo
    assert f"[{VERSIONE}]: https://github.com/gcerretani/termopilota/releases/tag/v{VERSIONE}" in testo


def test_changelog_non_ha_versioni_duplicate():
    versioni = re.findall(rf"^## \[({SEMVER})\]", _leggi("CHANGELOG.md"), re.M)
    assert len(versioni) == len(set(versioni))


def test_la_versione_compare_nel_footer(admin_client):
    html = admin_client.get("/").get_data(as_text=True)
    assert f"v{VERSIONE}" in html


def test_il_workflow_docker_pubblica_i_tag_di_versione():
    testo = _leggi(".github", "workflows", "docker.yml")
    assert '"v*.*.*"' in testo
    assert "type=semver,pattern={{version}}" in testo
