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
- `percorsi.py` — `DATA_DIR`, `CONFIG_FILE` e `STATO_AUTOMAZIONE_FILE` (`TERMOPILOTA_DATA_DIR` oppure `data/` nella cartella corrente): unica definizione, importata dagli altri moduli
- `costanti.py` — COP_TABELLA, `interpola_cop`, KWH_PER_SMC (fonte unica, condivisa)
- `raccomandazioni.py` — Motore `calcola_raccomandazioni` (importabile senza avviare Flask, testabile)
- `storico.py` — Persistenza SQLite (`data/storico.db`): campionatore orario in thread daemon, query per grafici e stima risparmi; tabella `letture_dispositivi` (ogni 15 min) con il contatore di energia degli AC: `energia_ac`, `consumi_misurati` (risparmio misurato), `potenza_media_ac`
- `auth.py`, `auth_google.py` — Autenticazione, gestione utenti SQLite, Flask-Login, accesso con Google
- `automazione.py` — Thread daemon, ciclo di controllo zone (ogni 15 min default). `pianifica()` e' una funzione pura (zone + contesto + stato → azioni, stato nuovo, eventi) testata in `tests/test_automazione_piano.py`; `_esegui` invia solo i comandi che cambiano qualcosa. Target = setpoint del programma Netatmo (`setpoint_programmato`), costi dalla riga dell'ora corrente del motore (`raccomandazione_ora_corrente` in `app.py`, passata con `imposta_fornitore`). Zone escluse/in pausa/in manuale dall'utente non si toccano; modalita' `esclusiva` (termostato a 7 °C) o `affiancata` (termostato a target − `riserva_gas_delta`); `automazione_simulazione` non invia comandi. Stato di runtime (override nostri, pause, AC accesi da noi) in `data/automazione_stato.json` (`STATO_AUTOMAZIONE_FILE`)
- `dispositivi.py` — Fotografia condivisa dei dispositivi (`snapshot`, cache 60 s: AC SmartThings + casa/stanze Netatmo, normalizzati e grezzi) per dashboard, pagina Dispositivi, storico e automazione; comandi manuali validati (`comando_ac` contro la whitelist `CONTROLLI_AC` di `smartthings.py`, `setpoint_stanza`, `modalita_casa`, …); `letture_per_storico`
- `prezzi.py` — Prezzi gas/luce: tariffa variabile (TTF Yahoo Finance, PUN ENTSO-E, cache in memoria) oppure fissa (prezzo bloccato da config), per gas e luce indipendentemente
- `pannello.py` — Stima produzione del pannello adottato (Murcia, inseguitori monoassiali) da irraggiamento Open-Meteo: modello monoasse (posizione solare, backtracking, Hay-Davies, temperatura celle; il fattore e' un rendimento 0,7-0,95) oppure orizzontale; ogni modello ha il suo fattore, calibrabile; la produzione di ogni quarto d'ora compensa il consumo di casa (`copertura_pannello` in `raccomandazioni.py`) e abbassa il costo marginale della pompa di calore
- `versione.py` — `VERSIONE`, unica fonte della versione (la legge anche `pyproject.toml`)
- `providers/` — Architettura modulare per dispositivi
  - `__init__.py` — ABC `ThermostatProvider`, `HeatPumpProvider`, registry
  - `netatmo.py` — Client Netatmo OAuth2 per termostati BTicino Smarther. Stanza: `manual`/`max`/`home` (`setroomthermpoint`, `home` = programma); casa: `schedule`/`away`/`hg` (`setthermmode`). `setpoint_programmato` calcola il target dalla timetable
  - `smartthings.py` — Client SmartThings OAuth2 (consigliato) + PAT fallback per AC Samsung. `normalizza_stato`, `CONTROLLI_AC` (whitelist dei comandi manuali: i non standard compaiono solo se la definizione della capability li conferma), `controlli_disponibili`, `valida_comando`
- `templates/`, `static/` — HTML (Jinja) e asset; `static/vendor/` e' generato, non e' nel repository
  - `templates/base.html` — app shell: barra laterale (desktop, `lg`+) e barra di navigazione in basso (mobile) con le 5 sezioni; blocchi `title`, `azioni` (barra superiore), `indietro`; `admin/_nav.html` e' la sotto-navigazione admin
  - `static/css/theme.css` — token di design (CSS variable, tema chiaro e `html[data-theme="dark"]`) e componenti `tp-*`
  - `static/js/` — `theme.js` (tema, nel `<head>`), `app.js` (comune: service worker, installazione, `ogni()`, `oraLocale()`, `gradi()`, `badgeStatoZona()`), `grafici.js` (stile Chart.js condiviso: colori dal tema, plugin adesso/fasce/giorni/mirino, legenda a chip), uno script per pagina (`home.js`, `previsioni.js`, `automazione.js`, `storico.js`, `dispositivi.js` per elenco e dettaglio), `admin.js` (`apiPostJson`, `apiGetJson`, `mostraMessaggio`)
  - Quando cambia un asset statico, aggiornare `CACHE` in `static/sw.js` (cache-first) e la lista `PRECACHE`

Nella radice:

- `pyproject.toml` — metadati, dipendenze Python (`dependencies`) e di sviluppo (extra `dev`), configurazione pytest. Dependabot aggiorna i minimi di versione
- `tests/` — pytest: unit test (motore, COP, storico, tariffe, pannello, piano dell'automazione, provider) e test di integrazione delle route Flask (`test_app.py`, `test_dispositivi_app.py`); `conftest.py` isola dati e rete; `dispositivi_finti.py` e `fixtures/` contengono client e dati finti (anonimizzati da un impianto reale) di SmartThings e Netatmo
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

- `GET /` — Home (richiede login, refresh live ogni 5 min via `/api/dashboard`)
- `GET /previsioni` — Grafico costo 48h, temperatura e dettaglio orario (richiede login)
- `GET /automazione` — Interruttore automazione, zone, log eventi (richiede login)
- `GET /storico` — Grafici storici e contatore risparmi (richiede login)
- `GET /dispositivi`, `GET /dispositivi/<ac|stanza|casa>/<id>` — Elenco e dettaglio dei dispositivi: valori, controlli manuali, grafici, valori grezzi (richiede login; fuori dalla navigazione, evidenzia "Automazione")
- `GET /impostazioni` — Hub: account, tema, link alle pagine admin (richiede login)
- `GET /sw.js` — Service worker PWA (pubblico, servito dalla root per lo scope)
- `GET /login`, `POST /login`, `GET /logout` — Autenticazione
- `GET /admin/` — Impostazioni (admin)
- `GET /admin/credentials` — Credenziali API
- `GET /admin/zones` — Editor zone
- `GET /admin/users` — Gestione utenti
- `GET /api/automazione/oauth-callback` — Callback OAuth Netatmo (pubblico)
- `GET /api/automazione/smartthings-callback` — Callback OAuth SmartThings (pubblico)
- API JSON: `/api/prezzi`, `/api/dati`, `/api/temp-cfr`, `/api/config`, `/api/automazione`, `/api/dispositivi`, `/api/dashboard`, `/api/pannello`, `POST /api/pannello/calibra`, `/api/storico?da=&a=&risoluzione=oraria|giornaliera`, `/api/risparmi`
- Dispositivi: `/api/dispositivi/stato`, `/api/dispositivi/<tipo>/<id>`, `/api/dispositivi/<ac|stanza>/<id>/storico`, `POST /api/dispositivi/ac/<id>/comando` (`{chiave, valore}`), `POST /api/dispositivi/stanza/<id>/setpoint|ripristina`, `POST /api/dispositivi/casa/modalita|programma` (programma solo admin). Zone: `POST /api/automazione/zona/<room_id>/pausa` (`{ore}`), `POST /api/automazione/zona/<room_id>/attiva`. Le POST nuove richiedono `Content-Type: application/json` (415 altrimenti): con il cookie `SameSite=Lax` e' la protezione CSRF
