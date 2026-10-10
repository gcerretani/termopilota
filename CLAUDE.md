# TermoPilota — Guida sviluppo

## Stack

- **Backend**: Flask (Python 3.14), gunicorn in produzione
- **Frontend**: Bootstrap 5.3.8, Chart.js 4.5.1, Leaflet 1.9.4 (solo Admin → Impostazioni, mappe OpenStreetMap), vanilla JS (no bundler) — nessuna CDN: le librerie sono dichiarate in `package.json`/`package-lock.json` (Dependabot le aggiorna) e copiate in `src/termopilota/static/vendor/` (non committata) con `npm run vendor`; PWA installabile (manifest + service worker)
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
- `temperatura_esterna.py` — Scelta pura della temperatura esterna attuale (`scegli`: prima fonte valida in `ordine(priorita_temp_esterna)` con misura non piu' vecchia di `temp_esterna_max_eta_minuti`; None = previsione). In `app.py` `misure_temp_esterna` (modulo Netatmo dalla fotografia, CFR da `scarica_temp_cfr`) e `temperatura_esterna_attuale`, usate da dashboard, `/api/dati` e `raccomandazione_ora_corrente`
- `raccomandazioni.py` — Motore `calcola_raccomandazioni` (importabile senza avviare Flask, testabile)
- `storico.py` — Persistenza SQLite (`data/storico.db`): campionatore orario in thread daemon, query per grafici e stima risparmi; tabella `letture_dispositivi` (ogni 15 min, AC e stanze) con il contatore di energia degli AC: `energia_ac`, `consumi_misurati` (risparmio misurato), `potenza_media_ac`. Tabella generica `misure` (`sorgente` `ac`|`stanza`|`sensore`|`cfr`, `id`, `grandezza`, `ts`, `valore`; righe da `dispositivi.misure_per_storico`) con il catalogo `serie` (nome, etichetta, unita'): `registra_misure`, `catalogo_serie` (piu' le serie `sistema` lette da `campioni`, `SERIE_SISTEMA`), `leggi_serie`; oltre `MISURE_DETTAGLIO_GIORNI` (90) restano le medie orarie (`_compatta_misure`). Le letture `meteo` della 1.7.0 passano in `misure` all'avvio (`_migra_letture_meteo`)
- `auth.py`, `auth_google.py` — Autenticazione, gestione utenti SQLite, Flask-Login, accesso con Google
- `automazione.py` — Thread daemon, ciclo di controllo zone (ogni 15 min default). `pianifica()` e' una funzione pura (zone + contesto + stato → azioni, stato nuovo, eventi) testata in `tests/test_automazione_piano.py`; `_esegui` invia solo i comandi che cambiano qualcosa. Target = setpoint del programma Netatmo (`setpoint_programmato`), costi dalla riga dell'ora corrente del motore (`raccomandazione_ora_corrente` in `app.py`, passata con `imposta_fornitore`). Zone escluse/in pausa/in manuale dall'utente non si toccano; modalita' `esclusiva` (termostato a 7 °C) o `affiancata` (termostato a target − `riserva_gas_delta`); `automazione_simulazione` non invia comandi. Stato di runtime (override nostri, pause, AC accesi da noi) in `data/automazione_stato.json` (`STATO_AUTOMAZIONE_FILE`)
- `dispositivi.py` — Fotografia condivisa dei dispositivi (`snapshot`: AC SmartThings + casa/stanze Netatmo + moduli esterni della stazione meteo, normalizzati e grezzi; tre parti con cache indipendenti, `TTL_PARTI` 60 s per `ac`/`netatmo` e 300 s per `meteo`, `invalida("ac"|"netatmo"|"meteo")`, `snapshot(cfg, parti=("meteo",))` rilegge solo quelle, cosi' un evento SmartThings non rilegge Netatmo; `modulo_meteo` = modulo esterno in uso; un solo avviso nel registro per disservizio Netatmo; `homesdata` in cache 15 min in `netatmo.py`) per dashboard, pagina Dispositivi, storico e automazione; comandi manuali validati (`comando_ac` contro la whitelist `CONTROLLI_AC` di `smartthings.py`, `setpoint_stanza`, `modalita_casa`, …); `letture_per_storico` (anche `tipo='meteo'`; la CFR si aggiunge in `app._letture_dispositivi` con id `cfr:<stazione>`, per il confronto)
- `live.py` — Aggiornamenti live dai webhook: principio "notifica → rilettura" (l'evento invalida la fotografia, incrementa una versione che le pagine interrogano con `ascoltaLive()` di `app.js`, e per le zone automatizzate chiama `ricalcola()` con debounce di 60 s); `comando_nostro(ident)` prima di ogni comando inviato, cosi' gli eventi che ne derivano (30 s) non fanno ripartire il ciclo
- `registro.py` — Registro eventi persistente (tabella `registro` di `storico.db`, 30 giorni, debug 7): `scrivi(categoria, messaggio, livello=, oggetto=, dati=, utente=)` non solleva mai e ripulisce i segreti dai `dati`; categorie `automazione`/`comando`/`evento`/`sistema`; `GestoreLogRegistro` porta i WARNING/ERROR dei logger `termopilota.*`. L'automazione scrive qui (`_log_evento`), le route POST registrano l'utente (`_registra_comando`, `_esito_comando` con descrizione)
- `osservatore.py` — Polling Netatmo (`netatmo_polling_secondi`, default 120): `dispositivi.aggiorna_netatmo` rilegge solo Netatmo, `differenze()` (pura) confronta con la lettura precedente; i cambi vanno nel registro ("da TermoPilota" o "esterno") e in `live.notifica`. Serve perche' Netatmo non manda webhook per i termostati Smarther
- `prezzi.py` — Prezzi gas/luce: tariffa variabile (TTF Yahoo Finance, PUN ENTSO-E, cache in memoria) oppure fissa (prezzo bloccato da config), per gas e luce indipendentemente
- `pannello.py` — Stima produzione del pannello adottato (Murcia, inseguitori monoassiali) da irraggiamento Open-Meteo: modello monoasse (posizione solare, backtracking, Hay-Davies, temperatura celle; il fattore e' un rendimento 0,7-0,95) oppure orizzontale; ogni modello ha il suo fattore, calibrabile; la produzione di ogni quarto d'ora compensa il consumo di casa (`copertura_pannello` in `raccomandazioni.py`) e abbassa il costo marginale della pompa di calore
- `versione.py` — `VERSIONE`, unica fonte della versione (la legge anche `pyproject.toml`)
- `providers/` — Architettura modulare per dispositivi
  - `__init__.py` — ABC `ThermostatProvider`, `HeatPumpProvider`, registry; `chiamata(servizio, metodo, url, ...)` per tutte le richieste alle API dei dispositivi: conta le chiamate dell'ultima ora (`conteggio_chiamate`, in Credenziali API) e, se il servizio segnala il limite (429 o 403 codice 26), solleva `LimiteChiamate` per 10 minuti senza chiamare
  - `netatmo.py` — Client Netatmo OAuth2 per termostati BTicino Smarther. Stanza: `manual`/`max`/`home` con `setstate` (`home` = programma): con gli scope Smarther `setroomthermpoint` risponde 403; casa: `schedule`/`away`/`hg` (`setthermmode`) e `switchhomeschedule`, ammessi. I rifiuti sollevano `ErroreNetatmo` (messaggio e codice di Netatmo). `setpoint_programmato` calcola il target dalla timetable. homestatus omette stanza e modulo dei termostati offline e li elenca in `errors` (codice 6): `stato_casa` li restituisce in `errori` e `dispositivi.snapshot` tiene la stanza come non raggiungibile. Gli scope (`SCOPE`) li chiede l'URL di autorizzazione, non si impostano su dev.netatmo.com: aggiungerne uno vuol dire ricollegare l'account. `stato_stazioni` (sensori della stazione meteo riconosciuti dal tipo, `TIPI_MODULI_METEO`; `esterno` per `TIPI_MODULI_ESTERNI`): getstationsdata se il token ha `read_station` (`ha_scope`), altrimenti o se e' vuoto i moduli della casa da homestatus (`moduli_esterni_casa`). Le stanze si ricavano dai soli moduli non meteo: niente nomi o tipi di stanza cablati
  - `smartthings.py` — Client SmartThings OAuth2 (consigliato) + PAT fallback per AC Samsung. `normalizza_stato`, `CONTROLLI_AC` (whitelist dei comandi manuali: i non standard compaiono solo se la definizione della capability li conferma; `da_definizione` prende valori/intervallo dallo schema del comando), `controlli_disponibili`, `valida_comando`; console admin `comandi_avanzati`/`valida_comando_avanzato` (tutti i comandi delle definizioni tranne `CAPABILITY_ESCLUSE` e gli argomenti non semplici); sottoscrizioni agli eventi (`sottoscrivi_dispositivo`, serve `installed_app_id` del token OAuth)
- `templates/`, `static/` — HTML (Jinja) e asset; `static/vendor/` e' generato, non e' nel repository
  - `templates/base.html` — app shell: barra laterale (desktop, `lg`+) e barra di navigazione in basso (mobile) con le 5 sezioni; blocchi `title`, `azioni` (barra superiore), `indietro`; `admin/_nav.html` e' la sotto-navigazione admin
  - `static/css/theme.css` — token di design (CSS variable, tema chiaro e `html[data-theme="dark"]`) e componenti `tp-*`
  - `static/js/` — `theme.js` (tema, nel `<head>`), `app.js` (comune: service worker, installazione, `ogni()`, `oraLocale()`, `gradi()`, `badgeStatoZona()`), `grafici.js` (stile Chart.js condiviso: colori dal tema, plugin adesso/fasce/giorni/mirino, legenda a chip, `periodo`, `etichettaPeriodo`, `graficoSerie` con un asse y per unita'), uno script per pagina (`home.js`, `previsioni.js`, `automazione.js`, `storico.js`, `esplora.js` per Storico → Esplora i dati, `dispositivi.js` per elenco e dettaglio, `stanza.js`); in `app.js` anche `valoreGrandezza`, `tipoSensore` e `htmlSensori` (riquadro "Sensori nella stanza"), `admin.js` (`apiPostJson`, `apiGetJson`, `mostraMessaggio`)
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
- **Licenza**: GPL v3 o successiva (`LICENSE`). Ogni nuovo `.py`, `.js` o `.css` proprio deve iniziare con `SPDX-License-Identifier: GPL-3.0-or-later` (dopo l'eventuale shebang), altrimenti `tests/test_licenza.py` fallisce. Le librerie front-end sono MIT (Leaflet BSD-2-Clause, con `LICENSE` copiato in `static/vendor/leaflet/`) e vanno elencate in `THIRD_PARTY_NOTICES.txt`
- **COP_TABELLA**: definita una sola volta in `costanti.py`, importata da `app.py` e `automazione.py`

## File sensibili (mai committare)

- `data/config.json` — contiene token OAuth, client secret, PAT SmartThings
- `data/users.db` — hash password utenti
- `*.log`

## Route principali

- `GET /` — Home (richiede login, refresh live ogni 5 min via `/api/dashboard`)
- `GET /previsioni` — Grafico costo 48h, temperatura e dettaglio orario (richiede login)
- `GET /automazione` — Interruttore automazione, zone, log eventi (richiede login)
- `GET /storico` — Grafici storici, contatore risparmi e "Esplora i dati" (ogni misura di ogni dispositivo, `?s=` per le serie scelte; richiede login)
- `GET /dispositivi`, `GET /dispositivi/<ac|stanza|casa|meteo>/<id>` — Elenco e dettaglio dei dispositivi: valori, controlli manuali (non per `meteo`, sola lettura), grafici, valori grezzi (richiede login; fuori dalla navigazione, evidenzia "Automazione")
- `GET /stanze/<room_id>` — Pagina della stanza ("zona" nel codice, "stanza" in interfaccia; chiave = `room_id` Netatmo): termostato e AC insieme, calcolo dei setpoint, comandi, grafico combinato, correzione suggerita (`storico.differenza_sensori`), registro, impostazioni (admin). Evidenzia "Home"
- `GET /registro` — Registro eventi con filtri (richiede login; fuori dalla navigazione, evidenzia "Automazione"; i `dati` tecnici solo agli admin)
- `GET /impostazioni` — Hub: account, tema, link alle pagine admin (richiede login)
- `GET /sw.js` — Service worker PWA (pubblico, servito dalla root per lo scope)
- `GET /login`, `POST /login`, `GET /logout` — Autenticazione
- `GET /admin/` — Impostazioni (admin)
- `GET /admin/credentials` — Credenziali API
- `GET /admin/zones` — Admin → Stanze: elenco e "Nuova stanza" (le impostazioni si modificano nella pagina della stanza)
- `GET /admin/users` — Gestione utenti
- `GET /api/automazione/oauth-callback` — Callback OAuth Netatmo (pubblico)
- `GET /api/automazione/smartthings-callback` — Callback OAuth SmartThings (pubblico)
- `POST /api/webhook/netatmo` — Eventi Netatmo (pubblico, firma `X-Netatmo-secret` = HMAC-SHA256 del corpo col client secret)
- `POST /api/webhook/smartthings/<token>` — Eventi SmartThings (pubblico: token casuale `smartthings_webhook_token` nell'URL, `installedAppId`, conferma solo verso `*.smartthings.com`)
- API JSON: `/api/prezzi`, `/api/dati`, `/api/temp-cfr`, `/api/temp-esterna` (misura scelta e tutte le fonti), `/api/config`, `/api/automazione`, `/api/dispositivi`, `/api/dashboard`, `/api/pannello`, `POST /api/pannello/calibra`, `/api/storico?da=&a=&risoluzione=oraria|giornaliera`, `/api/risparmi`
- Dispositivi: `/api/dispositivi/stato`, `/api/dispositivi/<tipo>/<id>`, `/api/dispositivi/<ac|stanza>/<id>/storico`. Serie: `GET /api/serie` (catalogo per dispositivo, chiave `sorgente|id|grandezza`), `GET /api/serie/dati?s=&s=&da=&a=&risoluzione=` (al massimo `MAX_SERIE` = 8), `POST /api/dispositivi/ac/<id>/comando` (`{chiave, valore}`), `POST /api/dispositivi/stanza/<id>/setpoint|boost|ripristina`, `GET /api/dispositivi/ac/<id>/avanzati` e `POST .../avanzato` (`{capability, comando, argomenti}`, admin), `POST /api/dispositivi/casa/modalita|programma` (programma solo admin). Stanze: `GET /api/stanze/<room_id>` (tutto per la pagina), `GET /api/stanze/<room_id>/storico`, `GET /api/stanze/opzioni`, `POST /api/stanze` (crea), `POST /api/stanze/<room_id>` (modifica; se cambia il termostato cambia la chiave), `DELETE /api/stanze/<room_id>` (admin; rilascio con `automazione.rilascio_zona`). Registro: `GET /api/registro?categorie=&livello=&oggetto=&q=&prima_di=&limite=`. Live: `GET /api/live` (versione e contatori), `POST /api/live/smartthings/rigenera-token` (admin), `GET /api/live/configurazione` e `POST /api/live/<netatmo|smartthings>/<attiva|disattiva>` (admin). Zone: `POST /api/automazione/zona/<room_id>/pausa` (`{ore}`), `POST /api/automazione/zona/<room_id>/attiva`. Le POST nuove richiedono `Content-Type: application/json` (415 altrimenti): con il cookie `SameSite=Lax` e' la protezione CSRF
