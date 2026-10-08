# Changelog

Tutte le modifiche rilevanti di TermoPilota sono documentate in questo file.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/) e il progetto
adotta il [Versionamento Semantico](https://semver.org/lang/it/).

## [Non rilasciato]

### Modificato
- **Interfaccia rinnovata**, pensata per il telefono (anche come app installata) e per il desktop:
  barra di navigazione in basso su mobile e barra laterale su desktop, con cinque sezioni sempre a
  portata di pollice — **Home**, **Previsioni**, **Automazione**, **Storico**, **Impostazioni** —
  al posto dell'unica pagina lunga di prima. Nessuna funzione rimossa.
  - Home: consiglio per adesso, costi caldaia/pompa, ore di oggi, risparmio della stagione, striscia
    delle prossime 12 ore, prezzi energia e pannello, temperatura delle stanze.
  - Previsioni (`/previsioni`): grafico del costo del calore a 48 ore con fasce della fonte
    consigliata, linea "adesso" e separatore di giorno, temperatura in un grafico a parte allineato
    (niente più doppio asse); dettaglio orario come lista espandibile su mobile e tabella su desktop.
  - Automazione (`/automazione`): interruttore, schede delle zone e registro eventi.
  - Impostazioni (`/impostazioni`): account, tema chiaro/scuro/automatico, voci di amministrazione
    (solo admin), installazione dell'app, versione ed uscita. Le pagine admin hanno una
    sotto-navigazione comune e il pulsante per tornare indietro.
- Grafici moderni e coerenti col tema: colori presi dal tema chiaro/scuro (si aggiornano al cambio
  senza ricaricare), gradienti, mirino e tooltip al tocco, legenda cliccabile per nascondere le serie.
- Il manifest della PWA ha le scorciatoie per Previsioni, Automazione e Storico.

### Corretto
- L'ora "adesso" evidenziata nel dettaglio orario e gli intervalli dello Storico usavano l'ora UTC
  del browser invece di quella locale.

## [1.0.3] - 2026-10-08

### Aggiunto
- **Modello monoasse del pannello**: l'impianto usa inseguitori solari a un asse, e il modello
  originale (potenza × irraggiamento orizzontale × fattore) costringeva il fattore ad assorbirne il
  guadagno, che cambia con ora e stagione (di mattina e sera la luce sul pannello è fino a 2,5 volte
  quella orizzontale, a mezzogiorno è uguale): un fattore calibrato in un momento era sbagliato
  negli altri. Ora si calcolano posizione del sole, inclinazione dell'inseguitore (con
  backtracking), luce diretta e diffusa sul piano del pannello (Hay-Davies) e perdita per la
  temperatura delle celle. Il fattore diventa un rendimento di sistema (atteso 0,7-0,95), costante
  tutto l'anno; la calibrazione avvisa se il valore è fuori range, segno di una potenza di picco
  sbagliata. Si sceglie in Impostazioni (modello monoasse o orizzontale).
- **IVA per gas e luce** (campi `gas_iva_pct` e `luce_iva_pct`): il prezzo usato nel confronto è
  `(energia + altre voci al consumo) × (1 + IVA)`. Conta perché le due IVA sono diverse (luce 10%,
  gas 22% oltre i primi 480 Smc/anno) e quindi cambiano il rapporto fra il costo del gas e quello
  della pompa di calore. In Impostazioni si vede il costo marginale lordo calcolato, da confrontare
  con la bolletta. Il valore predefinito è 0, così le configurazioni esistenti (che di solito
  includevano già l'IVA nelle voci fisse) non cambiano da sole. Con la compensazione "solo materia
  prima" anche l'IVA sulla materia prima viene azzerata.
- Il Plant ID di Netatmo si sceglie da un menu a tendina (Credenziali API) che si riempie da solo
  dopo l'autorizzazione; se l'account ha un solo impianto viene impostato automaticamente.

### Modificato
- La ricerca di termostati e condizionatori è solo nella scheda Zone: parte da sola all'apertura
  e ha un unico pulsante "Aggiorna elenco dispositivi". Nelle Credenziali API non c'è più.
- L'autorizzazione Netatmo e SmartThings si apre nella stessa scheda e, al ritorno, si vede subito
  l'esito: prima il popup atterrava sulla pagina dentro il popup stesso.
- Il messaggio "Dati Netatmo non disponibili" dice ora il motivo (Netatmo non collegato, Plant ID
  mancante, stanza non presente nella risposta, temperatura o setpoint non forniti, con i campi
  ricevuti) e viene scritto anche nel log del container.
- La stima del pannello riferisce i dati di Open-Meteo, che sono medie dell'ora precedente,
  all'inizio dell'ora (prima erano sfalsati di un'ora) e il valore "adesso" è interpolato fra
  due ore.
- Il fattore di resa è ora per modello (`pannello_fattore_monoasse`, nuovo, e `pannello_fattore`,
  del modello orizzontale): il valore già calibrato resta com'è e non vale per il monoasse, che
  parte da 0,85 e va ricalibrato.
- Tolti dall'interfaccia e dal repository i nomi del fornitore di luce e gas.
- I log dei moduli (livello INFO) compaiono nel log del container: prima gunicorn li scartava.

## [1.0.2] - 2026-10-08

### Aggiunto
- Supporto ai reverse proxy: con `TERMOPILOTA_PROXY=1` l'app si fida degli header
  `X-Forwarded-*` di un proxy (Traefik, nginx). Serve perché il callback di Google OAuth venga
  composto con `https` e con l'indirizzo pubblico. È spento di default: con la porta esposta
  direttamente un client potrebbe falsificare quegli header.

## [1.0.1] - 2026-10-08

Manutenzione: nessuna novità per chi usa l'app, ma il progetto è riordinato e le dipendenze sono
aggiornate.

### Modificato
- I sorgenti Python, i template e i file statici sono nel pacchetto `src/termopilota/`; nella
  radice restano `tests/`, `scripts/`, `data/` e i file di progetto. L'avvio locale è
  `python -m termopilota` (in produzione `gunicorn termopilota.app:app`).
- Le dipendenze Python sono dichiarate in `pyproject.toml` (al posto di `requirements*.txt`):
  `pip install -e ".[dev]"` installa app e strumenti di test.
- Il percorso dei dati (`data/`, o `TERMOPILOTA_DATA_DIR`) è definito in un solo punto
  (`percorsi.py`); prima era ripetuto in sei file e i provider ignoravano `TERMOPILOTA_DATA_DIR`.
- Il container usa un solo worker gunicorn con 4 thread, senza `--preload`: con più processi i
  servizi in background venivano duplicati e lo smoke test della CI andava in timeout a tratti.
- Dipendenze aggiornate: Bootstrap 5.3.8, Bootstrap Icons 1.13.1, Chart.js 4.5.1, Flask 3.1,
  gunicorn 26, authlib 1.8, GitHub Actions alle ultime versioni principali.

### Rimosso
- I moduli di compatibilità `bticino.py` e `samsung.py` (non li importava più nessuno).

### Note per chi aggiorna
- Chi lancia l'app a mano deve usare `python -m termopilota` invece di `python app.py`.
- Con Docker non cambia nulla: l'immagine mantiene `/app/data` come cartella dei dati.

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
- **Pannello adottato (Murcia)**: stima della produzione da irraggiamento (Open-Meteo)
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

[Non rilasciato]: https://github.com/gcerretani/termopilota/compare/v1.0.3...HEAD
[1.0.3]: https://github.com/gcerretani/termopilota/releases/tag/v1.0.3
[1.0.2]: https://github.com/gcerretani/termopilota/releases/tag/v1.0.2
[1.0.1]: https://github.com/gcerretani/termopilota/releases/tag/v1.0.1
[1.0.0]: https://github.com/gcerretani/termopilota/releases/tag/v1.0.0
