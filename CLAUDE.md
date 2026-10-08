# TermoPilota — Guida sviluppo

## Stack

- **Backend**: Flask (Python 3.12), gunicorn in produzione
- **Frontend**: Bootstrap 5.3.8, Chart.js 4.5.1, vanilla JS (no bundler) — nessuna CDN: le librerie sono dichiarate in `package.json`/`package-lock.json` (Dependabot le aggiorna) e copiate in `src/termopilota/static/vendor/` (non committata) con `npm run vendor`; PWA installabile (manifest + service worker)
- **Database**: SQLite per utenti (`data/users.db`) e storico (`data/storico.db`), JSON per configurazione (`data/config.json`)
- **Auth**: Flask-Login con sessioni, admin iniziale da variabili d'ambiente

## Comandi

```bash
# Sviluppo locale
source venv/bin/activate
ADMIN_USER=admin ADMIN_PASSWORD=test python -m termopilota

# Docker
ADMIN_PASSWORD=test docker compose up --build

# Installare dipendenze (app in modalita' sviluppo + pytest, poi le librerie front-end; richiede Node 22)
pip install -e ".[dev]"
npm ci && npm run vendor

# Test
python -m pytest tests/

# Rigenerare le icone PWA (solo se cambia il design)
pip install cairosvg && python scripts/genera_icone.py
```

## Test e CI

- `src/termopilota/static/vendor/` non e' nel repository: senza `npm ci && npm run vendor` i test falliscono con questa istruzione (`tests/test_vendor.py` controlla anche che le versioni coincidano con `package-lock.json`)
- `python -m pytest tests/` — i test usano una cartella dati temporanea (`TERMOPILOTA_DATA_DIR`) e non avviano thread in background né chiamate di rete (`TERMOPILOTA_SENZA_SERVIZI=1`); impostate da `tests/conftest.py`, non servono a mano
- `.github/workflows/tests.yml` — su ogni pull request: pytest + build e smoke test del container. Richiamato da `docker.yml`: un push su `main` pubblica l'immagine solo se i test passano
- Il branch `main` e' protetto: serve una pull request e devono passare `pytest` e `build e avvio del container`

## Versioni e release

- Versionamento Semantico. La versione e' in `src/termopilota/versione.py` (`VERSIONE`; `pyproject.toml` la legge da li'), `package.json` e `package-lock.json` (`npm version X.Y.Z --no-git-tag-version`) e nell'ultima voce di `CHANGELOG.md`; `tests/test_versione.py` fallisce se non coincidono
- Ogni modifica visibile all'utente va annotata in `CHANGELOG.md` sotto `## [Non rilasciato]` (formato Keep a Changelog, in italiano)
- Per rilasciare: spostare le voci sotto la nuova versione con la data, aggiornare i numeri di versione e i link in fondo al changelog, merge su `main`, poi tag `vX.Y.Z` sul commit di `main` e release su GitHub. Il push del tag fa pubblicare all'immagine anche i tag `X.Y.Z` e `X.Y` (`docker.yml`, solo se i test passano)

## Porta

L'app gira su **porta 5001** (la 5000 e' occupata da AirPlay su macOS).

## Struttura

Pacchetto `src/termopilota/` (layout `src`: si installa con `pip install -e .`, gli import sono `from termopilota.x import ...`):

- `app.py` — Routes Flask, Blueprint admin (`/admin/*`), avvio servizi in background; `__main__.py` — avvio locale (`python -m termopilota`)
- `percorsi.py` — `DATA_DIR` e `CONFIG_FILE` (`TERMOPILOTA_DATA_DIR` oppure `data/` nella cartella corrente): unica definizione, importata dagli altri moduli
- `costanti.py` — COP_TABELLA, `interpola_cop`, KWH_PER_SMC (fonte unica, condivisa)
- `raccomandazioni.py` — Motore `calcola_raccomandazioni` (importabile senza avviare Flask, testabile)
- `storico.py` — Persistenza SQLite (`data/storico.db`): campionatore orario in thread daemon, query per grafici e stima risparmi
- `auth.py`, `auth_google.py` — Autenticazione, gestione utenti SQLite, Flask-Login, accesso con Google
- `automazione.py` — Thread daemon, ciclo di controllo zone (ogni 15 min default)
- `prezzi.py` — Prezzi gas/luce: tariffa variabile (TTF Yahoo Finance, PUN ENTSO-E, cache in memoria) oppure fissa (prezzo bloccato da config), per gas e luce indipendentemente
- `pannello.py` — Stima produzione del pannello adottato Plenitude (Murcia) da irraggiamento Open-Meteo, con calibrazione del fattore di resa; la produzione di ogni quarto d'ora compensa il consumo di casa (`copertura_pannello` in `raccomandazioni.py`) e abbassa il costo marginale della pompa di calore
- `versione.py` — `VERSIONE`, unica fonte della versione (la legge anche `pyproject.toml`)
- `providers/` — Architettura modulare per dispositivi
  - `__init__.py` — ABC `ThermostatProvider`, `HeatPumpProvider`, registry
  - `netatmo.py` — Client Netatmo OAuth2 per termostati BTicino Smarther
  - `smartthings.py` — Client SmartThings OAuth2 (consigliato) + PAT fallback per AC Samsung
- `templates/`, `static/` — HTML (Jinja) e asset; `static/vendor/` e' generato, non e' nel repository

Nella radice:

- `pyproject.toml` — metadati, dipendenze Python (`dependencies`) e di sviluppo (extra `dev`), configurazione pytest. Dependabot aggiorna i minimi di versione
- `tests/` — pytest: unit test (motore, COP, storico, tariffe, pannello) e test di integrazione delle route Flask (`test_app.py`); `conftest.py` isola dati e rete
- `package.json`, `package-lock.json` — Bootstrap, Bootstrap Icons, Chart.js a versioni esatte; `scripts/vendor.js` le copia in `src/termopilota/static/vendor/`. Per aggiornarle: merge della PR di Dependabot (la CI rigenera i file da sola); a mano: `npm install --save-exact <pacchetto>@<versione>`
- `Dockerfile` — multi-stage: una fase Node esegue `npm ci` e `npm run vendor`, la fase Python installa il pacchetto con `pip install .`; `.dockerignore` tiene fuori dati locali e segreti. Gunicorn con un solo worker (`-w 1 --threads 4`): i servizi in background partono all'import e non vanno duplicati
- `scripts/` — `vendor.js`, `genera_icone.py`

## Convenzioni

- **Lingua**: UI e commenti in italiano, identificatori codice in italiano (snake_case)
- **Config**: `data/config.json` e' gitignored (tramite `data/`), contiene credenziali. `config.example.json` e' il template
- **Provider pattern**: per aggiungere un nuovo tipo di termostato/pompa di calore, creare un modulo in `src/termopilota/providers/` che implementi l'ABC e si registri nel registry
- **Licenza**: GPL v3 o successiva (`LICENSE`). Ogni nuovo `.py`, `.js` o `.css` proprio deve iniziare con `SPDX-License-Identifier: GPL-3.0-or-later` (dopo l'eventuale shebang), altrimenti `tests/test_licenza.py` fallisce. Le librerie front-end sono MIT e vanno elencate in `THIRD_PARTY_NOTICES.txt`
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
