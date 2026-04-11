# TermoPilota — Guida sviluppo

## Stack

- **Backend**: Flask (Python 3.12), gunicorn in produzione
- **Frontend**: Bootstrap 5.3.2, Chart.js 4.4.2, vanilla JS (no bundler)
- **Database**: SQLite per utenti (`data/users.db`), JSON per configurazione (`config.json`)
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
```

## Porta

L'app gira su **porta 5001** (la 5000 e' occupata da AirPlay su macOS).

## Struttura

- `app.py` — Routes Flask, Blueprint admin (`/admin/*`), calcolo raccomandazioni
- `auth.py` — Autenticazione, gestione utenti SQLite, Flask-Login setup
- `automazione.py` — Thread daemon, ciclo di controllo zone (ogni 15 min default)
- `prezzi.py` — Fetch TTF (Yahoo Finance) e PUN (ENTSO-E), cache in memoria
- `providers/` — Architettura modulare per dispositivi
  - `__init__.py` — ABC `ThermostatProvider`, `HeatPumpProvider`, registry
  - `netatmo.py` — Client Netatmo OAuth2 per termostati BTicino Smarther
  - `smartthings.py` — Client SmartThings REST per AC Samsung
- `bticino.py`, `samsung.py` — Shim di compatibilita', importano da providers/

## Convenzioni

- **Lingua**: UI e commenti in italiano, identificatori codice in italiano (snake_case)
- **Config**: `config.json` e' gitignored, contiene credenziali. `config.example.json` e' il template
- **Provider pattern**: per aggiungere un nuovo tipo di termostato/pompa di calore, creare un modulo in `providers/` che implementi l'ABC e si registri nel registry
- **COP_TABELLA**: duplicata in `app.py` e `automazione.py` — aggiornare entrambi se cambia

## File sensibili (mai committare)

- `config.json` — contiene token OAuth, client secret, PAT SmartThings
- `data/users.db` — hash password utenti
- `*.log`

## Route principali

- `GET /` — Dashboard (richiede login)
- `GET /login`, `POST /login`, `GET /logout` — Autenticazione
- `GET /admin/` — Impostazioni (admin)
- `GET /admin/credentials` — Credenziali API
- `GET /admin/zones` — Editor zone
- `GET /admin/users` — Gestione utenti
- `GET /api/automazione/oauth-callback` — Callback OAuth Netatmo (pubblico)
- API JSON: `/api/prezzi`, `/api/dati`, `/api/temp-cfr`, `/api/config`, `/api/automazione`, `/api/dispositivi`
