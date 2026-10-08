# TermoPilota — Guida sviluppo

## Stack

- **Backend**: Flask (Python 3.12), gunicorn in produzione
- **Frontend**: Bootstrap 5.3.2, Chart.js 4.4.2, vanilla JS (no bundler) — asset vendorizzati in `static/vendor/` (nessuna CDN), PWA installabile (manifest + service worker)
- **Database**: SQLite per utenti (`data/users.db`) e storico (`data/storico.db`), JSON per configurazione (`data/config.json`)
- **Auth**: Flask-Login con sessioni, admin iniziale da variabili d'ambiente

## Comandi

```bash
# Sviluppo locale
source venv/bin/activate
ADMIN_USER=admin ADMIN_PASSWORD=test python app.py

# Docker
ADMIN_PASSWORD=test docker compose up --build

# Installare dipendenze
pip install -r requirements.txt

# Test
pip install -r requirements-dev.txt
python -m pytest tests/

# Rigenerare le icone PWA (solo se cambia il design)
pip install cairosvg && python scripts/genera_icone.py
```

## Test e CI

- `python -m pytest tests/` — i test usano una cartella dati temporanea (`TERMOPILOTA_DATA_DIR`) e non avviano thread in background né chiamate di rete (`TERMOPILOTA_SENZA_SERVIZI=1`); impostate da `tests/conftest.py`, non servono a mano
- `.github/workflows/tests.yml` — su ogni pull request: pytest + build e smoke test del container. Richiamato da `docker.yml`: un push su `main` pubblica l'immagine solo se i test passano
- Per bloccare il merge in caso di test rossi, impostare `pytest` e `build e avvio del container` come controlli obbligatori nelle regole di protezione del branch `main` (Settings → Branches)

## Porta

L'app gira su **porta 5001** (la 5000 e' occupata da AirPlay su macOS).

## Struttura

- `app.py` — Routes Flask, Blueprint admin (`/admin/*`), avvio servizi in background
- `costanti.py` — COP_TABELLA, `interpola_cop`, KWH_PER_SMC (fonte unica, condivisa)
- `raccomandazioni.py` — Motore `calcola_raccomandazioni` (importabile senza avviare Flask, testabile)
- `storico.py` — Persistenza SQLite (`data/storico.db`): campionatore orario in thread daemon, query per grafici e stima risparmi
- `auth.py` — Autenticazione, gestione utenti SQLite, Flask-Login setup
- `automazione.py` — Thread daemon, ciclo di controllo zone (ogni 15 min default)
- `prezzi.py` — Prezzi gas/luce: tariffa variabile (TTF Yahoo Finance, PUN ENTSO-E, cache in memoria) oppure fissa (prezzo bloccato da config), per gas e luce indipendentemente
- `pannello.py` — Stima produzione del pannello adottato Plenitude (Murcia) da irraggiamento Open-Meteo, con calibrazione del fattore di resa; la produzione di ogni quarto d'ora compensa il consumo di casa (`copertura_pannello` in `raccomandazioni.py`) e abbassa il costo marginale della pompa di calore
- `tests/` — pytest: unit test (motore, COP, storico, tariffe, pannello) e test di integrazione delle route Flask (`test_app.py`); `conftest.py` isola dati e rete
- `providers/` — Architettura modulare per dispositivi
  - `__init__.py` — ABC `ThermostatProvider`, `HeatPumpProvider`, registry
  - `netatmo.py` — Client Netatmo OAuth2 per termostati BTicino Smarther
  - `smartthings.py` — Client SmartThings OAuth2 (consigliato) + PAT fallback per AC Samsung
- `bticino.py`, `samsung.py` — Shim di compatibilita', importano da providers/

## Convenzioni

- **Lingua**: UI e commenti in italiano, identificatori codice in italiano (snake_case)
- **Config**: `data/config.json` e' gitignored (tramite `data/`), contiene credenziali. `config.example.json` e' il template
- **Provider pattern**: per aggiungere un nuovo tipo di termostato/pompa di calore, creare un modulo in `providers/` che implementi l'ABC e si registri nel registry
- **COP_TABELLA**: definita una sola volta in `costanti.py`, importata da `app.py` e `automazione.py`

## File sensibili (mai committare)

- `data/config.json` — contiene token OAuth, client secret, PAT SmartThings
- `data/users.db` — hash password utenti
- `*.log`

## Route principali

- `GET /` — Dashboard (richiede login, refresh live ogni 5 min via `/api/dashboard`)
- `GET /storico` — Grafici storici e contatore risparmi (richiede login)
- `GET /sw.js` — Service worker PWA (pubblico, servito dalla root per lo scope)
- `GET /login`, `POST /login`, `GET /logout` — Autenticazione
- `GET /admin/` — Impostazioni (admin)
- `GET /admin/credentials` — Credenziali API
- `GET /admin/zones` — Editor zone
- `GET /admin/users` — Gestione utenti
- `GET /api/automazione/oauth-callback` — Callback OAuth Netatmo (pubblico)
- `GET /api/automazione/smartthings-callback` — Callback OAuth SmartThings (pubblico)
- API JSON: `/api/prezzi`, `/api/dati`, `/api/temp-cfr`, `/api/config`, `/api/automazione`, `/api/dispositivi`, `/api/dashboard`, `/api/pannello`, `POST /api/pannello/calibra`, `/api/storico?da=&a=&risoluzione=oraria|giornaliera`, `/api/risparmi`
