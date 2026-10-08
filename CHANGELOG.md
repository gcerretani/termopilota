# Changelog

Tutte le modifiche rilevanti di TermoPilota sono documentate in questo file.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/) e il progetto
adotta il [Versionamento Semantico](https://semver.org/lang/it/).

## [Non rilasciato]

## [1.0.0] - 2026-10-08

Prima versione stabile.

### Funzionalità di base
- Dashboard con raccomandazione in tempo reale tra caldaia a gas e pompa di calore, in base a
  temperatura esterna, COP e prezzi dell'energia, con previsioni a 48 ore e grafico comparativo.
- Prezzi automatici: gas dal TTF (Yahoo Finance) e luce dal PUN (ENTSO-E), con ripiego sui prezzi
  manuali.
- Temperatura reale dalla stazione meteo CFR Toscana, configurabile.
- Automazione per zona: legge setpoint e temperatura dai termostati BTicino/Netatmo e commuta tra
  caldaia e condizionatore Samsung (SmartThings) quando conviene; un condizionatore può servire
  più stanze e si spegne solo quando tutte sono a temperatura.
- Area admin per utenti, credenziali API, zone e prezzi; login con password o con Google (OAuth).
- Tema chiaro e scuro; architettura modulare a provider per termostati e pompe di calore.

### Aggiunto
- **Storico e risparmi**: un campionatore registra ogni ora prezzi, temperatura e raccomandazione
  in `data/storico.db`. La nuova pagina Storico mostra i costi, la temperatura, le ore per fonte e
  il risparmio cumulativo (7 giorni, 30 giorni, stagione) con il contatore "Risparmiato questa
  stagione". Il risparmio è una stima che usa la potenza termica configurata in Admin.
- **Tariffe fisse o variabili**, indipendenti per luce e gas: con la tariffa fissa si usa il prezzo
  bloccato di contratto e non si interrogano TTF e PUN. La componente fissa (distribuzione, oneri,
  tasse) si somma in entrambi i casi.
- **Pannello adottato (Plenitude, Murcia)**: stima della produzione da irraggiamento (Open-Meteo)
  con fattore di resa calibrabile sulla lettura dell'app. La produzione di ogni quarto d'ora
  compensa il consumo di casa e riduce il costo marginale della pompa di calore; la modalità di
  compensazione (tutto il costo, solo la materia prima, nessuna) è configurabile.
- **App installabile (PWA)** con icone, manifest e service worker; funziona anche in LAN senza
  internet perché le librerie front-end non vengono più caricate da CDN.
- **Dashboard live**: si aggiorna da sola ogni 5 minuti e al ritorno sul tab, senza ricaricare.
- Interfaccia mobile rivista: tabella oraria scorrevole con colonna Ora fissa, hero compatta, aree
  di tocco più ampie, supporto alle safe area.
- Test automatici (pytest) su motore di raccomandazione, tariffe, storico, pannello, route Flask,
  PWA e licenza; CI su ogni pull request con smoke test del container.
- Licenza GNU GPL v3 o successiva, con intestazioni SPDX nei sorgenti e avvisi di terze parti.
- Dependabot per dipendenze Python, librerie npm, immagini Docker e GitHub Actions.
- Versione dell'applicazione visibile nel piè di pagina.

### Modificato
- La tabella COP e l'interpolazione sono definite una sola volta (`costanti.py`) e il motore di
  raccomandazione è un modulo a sé (`raccomandazioni.py`).
- Le librerie front-end (Bootstrap, Bootstrap Icons, Chart.js) sono dichiarate in `package.json` a
  versioni esatte e copiate in `static/vendor/` con `npm run vendor`: la cartella non è più nel
  repository.
- L'immagine Docker è costruita in due fasi (Node per le librerie, poi Python) e viene pubblicata
  solo se i test passano.

### Sicurezza
- `.dockerignore` esclude dall'immagine `data/`, `config.json` e i file di sviluppo: prima una
  build locale avrebbe incluso database e credenziali presenti sul computer.

### Note per chi aggiorna
- Per sviluppare serve Node 22: dopo il clone eseguire `npm ci && npm run vendor`.
- Le nuove chiavi di configurazione hanno valori predefiniti, non serve modificare `config.json`.
- Al primo avvio viene creato `data/storico.db`; lo storico parte vuoto e si popola con l'uso.
- L'automazione dei termostati non tiene ancora conto del pannello: calcola il costo della pompa
  senza compensazione, mentre la dashboard sì.

[Non rilasciato]: https://github.com/gcerretani/termopilota/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/gcerretani/termopilota/releases/tag/v1.0.0
