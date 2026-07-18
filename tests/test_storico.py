from datetime import date, datetime, timedelta

import pytest

import storico


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


def test_aggregazione_giornaliera():
    storico.registra_campione(campione("2026-01-10T08:00", temp=2.0, racc="ac"))
    storico.registra_campione(campione("2026-01-10T09:00", temp=4.0, racc="gas"))
    punti = storico.leggi_campioni("2026-01-10", "2026-01-10", "giornaliera", potenza_kw=4.0)
    assert len(punti) == 1
    p = punti[0]
    assert p["ore_gas"] == 1
    assert p["ore_ac"] == 1
    assert p["temp_media"] == 3.0
    # Solo l'ora in AC risparmia: (0.105-0.075) * 4 kW = 0.12 €
    assert p["risparmio_eur"] == 0.12


def test_risparmi_contano_solo_ore_ac_con_riscaldamento():
    oggi = date.today().isoformat()
    storico.registra_campione(campione(f"{oggi}T08:00", temp=4.0, racc="ac"))       # conta
    storico.registra_campione(campione(f"{oggi}T09:00", temp=4.0, racc="gas"))      # no: gas
    storico.registra_campione(campione(f"{oggi}T10:00", temp=22.0, racc="ac"))      # no: caldo, riscaldamento spento
    r = storico.calcola_risparmi(potenza_kw=4.0)
    assert r["oggi_eur"] == round((0.105 - 0.075) * 4.0, 2)
    assert r["oggi_eur"] == r["stagione_eur"]


def test_risparmi_vuoti_senza_dati():
    r = storico.calcola_risparmi(potenza_kw=4.0)
    assert r["oggi_eur"] == 0.0
    assert r["stagione_eur"] == 0.0
    assert r["ore_ac_stagione"] == 0


def test_inizio_stagione():
    assert storico._inizio_stagione(date(2026, 7, 18)) == date(2025, 10, 1)
    assert storico._inizio_stagione(date(2026, 11, 2)) == date(2026, 10, 1)
