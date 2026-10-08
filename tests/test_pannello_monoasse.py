# SPDX-FileCopyrightText: 2026 Giovanni Cerretani
# SPDX-License-Identifier: GPL-3.0-or-later
"""Modello monoasse del pannello: geometria solare, inseguitore, luce sul piano,
temperatura, interpolazione e calibrazione."""

import math
from datetime import datetime, timezone

import pytest

from termopilota import pannello
from termopilota.pannello import Punto

LAT, LON = 38.5, -1.2


def _ts(anno, mese, giorno, ore=12, minuti=0):
    return datetime(anno, mese, giorno, ore, minuti, tzinfo=timezone.utc).timestamp()


def _zenit_gradi(ts, lat=LAT, lon=LON):
    _e, _n, alto, _g = pannello.vettore_solare(ts, lat, lon)
    return math.degrees(math.acos(alto))


# ─── Posizione del sole ──────────────────────────────────────────────────────

def test_mezzogiorno_solare_al_solstizio_d_estate():
    # mezzogiorno solare a lon -1,2: 12:00 UTC + 4,8 minuti (l'equazione del tempo e' ~-1,5 min)
    zenit = _zenit_gradi(_ts(2026, 6, 21, 12, 3))
    assert zenit == pytest.approx(LAT - 23.44, abs=0.7)


def test_mezzogiorno_solare_al_solstizio_d_inverno():
    zenit = _zenit_gradi(_ts(2026, 12, 21, 12, 7))
    assert zenit == pytest.approx(LAT + 23.44, abs=0.7)


def test_di_notte_il_sole_e_sotto_l_orizzonte():
    assert pannello.vettore_solare(_ts(2026, 10, 8, 1), LAT, LON)[2] < 0


def test_il_sole_sorge_a_est_e_tramonta_a_ovest():
    assert pannello.vettore_solare(_ts(2026, 6, 21, 6), LAT, LON)[0] > 0.5
    assert pannello.vettore_solare(_ts(2026, 6, 21, 18), LAT, LON)[0] < -0.5


# ─── Inseguitore ─────────────────────────────────────────────────────────────

def test_a_mezzogiorno_l_inseguitore_e_orizzontale():
    assert pannello.angolo_inseguitore(0.0, 1.0) == 0.0


def test_l_inseguitore_segue_il_sole_senza_backtracking():
    angolo = math.degrees(pannello.angolo_inseguitore(math.sin(math.radians(40)), math.cos(math.radians(40))))
    assert angolo == pytest.approx(40.0, abs=0.01)


def test_l_inseguitore_e_simmetrico():
    a = pannello.angolo_inseguitore(0.5, 0.8)
    b = pannello.angolo_inseguitore(-0.5, 0.8)
    assert a == pytest.approx(-b)


def test_la_corsa_e_limitata():
    # sole a 65° dalla verticale, file molto distanziate (niente backtracking): oltre i 60° meccanici
    angolo = math.degrees(pannello.angolo_inseguitore(math.sin(math.radians(65)), math.cos(math.radians(65)), gcr=0.2))
    assert angolo == pytest.approx(60.0, abs=0.01)


def test_il_backtracking_riduce_l_inclinazione_col_sole_basso():
    e, u = math.sin(math.radians(80)), math.cos(math.radians(80))
    con = math.degrees(pannello.angolo_inseguitore(e, u))
    assert 0 < con < 60  # senza backtracking sarebbe 80 -> limitato a 60


def test_sotto_l_orizzonte_l_inseguitore_resta_fermo():
    assert pannello.angolo_inseguitore(0.3, -0.1) == 0.0


# ─── Luce sul piano del pannello ─────────────────────────────────────────────

def test_a_mezzogiorno_con_sola_luce_diretta_la_poa_e_la_dni():
    ts = _ts(2026, 6, 21, 12, 3)
    zenit = math.radians(_zenit_gradi(ts))
    dni = 800.0
    ghi = dni * math.cos(zenit)
    poa = pannello.poa_monoasse(ts, LAT, LON, ghi, dni, 0.0)
    # inseguitore piatto e quasi perpendicolare al sole: POA ~ GHI + componente riflessa nulla
    assert poa == pytest.approx(ghi, rel=0.03)


def test_di_mattina_l_inseguitore_riceve_molto_piu_dell_orizzontale():
    ts = _ts(2026, 6, 21, 7, 30)
    poa = pannello.poa_monoasse(ts, LAT, LON, ghi=467.0, dni=750.0, dhi=117.0)
    assert poa > 1.5 * 467.0


def test_di_notte_la_poa_e_zero():
    assert pannello.poa_monoasse(_ts(2026, 10, 8, 1), LAT, LON, 0.0, 0.0, 0.0) == 0.0


def test_senza_dni_e_dhi_si_ricavano_da_ghi():
    poa = pannello.poa_monoasse(_ts(2026, 6, 21, 9), LAT, LON, ghi=800.0)
    assert 800.0 < poa < 1300.0


def test_la_poa_non_e_mai_negativa():
    for ora in range(0, 24):
        assert pannello.poa_monoasse(_ts(2026, 10, 8, ora), LAT, LON, 100.0, 50.0, 50.0) >= 0.0


# ─── Temperatura ─────────────────────────────────────────────────────────────

def test_a_25_gradi_di_cella_nessuna_perdita():
    assert pannello.fattore_temperatura(0.0, 25.0) == pytest.approx(1.0)


def test_il_caldo_riduce_la_potenza():
    assert pannello.fattore_temperatura(1000.0, 35.0) < pannello.fattore_temperatura(1000.0, 5.0)


def test_il_freddo_con_poca_luce_la_aumenta():
    assert pannello.fattore_temperatura(300.0, 5.0) > 1.0


# ─── Serie e interpolazione ──────────────────────────────────────────────────

def _punti(kw):
    return [Punto(ora=f"2026-10-08T{10 + i}:00", ts=_ts(2026, 10, 8, 10 + i), ghi=0.0) for i, _ in enumerate(kw)]


def test_interpolazione_fra_punti_medi():
    punti = _punti([1.0, 3.0])
    primo = punti[0].ts
    assert pannello._valore_adesso(punti, [1.0, 3.0], primo + 1800) == pytest.approx(1.0)   # punto medio 1
    assert pannello._valore_adesso(punti, [1.0, 3.0], primo + 3600) == pytest.approx(2.0)   # meta' fra i due
    assert pannello._valore_adesso(punti, [1.0, 3.0], primo + 3600 + 1800) == pytest.approx(3.0)


def test_adesso_fuori_dalla_serie_vale_zero():
    punti = _punti([1.0, 3.0])
    assert pannello._valore_adesso(punti, [1.0, 3.0], punti[0].ts - 7200) == 0.0


def test_un_solo_punto_vale_se_stesso():
    punti = _punti([2.0])
    assert pannello._valore_adesso(punti, [2.0], punti[0].ts + 900) == pytest.approx(2.0)


def test_normalizza_accetta_la_vecchia_forma():
    punti = pannello._normalizza([("2099-01-01T12:00", 800.0)])
    assert punti[0].ghi == 800.0 and punti[0].dni is None and punti[0].ora == "2099-01-01T12:00"


# ─── Modelli e fattori ───────────────────────────────────────────────────────

def test_il_modello_predefinito_e_monoasse():
    assert pannello.modello_attivo({}) == "monoasse"
    assert pannello.modello_attivo({"pannello_modello": "boh"}) == "monoasse"
    assert pannello.modello_attivo({"pannello_modello": "orizzontale"}) == "orizzontale"


def test_il_fattore_orizzontale_non_vale_per_il_monoasse():
    cfg = {"pannello_fattore": 1.747}
    assert pannello.fattore_configurato(cfg, "orizzontale") == 1.747
    assert pannello.fattore_configurato(cfg, "monoasse") == pannello.FATTORE_MONOASSE_PREDEFINITO


def test_produzione_con_modello_monoasse(monkeypatch):
    ora = datetime.now().replace(minute=0, second=0, microsecond=0)
    punto = Punto(ora=ora.strftime("%Y-%m-%dT%H:00"), ts=ora.timestamp(), ghi=500.0, dni=600.0, dhi=100.0, temp=15.0)
    monkeypatch.setattr(pannello, "_scarica_irraggiamento", lambda lat, lon: [punto])
    monkeypatch.setattr(pannello, "poa_monoasse", lambda *a, **k: 700.0)
    pannello._cache.update(chiave=None, dati=None, timestamp=0.0)
    stima = pannello.produzione_pannello({"pannello_modello": "monoasse", "pannello_potenza_kw": 1.0,
                                          "pannello_fattore_monoasse": 0.8})
    atteso = 1.0 * 0.7 * pannello.fattore_temperatura(700.0, 15.0) * 0.8
    assert stima["modello"] == "monoasse"
    assert stima["serie"][0]["kw"] == pytest.approx(atteso, abs=0.001)
    assert stima["serie"][0]["poa_wm2"] == 700.0
    assert stima["adesso_kw"] == pytest.approx(atteso, abs=0.001)


def test_produzione_con_modello_orizzontale(monkeypatch):
    ora = datetime.now().replace(minute=0, second=0, microsecond=0)
    punto = Punto(ora=ora.strftime("%Y-%m-%dT%H:00"), ts=ora.timestamp(), ghi=800.0)
    monkeypatch.setattr(pannello, "_scarica_irraggiamento", lambda lat, lon: [punto])
    pannello._cache.update(chiave=None, dati=None, timestamp=0.0)
    stima = pannello.produzione_pannello({"pannello_modello": "orizzontale", "pannello_potenza_kw": 1.0,
                                          "pannello_fattore": 0.5})
    assert stima["serie"][0]["kw"] == pytest.approx(0.4)


# ─── Calibrazione ────────────────────────────────────────────────────────────

def test_calibra_monoasse_riporta_la_stima_alla_lettura():
    stima = {"modello": "monoasse", "poa_wm2": 500.0, "irraggiamento_wm2": 300.0,
             "adesso_kw": 0.35, "fattore": 0.85}
    # la stima con fattore 0,85 e' 0,35 kW: per leggere 0,40 serve 0,85 × 0,40/0,35
    assert pannello.calibra(stima, 0.40) == pytest.approx(0.971, abs=0.001)


def test_calibra_orizzontale_usa_l_irraggiamento_orizzontale():
    stima = {"modello": "orizzontale", "poa_wm2": 900.0, "irraggiamento_wm2": 100.0,
             "adesso_kw": 0.09, "fattore": 0.9}
    assert pannello.calibra(stima, 0.2) is None  # 100 W/m2 sono pochi, anche se la POA e' alta


def test_calibra_rifiuta_poca_luce_e_stima_nulla():
    base = {"modello": "monoasse", "poa_wm2": 100.0, "irraggiamento_wm2": 100.0, "adesso_kw": 0.1, "fattore": 0.85}
    assert pannello.calibra(base, 0.3) is None
    assert pannello.calibra({**base, "poa_wm2": 600.0, "adesso_kw": 0.0}, 0.3) is None


def test_avviso_per_fattori_fuori_range_nel_monoasse():
    assert pannello.avviso_calibrazione("monoasse", 1.747) is not None
    assert pannello.avviso_calibrazione("monoasse", 0.4) is not None
    assert pannello.avviso_calibrazione("monoasse", 0.85) is None
    assert pannello.avviso_calibrazione("orizzontale", 1.747) is None


# ─── API ─────────────────────────────────────────────────────────────────────

def test_api_config_salva_modello_e_fattori(admin_client):
    r = admin_client.post("/api/config", json={"pannello_modello": "orizzontale",
                                               "pannello_fattore": 1.5, "pannello_fattore_monoasse": 0.9})
    assert r.status_code == 200
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["pannello_modello"] == "orizzontale"
    assert cfg["pannello_fattore"] == 1.5 and cfg["pannello_fattore_monoasse"] == 0.9


def test_api_config_ignora_un_modello_sconosciuto(admin_client):
    admin_client.post("/api/config", json={"pannello_modello": "boh"})
    assert admin_client.get("/api/config").get_json()["pannello_modello"] == "monoasse"


def test_api_calibra_monoasse_non_tocca_il_fattore_orizzontale(admin_client, monkeypatch):
    ora = datetime.now().strftime("%Y-%m-%dT%H:00")
    monkeypatch.setattr(pannello, "_scarica_irraggiamento", lambda lat, lon: [(ora, 800.0)])
    monkeypatch.setattr(pannello, "poa_monoasse", lambda *a, **k: 800.0)
    admin_client.post("/api/config", json={"pannello_fattore": 1.747})
    r = admin_client.post("/api/pannello/calibra", json={"produzione_kw": 0.5})
    assert r.status_code == 200
    dati = r.get_json()
    assert dati["modello"] == "monoasse" and dati["avviso"] is None
    # 0,5 = 0,9 kWp × 0,8 × perdita termica (cella a 45 °C) × fattore
    atteso = 0.5 / (0.9 * 0.8 * pannello.fattore_temperatura(800.0, None))
    assert dati["pannello_fattore"] == pytest.approx(atteso, abs=0.005)
    cfg = admin_client.get("/api/config").get_json()
    assert cfg["pannello_fattore"] == 1.747
    assert cfg["pannello_fattore_monoasse"] == dati["pannello_fattore"]


def test_api_calibra_avvisa_se_il_fattore_e_insolito(admin_client, monkeypatch):
    ora = datetime.now().strftime("%Y-%m-%dT%H:00")
    monkeypatch.setattr(pannello, "_scarica_irraggiamento", lambda lat, lon: [(ora, 800.0)])
    monkeypatch.setattr(pannello, "poa_monoasse", lambda *a, **k: 800.0)
    r = admin_client.post("/api/pannello/calibra", json={"produzione_kw": 1.4})
    assert r.status_code == 200
    assert "insolito" in r.get_json()["avviso"]
