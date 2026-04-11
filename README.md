# TermoPilota

Sistema di controllo intelligente del riscaldamento domestico. Confronta in tempo reale il costo del riscaldamento a gas (caldaia a condensazione) con la pompa di calore (condizionatore inverter), e può gestire automaticamente la commutazione tra le due fonti.

## Funzionalita'

- **Dashboard** con raccomandazione in tempo reale (gas o AC) basata su temperatura esterna, COP e prezzi energia
- **Previsioni 48 ore** con grafico comparativo costi gas vs AC
- **Prezzi automatici**: commodity gas (TTF da Yahoo Finance) e luce (PUN da ENTSO-E)
- **Temperatura reale** dalla stazione meteo CFR Toscana (configurabile)
- **Automazione per zona**: legge setpoint dai termostati Netatmo, commuta tra caldaia e AC quando conviene
- **Gestione AC condiviso**: un condizionatore puo' servire piu' stanze, si spegne solo quando tutte sono a temperatura
- **Area admin** per gestione utenti, credenziali API, configurazione zone e prezzi
- **Architettura modulare** a provider per termostati e pompe di calore

## Impianto

| Componente | Modello |
|---|---|
| Caldaia | Condensazione con regolazione climatica, mandata ~30C |
| Distribuzione | Pavimento radiante |
| Pompa di calore | Samsung AJ040TXJ2KG/EU WindFree Comfort Dual |
| Termostati | BTicino Smarther with Netatmo (4 zone) |
| Condizionatori | 2 split (1 serve 3 zone, 1 serve 1 zona) |

## Setup locale

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copia e configura
cp config.example.json config.json
# Modifica config.json con i tuoi prezzi e credenziali

# Avvia (prima volta, imposta le credenziali admin)
export ADMIN_USER=admin
export ADMIN_PASSWORD=la_tua_password
python app.py
```

L'app e' disponibile su http://localhost:5001

## Deploy con Docker

```bash
# Build e avvio
ADMIN_PASSWORD=la_tua_password docker compose up -d

# Oppure con variabili personalizzate
ADMIN_USER=giovanni ADMIN_PASSWORD=secret SECRET_KEY=chiave_segreta docker compose up -d
```

L'immagine Docker viene costruita automaticamente su push a `main` e pubblicata su `ghcr.io/gcerretani/termopilota`.

```bash
# Pull dell'immagine pre-costruita
docker pull ghcr.io/gcerretani/termopilota:latest
```

## Architettura

```
app.py                  # Flask app principale, routes dashboard e API
auth.py                 # Autenticazione utenti (SQLite + Flask-Login)
automazione.py          # Thread daemon per controllo automatico zone
prezzi.py               # Fetch prezzi energia (TTF gas, PUN luce)
providers/
  __init__.py           # ABC ThermostatProvider, HeatPumpProvider + registry
  netatmo.py            # Provider termostati Netatmo (OAuth2)
  smartthings.py        # Provider AC Samsung SmartThings (PAT)
templates/
  base.html             # Layout condiviso (navbar, CSS, JS comuni)
  login.html            # Pagina login
  dashboard.html        # Dashboard principale con raccomandazioni
  admin/
    settings.html       # Configurazione prezzi e impianto
    credentials.html    # Credenziali API Netatmo e Samsung
    zones.html          # Editor zone (stanza -> condizionatore)
    users.html          # Gestione utenti
config.json             # Configurazione runtime (gitignored)
config.example.json     # Template configurazione
```

## Configurazione

Il file `config.json` contiene:

- **Prezzi energia**: componenti fisse gas/luce, valori manuali di fallback, token ENTSO-E per PUN automatico
- **Impianto**: efficienza caldaia, temperatura minima operativa AC, setpoint interno
- **Credenziali**: client ID/secret Netatmo (OAuth2), token SmartThings (PAT)
- **Zone**: associazione stanza Netatmo (room_id) a condizionatore Samsung (ac_device_id)
- **Automazione**: intervallo controllo, soglia risparmio minimo

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
