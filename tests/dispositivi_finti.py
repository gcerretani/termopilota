# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Client SmartThings e Netatmo finti per i test, con i dati anonimizzati
delle fixture (ricavati da un impianto reale) e il registro dei comandi."""

import copy
import json
import os

CARTELLA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def carica(nome: str) -> dict:
    with open(os.path.join(CARTELLA, nome), encoding="utf-8") as f:
        return json.load(f)


# Definizioni delle capability non standard (come /capabilities/{id}/{versione})
DEFINIZIONI = {
    "custom.airConditionerOptionalMode": {"commands": {"setAcOptionalMode": {}}},
    "samsungce.airConditionerLighting": {"commands": {"setLightingLevel": {}}},
    "samsungce.airConditionerBeep": {"commands": {"on": {}, "off": {}}},
    "custom.autoCleaningMode": {"commands": {"setAutoCleaningMode": {}}},
    "samsungce.dustFilterAlarm": {"commands": {"setAlarmThreshold": {}}},
    "custom.dustFilter": {"commands": {"resetDustFilter": {}}},
}


class SmartThingsFinto:
    configurato = True
    client_id = "finto"

    def __init__(self, stato=None, definizioni=None, esito=True):
        self.stato = stato if stato is not None else carica("smartthings_status_ac.json")
        self.definizioni = DEFINIZIONI if definizioni is None else definizioni
        self.esito = esito
        self.comandi = []

    def lista_dispositivi_ac(self):
        return [{"device_id": "ac-1", "label": "Condizionatore Salotto", "location_id": "loc",
                 "capability": {cap: 1 for cap in self.stato}, "ocf": {"vid": "DA-AC-RAC-01011"}}]

    def stato_completo(self, device_id):
        return copy.deepcopy(self.stato)

    def definizione_capability(self, capability, versione=1):
        return self.definizioni.get(capability)

    def esegui_comando(self, device_id, capability, comando, argomenti=None):
        self.comandi.append((device_id, capability, comando, list(argomenti or [])))
        return self.esito

    def accendi_ac(self, device_id, setpoint=21.0, modalita="heat", ventola=None, modalita_opzionale=None):
        self.comandi.append((device_id, "accendi", setpoint, ventola, modalita_opzionale))
        return self.esito

    def spegni_ac(self, device_id):
        self.comandi.append((device_id, "spegni"))
        return self.esito


class NetatmoFinto:
    autenticato = True

    def __init__(self, casa=None, esito=True):
        self.casa = casa if casa is not None else carica("netatmo_casa.json")
        self.esito = esito
        self.comandi = []

    def stato_casa(self, home_id):
        return copy.deepcopy(self.casa)

    def stato_tutte_stanze(self, home_id):
        from termopilota.providers.netatmo import normalizza_stanza
        return {r["id"]: normalizza_stanza(r) for r in self.casa["stato"]["rooms"]}

    def imposta_modalita(self, home_id, room_id, mode, setpoint=7.0, fine=None):
        self.comandi.append((room_id, mode, setpoint, fine))
        return self.esito

    def imposta_modalita_casa(self, home_id, mode, fine=None):
        self.comandi.append(("casa", mode, fine))
        return self.esito

    def cambia_programma(self, home_id, schedule_id):
        self.comandi.append(("programma", schedule_id))
        return self.esito
