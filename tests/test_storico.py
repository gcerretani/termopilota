# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date, datetime, timedelta

import pytest

from termopilota import storico
@pytest.fixture(autouse=True)
def db_temporaneo(tmp_path, monkeypatch):
    monkeypatch.setattr(storico, "DB_FILE", str(tmp_path / "storico.db"))
    storico.inizializza_db()


def campione(ora, temp=4.0, gas=0.105, ac=0.075, racc="ac"):
    return {
        "ora": ora,
        "temp_esterna": temp,
        "fonte_temp": "cfr",
        "cop": 3.5,
        "costo_gas_kwh": gas,
        "costo_ac_kwh": ac,
        "gas_totale_smc": 1.05,
        "luce_totale_kwh": 0.26,
        "raccomandazione": racc,
    }


def test_registra_e_leggi_oraria():
    storico.registra_campione(campione("2026-01-10T08:00"))
    punti = storico.leggi_campioni("2026-01-10", "2026-01-10", "oraria")
    assert len(punti) == 1
    assert punti[0]["costo_ac_kwh"] == 0.075


def test_registra_sovrascrive_stessa_ora():
    storico.registra_campione(campione("2026-01-10T08:00", temp=1.0))
    storico.registra_campione(campione("2026-01-10T08:00", temp=9.0))
    punti = storico.leggi_campioni("2026-01-10", "2026-01-10", "oraria")
    assert len(punti) == 1
    assert punti[0]["temp_esterna"] == 9.0


def ac_acceso(ora, quarti=(True, True, True, True), modalita="heat", ident="ac-1"):
    """Letture di un AC (senza contatore) nei quattro quarti d'ora di `ora`."""
    for i, acceso in enumerate(quarti):
        storico.registra_letture(f"{ora[:13]}:{15 * i:02d}", [{
            "tipo": "ac", "id": ident, "nome": "AC", "t_ambiente": 21.0, "umidita": 50, "setpoint": 21,
            "attivo": 1 if acceso else 0, "modalita": modalita, "energia_wh": None, "potenza_w": None, "extra": {}}])


def test_aggregazione_giornaliera():
    storico.registra_campione(campione("2026-01-10T08:00", temp=2.0, racc="ac"))
    storico.registra_campione(campione("2026-01-10T09:00", temp=4.0, racc="gas"))
    ac_acceso("2026-01-10T08:00")
    punti = storico.leggi_campioni("2026-01-10", "2026-01-10", "giornaliera", potenza_kw=4.0)
    assert len(punti) == 1
    p = punti[0]
    assert p["ore_gas"] == 1
    assert p["ore_ac"] == 1
    assert p["temp_media"] == 3.0
    # Solo l'ora con l'AC acceso risparmia: (0.105-0.075) * 4 kW = 0.12 €
    assert p["risparmio_eur"] == 0.12
    orari = storico.leggi_campioni("2026-01-10", "2026-01-10", "oraria", potenza_kw=4.0)
    assert [o["risparmio_eur"] for o in orari] == [0.12, 0.0]


def test_risparmio_solo_con_un_ac_acceso_in_riscaldamento():
    oggi = date.today().isoformat()
    for ora in ("08", "09", "10", "11"):
        storico.registra_campione(campione(f"{oggi}T{ora}:00", racc="ac"))
    ac_acceso(f"{oggi}T08:00")                                        # conta tutta
    ac_acceso(f"{oggi}T09:00", quarti=(True, False, False, False))    # un quarto
    ac_acceso(f"{oggi}T10:00", quarti=(False,) * 4)                   # consigliato l'AC, ma tutto spento
    ac_acceso(f"{oggi}T11:00", modalita="cool")                       # raffrescamento: non sostituisce la caldaia
    r = storico.calcola_risparmi(potenza_kw=4.0)
    assert r["oggi_eur"] == round(0.03 * 4.0 * 1.25, 2)
    assert r["ore_ac_stagione"] == 1.2      # 1,25 arrotondato
    assert r["fonte"] == "stimato" and r["oggi_principale_eur"] == r["oggi_eur"]


def test_risparmio_negativo_se_l_ac_scalda_quando_conviene_il_gas():
    oggi = date.today().isoformat()
    storico.registra_campione(campione(f"{oggi}T08:00", gas=0.08, ac=0.10, racc="gas"))
    ac_acceso(f"{oggi}T08:00")
    assert storico.calcola_risparmi(potenza_kw=4.0)["oggi_eur"] == -0.08


def test_risparmi_vuoti_senza_dati():
    oggi = date.today().isoformat()
    storico.registra_campione(campione(f"{oggi}T08:00", racc="ac"))   # consiglio senza nessuna lettura
    r = storico.calcola_risparmi(potenza_kw=4.0)
    assert r["oggi_eur"] == 0.0
    assert r["stagione_eur"] == 0.0
    assert r["ore_ac_stagione"] == 0


def test_inizio_stagione():
    assert storico._inizio_stagione(date(2026, 7, 18)) == date(2025, 10, 1)
    assert storico._inizio_stagione(date(2026, 11, 2)) == date(2026, 10, 1)


# ─── Letture dei dispositivi e consumo misurato ──────────────────────────────

def lettura_ac(energia_wh, attivo=1, ident="ac-1", nome="Salotto"):
    return {"tipo": "ac", "id": ident, "nome": nome, "t_ambiente": 21.0, "umidita": 50,
            "setpoint": 21, "attivo": attivo, "modalita": "heat", "energia_wh": energia_wh,
            "potenza_w": None, "extra": {"ventola": "auto"}}


def test_letture_grezze_e_orarie():
    storico.registra_letture("2026-01-10T08:00", [lettura_ac(1000)])
    storico.registra_letture("2026-01-10T08:15", [lettura_ac(1300)])
    storico.registra_letture("2026-01-10T09:00", [lettura_ac(1500, attivo=0)])
    grezze = storico.leggi_letture("ac", "ac-1", "2026-01-10", "2026-01-10")
    assert [p["periodo"] for p in grezze] == ["2026-01-10T08:00", "2026-01-10T08:15", "2026-01-10T09:00"]
    assert grezze[0]["extra"] == {"ventola": "auto"}
    orarie = storico.leggi_letture("ac", "ac-1", "2026-01-10", "2026-01-10", "oraria")
    assert [(p["periodo"], p["attivo_pct"], p["kwh"]) for p in orarie] == [
        ("2026-01-10T08", 100, 0.3), ("2026-01-10T09", 0, 0.2)]


def test_energia_per_periodo_con_reset_e_lettura_del_giorno_prima():
    storico.registra_letture("2026-01-09T23:45", [lettura_ac(10_000)])
    storico.registra_letture("2026-01-10T00:15", [lettura_ac(10_400)])
    storico.registra_letture("2026-01-10T01:00", [lettura_ac(200)])      # contatore azzerato
    storico.registra_letture("2026-01-10T02:00", [lettura_ac(700)])
    storico.registra_letture("2026-01-10T02:00", [lettura_ac(5_000, ident="ac-2", nome="Corridoio")])
    giorno = storico.energia_ac("2026-01-10", "2026-01-10", "giornaliera")
    assert giorno == [{"periodo": "2026-01-10", "id": "ac-1", "nome": "Salotto", "kwh": 0.9}]
    orario = storico.energia_ac("2026-01-10", "2026-01-10", "oraria", "ac-1")
    assert [(e["periodo"], e["kwh"]) for e in orario] == [("2026-01-10T00", 0.4), ("2026-01-10T02", 0.5)]


def test_consumi_misurati_e_risparmio_reale():
    storico.registra_campione(campione("2026-01-10T08:00", gas=0.105, ac=0.075))   # cop 3.5
    storico.registra_letture("2026-01-10T07:45", [lettura_ac(1000)])
    storico.registra_letture("2026-01-10T08:30", [lettura_ac(2000)])              # 1 kWh alle 8
    storico.registra_letture("2026-01-10T10:00", [lettura_ac(2500)])              # 0,5 kWh senza prezzi
    giorno = storico.consumi_misurati("2026-01-10", "2026-01-10")["2026-01-10"]
    assert giorno["kwh"] == 1.5
    # 1 kWh elettrico × COP 3,5 = 3,5 kWh termici: costo 3,5 × 0,075, risparmio 3,5 × 0,03
    assert giorno["costo_eur"] == pytest.approx(0.2625, abs=0.001)
    assert giorno["risparmio_eur"] == pytest.approx(0.105, abs=0.001)
    punto = storico.leggi_campioni("2026-01-10", "2026-01-10", "giornaliera")[0]
    assert punto["energia_ac_kwh"] == 1.5 and punto["risparmio_reale_eur"] == pytest.approx(0.1, abs=0.01)


def test_calcola_risparmi_con_misure():
    oggi = date.today().isoformat()
    storico.registra_campione(campione(f"{oggi}T08:00"))
    storico.registra_letture(f"{oggi}T07:45", [lettura_ac(1000)])
    storico.registra_letture(f"{oggi}T08:30", [lettura_ac(3000)])
    r = storico.calcola_risparmi(4.0)
    assert r["misure_disponibili"] is True
    assert r["kwh_ac_oggi"] == 2.0 and r["kwh_ac_stagione"] == 2.0
    assert r["oggi_reale_eur"] == pytest.approx(0.21, abs=0.001)
    assert r["fonte"] == "misurato" and r["oggi_principale_eur"] == r["oggi_reale_eur"]


def test_consumo_in_raffrescamento_non_e_risparmio():
    storico.registra_campione(campione("2026-01-10T08:00"))
    fresco = lambda wh, attivo=1: {**lettura_ac(wh, attivo), "modalita": "cool"}
    storico.registra_letture("2026-01-10T07:45", [fresco(1000)])
    storico.registra_letture("2026-01-10T08:30", [fresco(2000)])
    assert storico.consumi_misurati("2026-01-10", "2026-01-10") == {}
    # ...ma resta nel consumo dei condizionatori
    assert storico.energia_ac("2026-01-10", "2026-01-10")[0]["kwh"] == 1.0


def test_calcola_risparmi_senza_misure():
    r = storico.calcola_risparmi(4.0)
    assert r["misure_disponibili"] is False and r["stagione_reale_eur"] is None


def test_potenza_media_ac_solo_con_ac_acceso():
    inizio = datetime.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=5)
    energia = 0
    for i in range(13):   # 3 ore accese a 15 minuti (0,3 kWh ogni quarto d'ora = 1,2 kW)
        ts = (inizio + timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M")
        storico.registra_letture(ts, [lettura_ac(energia)])
        energia += 300
    spento = (inizio + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M")
    storico.registra_letture(spento, [lettura_ac(energia + 50, attivo=0)])
    assert storico.potenza_media_ac() == 1.2


def test_potenza_media_ac_none_con_pochi_dati():
    assert storico.potenza_media_ac() is None
