# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pianificazione dell'automazione (funzione pura): decisioni per zona, comandi
solo al cambio, pause, manuale dell'utente, modalita' affiancata, rilascio."""

from datetime import datetime

from termopilota.automazione import in_fascia_notte, pianifica, rilascio, stato_vuoto

ADESSO = 1_800_000_000.0


def stanza(t=19.0, setpoint=20.0, target=20.0, modalita="home", **altro):
    return {"temperatura_attuale": t, "setpoint": setpoint, "target": target, "modalita": modalita,
            "raggiungibile": True, "finestra_aperta": False, "setpoint_fine": None, **altro}


def ac(acceso=False, setpoint=25, modalita="cool"):
    return {"acceso": acceso, "setpoint_riscaldamento": setpoint, "modalita": modalita,
            "ventola": "auto", "modalita_opzionale": "off", "setpoint_min": 16, "setpoint_max": 30}


def contesto(stanze, reali_ac=None, costo_gas=0.11, costo_ac=0.07, ora=14, **altro):
    return {
        "adesso": ADESSO, "ora": datetime(2026, 1, 12, ora, 0), "t_ext": 6.0,
        "costo_gas": costo_gas, "costo_ac": costo_ac, "soglia": 0.01, "t_min_ac": -10,
        "intervallo_min": 15, "stanze": stanze, "ac": reali_ac if reali_ac is not None else {"ac-1": ac()},
        "casa": {"temperature_control_mode": "heating"}, "diagnosi": {},
        "ac_ventola": "auto", "ac_modalita_notte": "quiet", "notte_inizio": 22, "notte_fine": 7,
        "pausa_manuale_ore": 3, **altro,
    }


ZONA = {"nome": "Salotto", "room_id": "r1", "ac_device_id": "ac-1"}


def test_ac_conveniente_chiude_il_termostato_e_accende_l_ac():
    piano = pianifica([ZONA], contesto({"r1": stanza()}), stato_vuoto())
    assert piano["zone"][0]["stato"] == "ac"
    [azione] = piano["netatmo"]
    assert azione["tipo"] == "manual" and azione["setpoint"] == 7.0
    assert azione["fine"] == ADESSO + 3600   # max(3 × 15 min, 1 h)
    [acc] = piano["ac"]
    assert acc["tipo"] == "accendi" and acc["setpoint"] == 20
    assert piano["stato"]["ac"]["ac-1"]["acceso_da_noi"] is True


def test_nessuna_oscillazione_con_la_stanza_a_7_gradi():
    # Regressione: dopo l'override il termostato legge 7 °C, ma il target resta
    # quello del programma: la zona resta in AC e non si reinvia nulla.
    primo = pianifica([ZONA], contesto({"r1": stanza()}), stato_vuoto())
    reali = {"ac-1": ac(acceso=True, setpoint=20, modalita="heat")}
    secondo = pianifica([ZONA], contesto({"r1": stanza(setpoint=7.0, modalita="manual")}, reali), primo["stato"])
    assert secondo["zone"][0]["stato"] == "ac"
    assert secondo["netatmo"] == [] and secondo["ac"] == []
    assert secondo["eventi"] == []    # nessun evento ripetuto


def test_isteresi_resta_in_ac_fino_a_target_piu_mezzo_grado():
    primo = pianifica([ZONA], contesto({"r1": stanza(t=19.0)}), stato_vuoto())
    reali = {"ac-1": ac(acceso=True, setpoint=20, modalita="heat")}
    resta = pianifica([ZONA], contesto({"r1": stanza(t=20.3, setpoint=7, modalita="manual")}, reali), primo["stato"])
    assert resta["zone"][0]["stato"] == "ac"
    esce = pianifica([ZONA], contesto({"r1": stanza(t=20.6, setpoint=7, modalita="manual")}, reali), resta["stato"])
    assert esce["zone"][0]["stato"] == "gas"
    assert esce["netatmo"] == [{"room_id": "r1", "zona": "Salotto", "tipo": "home"}]
    assert esce["ac"] == [{"device_id": "ac-1", "tipo": "spegni"}]


def test_vicino_al_target_non_entra_in_ac():
    piano = pianifica([ZONA], contesto({"r1": stanza(t=19.7)}), stato_vuoto())
    assert piano["zone"][0]["stato"] == "gas" and piano["ac"] == [] and piano["netatmo"] == []


def test_gas_conveniente_non_tocca_un_ac_acceso_a_mano():
    reali = {"ac-1": ac(acceso=True, modalita="cool")}
    piano = pianifica([ZONA], contesto({"r1": stanza()}, reali, costo_gas=0.06), stato_vuoto())
    assert piano["zone"][0]["stato"] == "gas"
    assert "Gas conviene" in piano["zone"][0]["motivo"]
    assert piano["ac"] == [] and piano["netatmo"] == []


def test_modalita_affiancata_lascia_la_caldaia_di_riserva():
    zona = dict(ZONA, modalita="affiancata", riserva_gas_delta=1.5)
    piano = pianifica([zona], contesto({"r1": stanza()}), stato_vuoto())
    assert piano["zone"][0]["stato"] == "affiancata"
    assert piano["netatmo"][0]["setpoint"] == 18.5
    assert piano["ac"][0]["setpoint"] == 20


def test_modalita_affiancata_senza_margine_scaldano_in_parallelo():
    zona = dict(ZONA, modalita="affiancata", riserva_gas_delta=0)
    piano = pianifica([zona], contesto({"r1": stanza()}), stato_vuoto())
    assert piano["netatmo"][0]["setpoint"] == 20 and piano["ac"][0]["setpoint"] == 20


def test_offset_ac_si_somma_al_target():
    piano = pianifica([dict(ZONA, offset_ac=1.0)], contesto({"r1": stanza()}), stato_vuoto())
    assert piano["ac"][0]["setpoint"] == 21


def test_zona_esclusa_non_tocca_nulla_e_rilascia_i_nostri_override():
    primo = pianifica([ZONA], contesto({"r1": stanza()}), stato_vuoto())
    esclusa = dict(ZONA, automazione=False)
    reali = {"ac-1": ac(acceso=True, setpoint=20, modalita="heat")}
    piano = pianifica([esclusa], contesto({"r1": stanza(setpoint=7, modalita="manual")}, reali), primo["stato"])
    assert piano["zone"][0]["stato"] == "esclusa"
    assert piano["netatmo"] == [{"room_id": "r1", "zona": "Salotto", "tipo": "home"}]
    assert piano["ac"] == [{"device_id": "ac-1", "tipo": "spegni"}]


def test_zona_in_pausa_non_riceve_comandi():
    stato = stato_vuoto()
    stato["stanze"]["r1"] = {"pausa_fino": ADESSO + 600}
    piano = pianifica([ZONA], contesto({"r1": stanza()}), stato)
    assert piano["zone"][0]["stato"] == "pausa"
    assert piano["netatmo"] == [] and piano["ac"] == []


def test_pausa_scaduta_riprende():
    stato = stato_vuoto()
    stato["stanze"]["r1"] = {"pausa_fino": ADESSO - 1}
    piano = pianifica([ZONA], contesto({"r1": stanza()}), stato)
    assert piano["zone"][0]["stato"] == "ac"
    assert piano["stato"]["stanze"]["r1"]["pausa_fino"] is None


def test_manuale_dell_utente_rispettato():
    piano = pianifica([ZONA], contesto({"r1": stanza(setpoint=22, modalita="manual", setpoint_fine=ADESSO + 900)}),
                      stato_vuoto())
    assert piano["zone"][0]["stato"] == "manuale"
    assert piano["netatmo"] == [] and piano["ac"] == []


def test_setpoint_cambiato_a_mano_durante_il_nostro_override():
    primo = pianifica([ZONA], contesto({"r1": stanza()}), stato_vuoto())
    reali = {"ac-1": ac(acceso=True, setpoint=20, modalita="heat")}
    piano = pianifica([ZONA], contesto({"r1": stanza(setpoint=23, modalita="manual")}, reali), primo["stato"])
    assert piano["zone"][0]["stato"] == "manuale"
    assert piano["netatmo"] == [] and piano["ac"] == []


def test_finestra_aperta_spegne_l_ac_e_restituisce_il_termostato():
    primo = pianifica([ZONA], contesto({"r1": stanza()}), stato_vuoto())
    reali = {"ac-1": ac(acceso=True, setpoint=20, modalita="heat")}
    piano = pianifica([ZONA], contesto({"r1": stanza(setpoint=7, modalita="manual", finestra_aperta=True)}, reali),
                      primo["stato"])
    assert piano["zone"][0]["stato"] == "finestra"
    assert piano["ac"] == [{"device_id": "ac-1", "tipo": "spegni"}]
    assert piano["netatmo"][0]["tipo"] == "home"


def test_raffrescamento_nessuna_azione():
    piano = pianifica([ZONA], contesto({"r1": stanza()}, casa={"temperature_control_mode": "cooling"}), stato_vuoto())
    assert piano["zone"][0]["stato"] == "raffrescamento"
    assert piano["netatmo"] == [] and piano["ac"] == []


def test_dati_mancanti_vanno_a_gas_con_la_diagnosi():
    piano = pianifica([ZONA], contesto({}, diagnosi={"r1": "Netatmo non collegato"}), stato_vuoto())
    assert piano["zone"][0]["stato"] == "errore"
    assert "Netatmo non collegato" in piano["zone"][0]["motivo"]


def test_target_dall_ultimo_setpoint_se_il_programma_non_e_noto():
    primo = pianifica([ZONA], contesto({"r1": stanza(target=None, setpoint=21)}), stato_vuoto())
    assert primo["zone"][0]["target"] == 21 and primo["ac"][0]["setpoint"] == 21
    reali = {"ac-1": ac(acceso=True, setpoint=21, modalita="heat")}
    secondo = pianifica([ZONA], contesto({"r1": stanza(target=None, setpoint=7, modalita="manual")}, reali),
                        primo["stato"])
    assert secondo["zone"][0]["stato"] == "ac" and secondo["zone"][0]["target"] == 21


def test_override_rinnovato_prima_della_scadenza():
    primo = pianifica([ZONA], contesto({"r1": stanza()}), stato_vuoto())
    reali = {"ac-1": ac(acceso=True, setpoint=20, modalita="heat")}
    piu_tardi = contesto({"r1": stanza(setpoint=7, modalita="manual")}, reali, adesso=ADESSO + 2000)
    piano = pianifica([ZONA], piu_tardi, primo["stato"])
    assert piano["netatmo"][0]["tipo"] == "manual"
    assert piano["netatmo"][0]["fine"] == ADESSO + 2000 + 3600


def test_ac_condiviso_resta_acceso_se_una_zona_lo_vuole():
    zone = [ZONA, {"nome": "Corridoio", "room_id": "r2", "ac_device_id": "ac-1"}]
    stanze = {"r1": stanza(t=19.0, target=20), "r2": stanza(t=21.0, target=21.5, setpoint=21.5)}
    piano = pianifica(zone, contesto(stanze), stato_vuoto())
    assert [z["stato"] for z in piano["zone"]] == ["ac", "gas"]
    assert piano["ac"] == [{"device_id": "ac-1", "tipo": "accendi", "setpoint": 20, "ventola": "auto", "opzionale": "off"}]


def test_ac_spento_a_mano_mette_in_pausa_le_sue_zone():
    primo = pianifica([ZONA], contesto({"r1": stanza()}), stato_vuoto())
    reali = {"ac-1": ac(acceso=False, setpoint=20, modalita="heat")}
    piano = pianifica([ZONA], contesto({"r1": stanza(setpoint=7, modalita="manual")}, reali), primo["stato"])
    assert piano["zone"][0]["stato"] == "pausa"
    assert piano["ac"] == []
    assert any("spento a mano" in dettaglio for _, _, dettaglio in piano["eventi"])


def test_di_notte_la_modalita_silenziosa():
    piano = pianifica([ZONA], contesto({"r1": stanza()}, ora=23), stato_vuoto())
    assert piano["ac"][0]["opzionale"] == "quiet"
    giorno = pianifica([ZONA], contesto({"r1": stanza()}, ora=14), stato_vuoto())
    assert giorno["ac"][0]["opzionale"] == "off"


def test_opzioni_non_inviate_se_l_ac_non_le_supporta():
    reali = {"ac-1": {"acceso": False, "modalita": "heat", "setpoint_riscaldamento": 20}}
    piano = pianifica([ZONA], contesto({"r1": stanza()}, reali, ora=23), stato_vuoto())
    assert piano["ac"][0]["ventola"] is None and piano["ac"][0]["opzionale"] is None


def test_simulazione_non_verifica_i_dispositivi_reali():
    # In simulazione i comandi non partono: la stanza reale resta in 'home'
    # ma lo stato simulato dice che c'e' il nostro override, e va bene cosi'.
    primo = pianifica([ZONA], contesto({"r1": stanza()}), stato_vuoto(), simulazione=True)
    secondo = pianifica([ZONA], contesto({"r1": stanza()}), primo["stato"], simulazione=True)
    assert secondo["zone"][0]["stato"] == "ac"
    assert secondo["netatmo"] == [] and secondo["ac"] == []


def test_lo_stato_in_ingresso_non_viene_modificato():
    stato = stato_vuoto()
    pianifica([ZONA], contesto({"r1": stanza()}), stato)
    assert stato == stato_vuoto()


def test_rilascio_restituisce_tutto():
    primo = pianifica([ZONA], contesto({"r1": stanza()}), stato_vuoto())
    piano = rilascio(primo["stato"])
    assert piano["netatmo"] == [{"room_id": "r1", "zona": "r1", "tipo": "home"}]
    assert piano["ac"] == [{"device_id": "ac-1", "tipo": "spegni"}]
    assert rilascio(piano["stato"])["netatmo"] == [] and rilascio(piano["stato"])["ac"] == []


def test_fascia_notte():
    assert in_fascia_notte(23, 22, 7) and in_fascia_notte(3, 22, 7)
    assert not in_fascia_notte(12, 22, 7)
    assert in_fascia_notte(14, 13, 15) and not in_fascia_notte(15, 13, 15)
    assert not in_fascia_notte(5, 7, 7)


def test_termostato_non_raggiungibile_con_il_motivo_di_netatmo():
    offline = {"temperatura_attuale": None, "setpoint": None, "target": 20.0, "modalita": None,
               "raggiungibile": False, "errore": "Termostato non raggiungibile da Netatmo (errore 6)"}
    piano = pianifica([ZONA], contesto({"r1": offline}), stato_vuoto())
    [z] = piano["zone"]
    assert z["stato"] == "errore" and "errore 6" in z["motivo"]
    assert piano["netatmo"] == [] and piano["ac"] == []
