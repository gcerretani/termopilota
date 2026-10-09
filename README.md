# TermoPilota

Sistema di controllo intelligente del riscaldamento domestico. Confronta in tempo reale il costo del riscaldamento a gas (caldaia a condensazione) con la pompa di calore (condizionatore inverter), e può gestire automaticamente la commutazione tra le due fonti.

## Funzionalita'

- **Dashboard** con raccomandazione in tempo reale (gas o AC) basata su temperatura esterna, COP e prezzi energia
- **Previsioni 48 ore** con grafico comparativo costi gas vs AC
- **Prezzi automatici**: commodity gas (TTF da Yahoo Finance) e luce (PUN da ENTSO-E)
- **Temperatura reale** dalla stazione meteo CFR Toscana (configurabile)
- **Automazione per stanza**: segue il programma dei termostati Netatmo e commuta tra caldaia e AC quando conviene; ogni stanza si include o esclude e si mette in pausa
- **Pagina della stanza**: termostato e condizionatore insieme, cosa fa l'automazione e perché, comandi, grafico delle due temperature e correzione suggerita del setpoint dell'AC
- **Modalita' esclusiva o affiancata**: con l'AC la caldaia si spegne oppure resta di riserva qualche grado sotto il target
- **Simulazione**: l'automazione decide e registra senza inviare comandi
- **Gestione AC condiviso**: un condizionatore puo' servire piu' stanze, si spegne solo quando tutte sono a temperatura
- **Pagina Dispositivi**: tutti i valori di condizionatori, stanze e casa Netatmo, grafici delle letture e comandi manuali
- **Consumo reale** dei condizionatori dal loro contatore di energia, con risparmio misurato nello Storico
- **Area admin** per gestione utenti, credenziali API, configurazione stanze e prezzi
- **Architettura modulare** a provider per termostati e pompe di calore

## Impianto

| Componente | Modello |
|---|---|
| Caldaia | Condensazione con regolazione climatica, mandata ~30C |
| Distribuzione | Pavimento radiante |
| Pompa di calore | Samsung AJ040TXJ2KG/EU WindFree Comfort Dual |
| Termostati | BTicino Smarther with Netatmo (4 stanze) |
| Condizionatori | 2 split (1 serve 3 stanze, 1 serve 1 stanza) |

## Setup locale

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Librerie front-end (Bootstrap, Chart.js...) in src/termopilota/static/vendor/: richiede Node 22
npm ci && npm run vendor

# Avvia (prima volta, imposta le credenziali admin; la configurazione si modifica da /admin)
export ADMIN_USER=admin
export ADMIN_PASSWORD=la_tua_password
python -m termopilota
```

L'app e' disponibile su http://localhost:5001

## Deploy con Docker

```bash
# Avvio da immagine pubblicata
ADMIN_PASSWORD=la_tua_password docker compose up -d

# Oppure con variabili personalizzate
ADMIN_USER=giovanni ADMIN_PASSWORD=secret SECRET_KEY=chiave_segreta docker compose up -d
```

Dietro un reverse proxy (Traefik, nginx) impostare `TERMOPILOTA_PROXY=1`, altrimenti i callback OAuth usano `http`.

L'immagine Docker viene costruita automaticamente su push a `main` e pubblicata su `ghcr.io/gcerretani/termopilota`.

```bash
# Pull dell'immagine pre-costruita
docker pull ghcr.io/gcerretani/termopilota:latest
```

## Architettura

```
src/termopilota/
  app.py                # Flask app principale, routes dashboard e API
  auth.py               # Autenticazione utenti (SQLite + Flask-Login)
  automazione.py        # Thread daemon per controllo automatico zone (piano puro + esecuzione)
  dispositivi.py        # Fotografia condivisa dei dispositivi e comandi manuali validati
  prezzi.py             # Fetch prezzi energia (TTF gas, PUN luce)
  raccomandazioni.py    # Motore di raccomandazione caldaia / pompa di calore
  storico.py            # Storico orario e stima dei risparmi
  providers/
    __init__.py         # ABC ThermostatProvider, HeatPumpProvider + registry
    netatmo.py          # Provider termostati Netatmo (OAuth2)
    smartthings.py      # Provider AC Samsung SmartThings
  templates/            # Pagine HTML (dashboard, storico, admin/...)
  static/               # CSS, JS, icone, service worker
tests/                  # pytest
scripts/                # librerie front-end, icone PWA
pyproject.toml          # dipendenze Python e metadati
data/                   # configurazione e database a runtime (gitignored)
config.example.json     # Template configurazione
```

## Configurazione

Il file `config.json` contiene:

- **Prezzi energia**: componenti fisse gas/luce, valori manuali di fallback, token ENTSO-E per PUN automatico
- **Impianto**: efficienza caldaia, temperatura minima operativa AC, setpoint interno
- **Credenziali**: client ID/secret Netatmo (OAuth2), token SmartThings (PAT)
- **Stanze** ("zone" nella configurazione): associazione stanza Netatmo (room_id) a condizionatore Samsung (ac_device_id), inclusione nell'automazione, modalita' esclusiva/affiancata, riserva della caldaia, correzione del setpoint dell'AC
- **Automazione**: intervallo controllo, soglia risparmio minimo, simulazione, pausa dopo un comando manuale, ventola e modalita' notturna dell'AC

Lo stato di runtime dell'automazione (override in corso, pause, AC accesi da TermoPilota) e' in `data/automazione_stato.json`.

## COP Samsung AJ040TXJ2KG/EU

Tabella COP ancorata al valore certificato EN14511: **4.47 W/W a +7C** (SCOP 4.61).

| T esterna | COP |
|-----------|-----|
| -15C | 1.60 |
| -10C | 1.95 |
| -7C | 2.20 |
| -5C | 2.40 |
| 0C | 2.90 |
| +7C | **4.47** |
| +15C | 5.15 |
| +20C | 5.40 |

Temperatura di break-even (gas = AC): circa **-6C** con prezzi tipici.

## API esterne

| Servizio | Scopo | Autenticazione |
|---|---|---|
| Netatmo (api.netatmo.com) | Termostati BTicino | OAuth2 (read_smarther, write_smarther) |
| SmartThings (api.smartthings.com) | Condizionatori Samsung | Personal Access Token |
| ENTSO-E Transparency | Prezzo PUN luce | Token API gratuito |
| Yahoo Finance | Prezzo TTF gas | Nessuna |
| CFR Toscana | Temperatura esterna | Nessuna |
| Open-Meteo / Met.no | Previsioni meteo | Nessuna |

## Licenza

Copyright (C) 2026 Giovanni Cerretani

TermoPilota e' software libero: puoi ridistribuirlo e/o modificarlo secondo i termini della
[GNU General Public License](LICENSE) pubblicata dalla Free Software Foundation, versione 3
della Licenza o (a tua scelta) qualsiasi versione successiva.

Il programma e' distribuito nella speranza che sia utile, ma SENZA ALCUNA GARANZIA, nemmeno
quella implicita di COMMERCIABILITA' o IDONEITA' PER UNO SCOPO PARTICOLARE. Si veda la GNU
General Public License per maggiori dettagli.

Ogni file sorgente riporta l'identificatore `SPDX-License-Identifier: GPL-3.0-or-later`.
Le librerie front-end (Bootstrap, Bootstrap Icons, Chart.js) hanno licenza MIT compatibile con la
GPL v3: vedi [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
