// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — pagina Dispositivi (elenco) e dettaglio di un dispositivo:
// valori principali, controlli manuali, grafici delle letture e tutti i valori grezzi.

(function () {
  const MODI_STANZA = { manual: 'Manuale', home: 'Programma', max: 'Massimo', schedule: 'Programma',
                        away: 'Assente', hg: 'Antigelo', off: 'Spento' };
  const MODI_CASA = [['schedule', 'Programma', 'calendar-week'], ['away', 'Assente', 'door-closed'],
                     ['hg', 'Antigelo', 'snow3']];
  const DURATE = [[60, '1 h'], [180, '3 h'], [480, '8 h'], [1440, '24 h']];
  const MODI_AC = { heat: 'Riscaldamento', cool: 'Raffrescamento', dry: 'Deumidificazione', fan: 'Ventilazione',
                    auto: 'Automatico', wind: 'Ventilazione' };

  const testoModo = (m) => MODI_STANZA[m] || m || '—';
  const kwh = (v) => (v === null || v === undefined) ? '—' : `${Number(v).toFixed(1)} kWh`;
  const enc = encodeURIComponent;
  const vuoto = (v) => v === null || v === undefined || (Array.isArray(v) && v.length === 0)
    || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0);
  // Netatmo: wifi_strength piu' basso = segnale migliore (56 buono, 71 medio, 86 debole)
  const qualitaWifi = (v) => v === null || v === undefined ? '—' : v <= 60 ? 'buono' : v <= 75 ? 'medio' : 'debole';
  // Moduli della stazione meteo: rf_status piu' basso = segnale radio migliore (60 buono, 90 debole)
  const qualitaRadio = (v) => v === null || v === undefined ? '—' : v <= 70 ? 'buono' : v <= 85 ? 'medio' : 'debole';
  const TENDENZE = { up: 'in salita', down: 'in discesa', stable: 'stabile' };
  const oraMisura = (iso) => iso ? iso.slice(11, 16) : '—';

  function dato(etichetta, valore) {
    return `<div class="tp-dato"><div class="tp-dato-label">${escapeHtml(etichetta)}</div>
      <div class="tp-dato-valore">${valore}</div></div>`;
  }

  function messaggio(tipo, testo) { mostraMessaggio('msgDispositivi', tipo, testo); }

  function testoPausa(pausa) {
    return pausa ? ` Automazione in pausa per ${escapeHtml(pausa.zone.join(', '))} fino alle ${oraDaEpoch(pausa.fino)}.` : '';
  }

  function mostraErrori(errori) {
    const el = document.getElementById('erroriDispositivi');
    if (el) {
      el.innerHTML = (errori || []).length
        ? `<div class="alert alert-warning py-2 small mb-0">${errori.map(escapeHtml).join('<br>')}</div>` : '';
    }
  }

  function segnalaLettura(lettoAlle) {
    if (lettoAlle) segnalaAggiornamento(`Letto alle ${lettoAlle.slice(11, 16)}`, false);
  }

  // ── Casa Netatmo: modalita' e programma ────────────────────────────────
  function controlliCasa(casa, isAdmin) {
    const modo = casa.therm_mode || 'schedule';
    const fine = casa.therm_mode_endtime ? ` fino alle ${oraDaEpoch(casa.therm_mode_endtime)}` : '';
    const programmi = (casa.programmi || []).filter(p => p.tipo === 'therm');
    let html = `<div class="tp-controllo">
      <div><div class="tp-controllo-label">Modalità della casa</div>
        <div class="small tp-muted">${escapeHtml(testoModo(modo))}${fine}</div></div>
      <div class="d-flex flex-wrap gap-2 align-items-center">
        <select class="form-select form-select-sm w-auto" id="durataCasa" aria-label="Durata">
          <option value="">senza scadenza</option>
          ${DURATE.map(([m, t]) => `<option value="${m}">per ${t}</option>`).join('')}
        </select>
        <div class="tp-segmented" role="group" aria-label="Modalità della casa">
          ${MODI_CASA.map(([v, t, i]) => `<button type="button" class="${v === modo ? 'active' : ''}" data-casa-modo="${v}"><i class="bi bi-${i}"></i>${t}</button>`).join('')}
        </div>
      </div>
    </div>`;
    html += `<div class="tp-controllo"><div class="tp-controllo-label">Programma attivo</div>`;
    if (isAdmin && programmi.length > 1) {
      html += `<div class="d-flex gap-2">
        <select class="form-select form-select-sm w-auto" id="programmaCasa" aria-label="Programma">
          ${programmi.map(p => `<option value="${escapeHtml(p.id)}" ${p.selezionato ? 'selected' : ''}>${escapeHtml(p.nome)}</option>`).join('')}
        </select>
        <button type="button" class="btn btn-sm btn-outline-primary" data-programma-applica>Attiva</button>
      </div>`;
    } else {
      html += `<span>${escapeHtml(casa.programma_attivo || '—')}</span>`;
    }
    html += '</div>';
    return html;
  }

  async function inviaModoCasa(modo) {
    const durata = document.getElementById('durataCasa');
    const durataMin = durata && durata.value ? Number(durata.value) : null;
    try {
      await apiPostJson('/api/dispositivi/casa/modalita', { modalita: modo, durata_min: durataMin });
      messaggio('success', 'Modalità della casa aggiornata.');
    } catch (e) {
      messaggio('danger', escapeHtml(e.message));
    }
    setTimeout(ricarica, 1500);
  }

  async function inviaProgramma() {
    const sel = document.getElementById('programmaCasa');
    if (!sel || !confirm('Attivare il programma selezionato per tutta la casa?')) return;
    try {
      await apiPostJson('/api/dispositivi/casa/programma', { schedule_id: sel.value });
      messaggio('success', 'Programma attivato.');
    } catch (e) {
      messaggio('danger', escapeHtml(e.message));
    }
    setTimeout(ricarica, 1500);
  }

  // ── Elenco ─────────────────────────────────────────────────────────────
  function cardAc(ac) {
    const s = ac.stato || {};
    const stato = ac.errore ? '<span class="tp-badge-stato danger"><i class="bi bi-wifi-off"></i>non raggiungibile</span>'
      : s.acceso ? `<span class="tp-badge-stato ac"><i class="bi bi-power"></i>${escapeHtml(MODI_AC[s.modalita] || s.modalita || 'acceso')}</span>`
      : '<span class="tp-badge-stato"><i class="bi bi-power"></i>spento</span>';
    return `<div class="col-md-6 col-xl-4"><a class="tp-card d-block h-100 text-reset text-decoration-none" href="/dispositivi/ac/${enc(ac.id)}">
      <div class="d-flex justify-content-between align-items-start gap-2">
        <div class="min-w-0">
          <div class="fw-semibold text-truncate">${escapeHtml(ac.nome)}</div>
          <div class="mt-1">${stato}</div>
        </div>
        <div class="zona-temp">${gradi(s.temperatura_ambiente)}</div>
      </div>
      <div class="small tp-muted mt-3 d-flex flex-wrap gap-3">
        <span><i class="bi bi-bullseye"></i> ${gradi(s.setpoint_riscaldamento, 0)}</span>
        <span><i class="bi bi-droplet"></i> ${percento(s.umidita)}</span>
        <span><i class="bi bi-plug"></i> oggi ${kwh(ac.kwh_oggi)}</span>
        ${s.filtro_stato && s.filtro_stato !== 'normal' ? '<span class="text-danger"><i class="bi bi-funnel"></i> filtro</span>' : ''}
      </div>
      ${ac.zone && ac.zone.length ? `<div class="small tp-faint mt-2">Stanze: ${escapeHtml(ac.zone.join(', '))}</div>` : ''}
    </a></div>`;
  }

  function cardStanza(st) {
    const modulo = (st.moduli || [])[0] || {};
    const tag = [];
    if (st.finestra_aperta) tag.push('<span class="tp-badge-stato danger"><i class="bi bi-wind"></i>finestra aperta</span>');
    if (st.raggiungibile === false) tag.push('<span class="tp-badge-stato danger"><i class="bi bi-wifi-off"></i>non raggiungibile</span>');
    if (st.richiesta_calore_pct) tag.push(`<span class="tp-badge-stato gas"><i class="bi bi-fire"></i>richiesta ${percento(st.richiesta_calore_pct)}</span>`);
    if (st.anticipo) tag.push('<span class="tp-badge-stato"><i class="bi bi-clock-history"></i>preriscaldamento</span>');
    return `<div class="col-md-6 col-xl-4"><a class="tp-card d-block h-100 text-reset text-decoration-none" href="/dispositivi/stanza/${enc(st.id)}">
      <div class="d-flex justify-content-between align-items-start gap-2">
        <div class="min-w-0">
          <div class="fw-semibold text-truncate">${escapeHtml(st.nome)}</div>
          <div class="small tp-muted mt-1">${escapeHtml(testoModo(st.modalita))} · termostato ${gradi(st.setpoint)}${st.target !== null && st.target !== undefined ? ` · target ${gradi(st.target)}` : ''}</div>
        </div>
        <div class="zona-temp">${gradi(st.temperatura_attuale)}</div>
      </div>
      ${tag.length ? `<div class="tp-list-tags">${tag.join('')}</div>` : ''}
      ${st.errore ? `<div class="small text-danger mt-2">${escapeHtml(st.errore)}</div>` : ''}
      <div class="small tp-muted mt-3 d-flex flex-wrap gap-3">
        <span><i class="bi bi-droplet"></i> ${percento(st.umidita)}</span>
        <span><i class="bi bi-wifi"></i> ${qualitaWifi(modulo.wifi)}</span>
        ${modulo.firmware !== undefined && modulo.firmware !== null ? `<span><i class="bi bi-cpu"></i> fw ${escapeHtml(modulo.firmware)}</span>` : ''}
      </div>
      ${st.zone && st.zone.length ? `<div class="small tp-faint mt-2">Stanze: ${escapeHtml(st.zone.join(', '))}</div>` : ''}
    </a></div>`;
  }

  function cardMeteo(m) {
    const tag = [];
    if (m.in_uso) tag.push('<span class="tp-badge-stato ac"><i class="bi bi-check2-circle"></i>in uso per i calcoli</span>');
    if (m.raggiungibile === false) tag.push('<span class="tp-badge-stato danger"><i class="bi bi-wifi-off"></i>non raggiungibile</span>');
    if (m.batteria_pct !== null && m.batteria_pct !== undefined && m.batteria_pct < 20) {
      tag.push(`<span class="tp-badge-stato danger"><i class="bi bi-battery"></i>batteria ${percento(m.batteria_pct)}</span>`);
    }
    return `<div class="col-md-6 col-xl-4"><a class="tp-card d-block h-100 text-reset text-decoration-none" href="/dispositivi/meteo/${enc(m.id)}">
      <div class="d-flex justify-content-between align-items-start gap-2">
        <div class="min-w-0">
          <div class="fw-semibold text-truncate">${escapeHtml(m.nome)}</div>
          <div class="small tp-muted mt-1">${escapeHtml(m.stazione || '')} · misura delle ${oraMisura(m.ora_misura)}</div>
        </div>
        <div class="zona-temp">${gradi(m.temperatura)}</div>
      </div>
      ${tag.length ? `<div class="tp-list-tags">${tag.join('')}</div>` : ''}
      <div class="small tp-muted mt-3 d-flex flex-wrap gap-3">
        <span><i class="bi bi-droplet"></i> ${percento(m.umidita)}</span>
        <span><i class="bi bi-arrow-down-up"></i> ${gradi(m.minima)} / ${gradi(m.massima)}</span>
        <span><i class="bi bi-battery-half"></i> ${percento(m.batteria_pct)}</span>
      </div>
    </a></div>`;
  }

  async function caricaElenco() {
    try {
      const dati = await apiGetJson('/api/dispositivi/stato');
      mostraErrori(dati.errori);
      segnalaLettura(dati.letto_alle);
      const vuotoAc = '<div class="col-12"><div class="tp-card tp-muted small">Nessun condizionatore: collega SmartThings in Impostazioni → Credenziali API.</div></div>';
      const vuotoSt = '<div class="col-12"><div class="tp-card tp-muted small">Nessun termostato: collega Netatmo e scegli l\'impianto in Impostazioni → Credenziali API.</div></div>';
      document.getElementById('listaAc').innerHTML = dati.ac.length ? dati.ac.map(cardAc).join('') : vuotoAc;
      document.getElementById('listaStanze').innerHTML = dati.stanze.length ? dati.stanze.map(cardStanza).join('') : vuotoSt;
      // Funzione in piu': la sezione c'e' solo se Netatmo ha una stazione meteo
      const meteo = dati.meteo || [];
      document.getElementById('listaMeteo').innerHTML = meteo.map(cardMeteo).join('');
      document.getElementById('sezioneMeteo').style.display = meteo.length ? '' : 'none';
      const casaCard = document.getElementById('casaCard');
      if (dati.casa) {
        casaCard.style.display = '';
        document.getElementById('casaNome').textContent = dati.casa.name || 'Casa';
        const modoClima = dati.casa.temperature_control_mode === 'cooling' ? 'raffrescamento' : 'riscaldamento';
        document.getElementById('casaSub').textContent =
          `Netatmo · impianto in ${modoClima} · ora locale ${dati.casa.ora_locale || '—'}`;
        document.getElementById('casaLink').href = `/dispositivi/casa/${enc(dati.casa.id)}`;
        document.getElementById('casaControlli').innerHTML = controlliCasa(dati.casa, !!window.utenteAdmin);
      } else {
        casaCard.style.display = 'none';
      }
    } catch (e) {
      mostraErrori([`Dispositivi non disponibili: ${e.message}`]);
    }
  }

  // ── Dettaglio ──────────────────────────────────────────────────────────
  const pagina = document.getElementById('paginaDispositivo');
  const tipo = pagina ? pagina.dataset.tipo : null;
  const ident = pagina ? pagina.dataset.id : null;
  let ultimoDettaglio = null;

  function zoneCollegate(zone) {
    if (!zone || !zone.length) return '';
    return `<div class="tp-list">${zone.map(z => {
      const d = z.decisione || {};
      const stato = !z.automazione ? 'esclusa' : (d.stato === 'esclusa' ? null : d.stato);
      return `<a class="tp-list-item" href="/stanze/${enc(z.room_id)}">
        <span class="tp-list-icon"><i class="bi bi-door-open"></i></span>
        <div class="tp-list-body">
          <div class="tp-list-title">Stanza ${escapeHtml(z.nome)}</div>
          <div class="tp-list-sub">${escapeHtml(!z.automazione ? 'Esclusa dall\'automazione'
            : (!stato ? 'Aggiornamento in corso…' : d.motivo || 'In attesa del prossimo controllo'))}</div>
        </div>
        ${stato ? badgeStatoZona(stato, d.simulazione) : ''}
      </a>`;
    }).join('')}</div>`;
  }

  function datiPrincipali(d) {
    const s = d.stato || {};
    if (tipo === 'meteo') return datiMeteo(s);
    if (tipo === 'ac') {
      const filtro = s.filtro_uso_h !== null && s.filtro_uso_h !== undefined
        ? `${s.filtro_uso_h} / ${s.filtro_capacita_h || '?'} h` : '—';
      return [
        dato('Stato', s.acceso ? escapeHtml(MODI_AC[s.modalita] || s.modalita || 'acceso') : 'spento'),
        dato('Temperatura', gradi(s.temperatura_ambiente)),
        dato('Impostata', gradi(s.setpoint_riscaldamento, 0)),
        dato('Umidità', percento(s.umidita)),
        dato('Ventola', escapeHtml(s.ventola || '—')),
        dato('Modalità speciale', escapeHtml(s.modalita_opzionale || '—')),
        dato('Contatore energia', s.energia_wh !== null && s.energia_wh !== undefined ? `${(s.energia_wh / 1000).toFixed(1)} kWh` : '—'),
        dato('Potenza', s.potenza_w !== null && s.potenza_w !== undefined ? `${s.potenza_w} W` : '—'),
        dato('Filtro', `${filtro}${s.filtro_stato && s.filtro_stato !== 'normal' ? ' <span class="text-danger">da pulire</span>' : ''}`),
      ].join('');
    }
    if (tipo === 'stanza') {
      const moduli = d.moduli || [];
      return [
        ...(s.errore ? [dato('Termostato', `<span class="text-danger">${escapeHtml(s.errore)}</span>`)] : []),
        dato('Temperatura', gradi(s.temperatura_attuale)),
        dato('Termostato', gradi(s.setpoint)),
        dato('Target (programma)', gradi(s.target)),
        dato('Modalità', escapeHtml(testoModo(s.modalita)) + (s.setpoint_fine ? ` <small class="tp-muted">fino alle ${oraDaEpoch(s.setpoint_fine)}</small>` : '')),
        dato('Umidità', percento(s.umidita)),
        dato('Richiesta di calore', percento(s.richiesta_calore_pct)),
        dato('Caldaia', s.caldaia_accesa ? '<span class="tp-gas">accesa</span>' : 'spenta'),
        dato('Finestra', s.finestra_aperta ? '<span class="text-danger">aperta</span>' : 'chiusa'),
        ...moduli.flatMap(m => [
          dato(`Modulo ${m.tipo || ''} · WiFi`, qualitaWifi(m.wifi)),
          dato(`Modulo ${m.tipo || ''} · firmware`, escapeHtml(m.firmware ?? '—')),
        ]),
      ].join('');
    }
    return [
      dato('Modalità', escapeHtml(testoModo(s.therm_mode))),
      dato('Impianto', s.temperature_control_mode === 'cooling' ? 'raffrescamento' : 'riscaldamento'),
      dato('Raffrescamento', escapeHtml(testoModo(s.cooling_mode))),
      dato('Programma', escapeHtml(s.programma_attivo || '—')),
      dato('Durata manuale', s.therm_setpoint_default_duration ? `${s.therm_setpoint_default_duration} min` : '—'),
      dato('Ora locale', escapeHtml(s.ora_locale || '—')),
    ].join('');
  }

  function datiMeteo(s) {
    return [
      ...(s.errore ? [dato('Modulo', `<span class="text-danger">${escapeHtml(s.errore)}</span>`)] : []),
      dato('Temperatura', gradi(s.temperatura)),
      dato('Umidità', percento(s.umidita)),
      dato('Minima / massima oggi', `${gradi(s.minima)} / ${gradi(s.massima)}`),
      dato('Tendenza', escapeHtml(TENDENZE[s.tendenza] || s.tendenza || '—')),
      dato('Misura delle', escapeHtml(oraMisura(s.ora_misura))),
      dato('Batteria', percento(s.batteria_pct)),
      dato('Segnale radio', qualitaRadio(s.segnale_radio)),
      dato('Stazione', escapeHtml(s.stazione || '—')),
      dato('Temperatura esterna', s.in_uso ? 'in uso per i calcoli' : 'non in uso'),
    ].join('');
  }

  // Controlli AC generati dalla whitelist del server (valori ammessi compresi)
  function controlloAc(c) {
    const attr = `data-ac-chiave="${escapeHtml(c.chiave)}"`;
    let input;
    if (c.tipo === 'switch') {
      input = `<div class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" role="switch"
        ${attr} data-tipo-controllo="switch" ${c.valore === 'on' ? 'checked' : ''} aria-label="${escapeHtml(c.etichetta)}"></div>`;
    } else if (c.tipo === 'enum') {
      input = `<div class="tp-segmented" role="group" aria-label="${escapeHtml(c.etichetta)}">${c.valori.map(v => {
        const t = String(v);
        return `<button type="button" class="${t === String(c.valore) ? 'active' : ''}" ${attr} data-valore="${escapeHtml(t)}">${escapeHtml(c.etichette[t] || t)}${c.unita ? ' ' + escapeHtml(c.unita) : ''}</button>`;
      }).join('')}</div>`;
    } else if (c.tipo === 'numero') {
      const valore = c.valore ?? c.minimo;
      input = `<div class="tp-stepper" data-min="${c.minimo}" data-max="${c.massimo}" data-passo="${c.passo}">
        <button type="button" class="btn btn-sm btn-outline-secondary" data-passo-dir="-1" aria-label="Diminuisci">−</button>
        <output>${valore}</output><span class="small tp-muted">${escapeHtml(c.unita || '')}</span>
        <button type="button" class="btn btn-sm btn-outline-secondary" data-passo-dir="1" aria-label="Aumenta">+</button>
        <button type="button" class="btn btn-sm btn-primary" ${attr} data-applica-stepper>Imposta</button>
      </div>`;
    } else {
      input = `<button type="button" class="btn btn-sm btn-outline-danger" ${attr} data-azione
        data-conferma="${escapeHtml(c.conferma || '')}">${escapeHtml(c.etichetta)}</button>`;
    }
    return `<div class="tp-controllo">
      <div class="tp-controllo-label">${escapeHtml(c.etichetta)}${c.solo_admin ? ' <span class="tp-badge-stato">admin</span>' : ''}</div>
      ${input}
    </div>`;
  }

  // Modalita' e programma valgono per tutta la casa: dal termostato si va alla pagina della casa
  function rigaCasa(casa) {
    if (!casa || !casa.id) return '';
    return `<div class="tp-controllo">
      <div><div class="tp-controllo-label">Casa</div>
        <div class="small tp-muted">Modalità ${escapeHtml(testoModo(casa.therm_mode))}${casa.programma_attivo ? ` · programma ${escapeHtml(casa.programma_attivo)}` : ''}</div></div>
      <a class="btn btn-sm btn-outline-secondary" href="/dispositivi/casa/${enc(casa.id)}"><i class="bi bi-house-gear me-1"></i>Modalità e programma <i class="bi bi-chevron-right"></i></a>
    </div>`;
  }

  function controlliStanza(s, casa) {
    if (s.raggiungibile === false) {
      return `<div class="small text-danger"><i class="bi bi-wifi-off me-1"></i>${escapeHtml(s.errore || 'Termostato non raggiungibile')}: comandi non disponibili.</div>${rigaCasa(casa)}`;
    }
    const valore = s.setpoint ?? s.target ?? 20;
    return `<div class="tp-controllo">
      <div><div class="tp-controllo-label">Temperatura manuale</div>
        <div class="small tp-muted">Poi il termostato torna da solo al programma</div></div>
      <div class="d-flex flex-wrap align-items-center gap-2">
        <div class="tp-stepper" data-min="5" data-max="30" data-passo="0.5">
          <button type="button" class="btn btn-sm btn-outline-secondary" data-passo-dir="-1" aria-label="Diminuisci">−</button>
          <output>${valore}</output><span class="small tp-muted">°C</span>
          <button type="button" class="btn btn-sm btn-outline-secondary" data-passo-dir="1" aria-label="Aumenta">+</button>
        </div>
        <select class="form-select form-select-sm w-auto" id="durataStanza" aria-label="Durata">
          ${DURATE.map(([m, t]) => `<option value="${m}" ${m === 180 ? 'selected' : ''}>per ${t}</option>`).join('')}
        </select>
        <button type="button" class="btn btn-sm btn-primary" data-stanza-setpoint>Imposta</button>
      </div>
    </div>
    <div class="tp-controllo">
      <div><div class="tp-controllo-label">Boost</div>
        <div class="small tp-muted">Termostato al massimo, poi di nuovo il programma</div></div>
      <div class="d-flex flex-wrap align-items-center gap-2">
        <select class="form-select form-select-sm w-auto" id="durataBoost" aria-label="Durata del boost">
          ${[[15, '15 min'], [30, '30 min'], [60, '1 h'], [120, '2 h']].map(([m, t]) => `<option value="${m}" ${m === 30 ? 'selected' : ''}>per ${t}</option>`).join('')}
        </select>
        <button type="button" class="btn btn-sm btn-outline-danger" data-stanza-boost><i class="bi bi-fire me-1"></i>Boost</button>
      </div>
    </div>
    <div class="tp-controllo">
      <div class="tp-controllo-label">Programma</div>
      <button type="button" class="btn btn-sm btn-outline-secondary" data-stanza-ripristina ${s.modalita === 'home' ? 'disabled' : ''}>
        <i class="bi bi-calendar-week me-1"></i>Torna al programma</button>
    </div>${rigaCasa(casa)}`;
  }

  // ── Tutti i valori ─────────────────────────────────────────────────────
  function formatta(chiave, v) {
    if (v === null || v === undefined) return 'null';
    let testo = typeof v === 'object' ? JSON.stringify(v) : String(v);
    // Epoch di Netatmo: aggiunge data e ora leggibili
    if (typeof v === 'number' && v > 1e9 && v < 1e10 && /time|endtime|date/i.test(chiave)) {
      testo += ` (${new Date(v * 1000).toLocaleString('it-IT', { dateStyle: 'short', timeStyle: 'short' })})`;
    }
    return escapeHtml(testo);
  }

  function righe(oggetto) {
    return Object.keys(oggetto || {}).sort().map(k => {
      const v = oggetto[k];
      return `<tr class="${vuoto(v) ? 'vuoto' : ''}"><td>${escapeHtml(k)}</td><td>${formatta(k, v)}</td></tr>`;
    }).join('');
  }

  function gruppo(titolo, contenuto) {
    return `<div class="tp-valori-gruppo">${escapeHtml(titolo)}</div><table class="tp-valori"><tbody>${contenuto}</tbody></table>`;
  }

  function tuttiValori(d) {
    const g = d.grezzo || {};
    if (tipo === 'ac') {
      return Object.keys(g).sort().map(cap => {
        const attributi = g[cap] || {};
        const html = Object.keys(attributi).sort().map(a => {
          const voce = attributi[a] || {};
          const unita = voce.unit ? ` ${voce.unit}` : '';
          return `<tr class="${vuoto(voce.value) ? 'vuoto' : ''}"><td>${escapeHtml(a)}</td><td>${formatta(a, voce.value)}${escapeHtml(unita)}</td></tr>`;
        }).join('');
        const tuttiVuoti = Object.values(attributi).every(v => vuoto((v || {}).value));
        return `<div class="${tuttiVuoti ? 'gruppo-vuoto' : ''}">${gruppo(cap, html)}</div>`;
      }).join('') + (d.ocf ? gruppo('ocf (dispositivo)', righe(d.ocf)) : '');
    }
    if (tipo === 'meteo') {
      const modulo = Object.fromEntries(Object.entries(g).filter(([k]) => k !== 'dashboard_data'));
      return gruppo('Misure', righe(g.dashboard_data)) + gruppo('Modulo', righe(modulo));
    }
    if (tipo === 'stanza') {
      return gruppo('Stanza', righe(g.stanza)) + (g.moduli || []).map((m, i) => gruppo(`Modulo ${i + 1}`, righe(m))).join('');
    }
    const dati = g.dati || {};
    const casa = Object.fromEntries(Object.entries(dati).filter(([k]) => !['rooms', 'modules', 'schedules'].includes(k)));
    const programmi = Object.fromEntries((dati.schedules || []).map(s => [s.name || s.id, s]));
    const stanze = Object.fromEntries((dati.rooms || []).map(r => [r.name || r.id, r]));
    const moduli = Object.fromEntries((dati.modules || []).map(m => [m.name || m.id, m]));
    return gruppo('Casa', righe(casa)) + gruppo('Stato', righe(g.stato)) + gruppo('Programmi', righe(programmi))
      + gruppo('Stanze', righe(stanze)) + gruppo('Moduli', righe(moduli));
  }

  function aggiornaVuoti() {
    const mostra = document.getElementById('mostraVuoti').checked;
    document.querySelectorAll('.tp-valori').forEach(t => t.classList.toggle('mostra-vuoti', mostra));
    document.querySelectorAll('.gruppo-vuoto').forEach(el => { el.style.display = mostra ? '' : 'none'; });
  }

  async function caricaDettaglio() {
    try {
      const res = await fetch(`/api/dispositivi/${enc(tipo)}/${enc(ident)}`);
      const d = await res.json();
      ultimoDettaglio = d;
      mostraErrori(d.errori);
      segnalaLettura(d.letto_alle);
      if (!res.ok) {
        document.getElementById('nomeDispositivo').textContent = d.errore || 'Non trovato';
        const controlli = document.getElementById('controlli');
        if (controlli) controlli.innerHTML = '';
        document.getElementById('tuttiValori').innerHTML = '';
        return;
      }
      document.title = `${d.nome} · TermoPilota`;
      document.getElementById('nomeDispositivo').textContent = d.nome;
      document.getElementById('sottotitoloDispositivo').textContent =
        { ac: 'Condizionatore Samsung (SmartThings)', stanza: 'Stanza Netatmo', meteo: 'Sensore della stazione meteo Netatmo' }[tipo]
        || 'Casa Netatmo';
      document.getElementById('lettoDispositivo').innerHTML = etichettaLettura(d.letto_alle);
      document.getElementById('datiPrincipali').innerHTML = datiPrincipali(d);
      document.getElementById('zoneCollegate').innerHTML = zoneCollegate(d.zone);
      const controlli = document.getElementById('controlli');
      if (!controlli) {
        // stazione meteo: sola lettura
      } else if (tipo === 'ac') {
        controlli.innerHTML = d.controlli && d.controlli.length ? d.controlli.map(controlloAc).join('')
          : '<div class="tp-muted small">Nessun controllo disponibile per questo dispositivo.</div>';
      } else if (tipo === 'stanza') {
        controlli.innerHTML = controlliStanza(d.stato || {}, d.casa);
      } else {
        controlli.innerHTML = controlliCasa(d.stato || {}, d.is_admin);
      }
      document.getElementById('tuttiValori').innerHTML = tuttiValori(d);
      aggiornaVuoti();
    } catch (e) {
      mostraErrori([`Dispositivo non disponibile: ${e.message}`]);
    }
  }

  // ── Grafici del dettaglio ──────────────────────────────────────────────
  const grafici = {};
  let intervallo = '24h';

  function periodoDa(intervallo) {
    const oggi = giornoLocale();
    if (intervallo === '7g') return { da: giornoLocale(new Date(Date.now() - 6 * 86400000)), a: oggi, risoluzione: 'oraria' };
    if (intervallo === '30g') return { da: giornoLocale(new Date(Date.now() - 29 * 86400000)), a: oggi, risoluzione: 'giornaliera' };
    return { da: giornoLocale(new Date(Date.now() - 86400000)), a: oggi, risoluzione: 'grezza' };
  }

  function etichetta(periodo, risoluzione) {
    if (risoluzione === 'grezza') return periodo.slice(11, 16);
    const base = `${periodo.slice(8, 10)}/${periodo.slice(5, 7)}`;
    return periodo.length > 10 ? `${base} ${periodo.slice(11, 13)}:00` : base;
  }

  function nuovoGrafico(id, config, legenda) {
    if (grafici[id]) grafici[id].destroy();
    const canvas = document.getElementById(id);
    if (!canvas || typeof Chart === 'undefined') return;
    grafici[id] = new Chart(canvas, config);
    if (legenda) TPGrafici.legenda(legenda, grafici[id]);
  }

  async function caricaGrafici() {
    if (typeof TPGrafici === 'undefined') return;
    const G = TPGrafici;
    const { da, a, risoluzione } = periodoDa(intervallo);
    let dati;
    try {
      dati = await apiGetJson(`/api/dispositivi/${enc(tipo)}/${enc(ident)}/storico?da=${da}&a=${a}&risoluzione=${risoluzione}`);
    } catch (e) {
      return;
    }
    let punti = dati.punti;
    if (risoluzione === 'grezza') {
      const limite = new Date(Date.now() - 86400000);
      const p = (n) => String(n).padStart(2, '0');
      const soglia = `${giornoLocale(limite)}T${p(limite.getHours())}:${p(limite.getMinutes())}`;
      punti = punti.filter(x => x.periodo >= soglia);
    }
    document.getElementById('graficiVuoti').style.display = punti.length ? 'none' : '';
    const labels = punti.map(x => etichetta(x.periodo, risoluzione));
    const linea = (chiave, extra = {}) => ({
      borderColor: G.colore(chiave), backgroundColor: G.gradiente(chiave, 0.15),
      pointHoverBackgroundColor: G.colore(chiave), pointHoverBorderColor: G.colore('pannello'), ...extra,
    });
    const maxTickX = G.mobile() ? 5 : 8;

    const colore = tipo === 'ac' ? 'ac' : 'gas';
    const datasets = [
      { label: 'Temperatura', coloreVar: tipo === 'ac' ? '--ac-color' : '--gas-color',
        data: punti.map(x => x.t_ambiente), ...linea(colore, { fill: 'start' }) },
      { label: tipo === 'ac' ? 'Impostata' : 'Termostato', coloreVar: '--chart-neutral',
        data: punti.map(x => x.setpoint), ...linea('neutro', { stepped: true, borderDash: [5, 4], backgroundColor: 'transparent' }) },
    ];
    if (tipo === 'meteo') {
      // Il modulo esterno, con la stazione CFR per confronto (stesse ore di lettura)
      datasets.length = 1;
      datasets[0].label = 'Netatmo';
      if (dati.cfr && dati.cfr.length) {
        const cfr = Object.fromEntries(dati.cfr.map(x => [x.periodo, x.t_ambiente]));
        datasets.push({ label: 'CFR', coloreVar: '--chart-neutral', data: punti.map(x => cfr[x.periodo] ?? null),
                        ...linea('neutro', { borderDash: [5, 4], backgroundColor: 'transparent', spanGaps: true }) });
      }
    }
    if (tipo === 'stanza' && risoluzione === 'grezza') {
      datasets.push({ label: 'Target', coloreVar: '--green', data: punti.map(x => (x.extra || {}).target ?? null),
                      ...linea('verde', { stepped: true, borderDash: [2, 3], backgroundColor: 'transparent' }) });
    }
    const opzT = G.opzioniBase({ unitaY: '°C', tickY: v => `${v.toFixed(0)}°`, maxTickX });
    opzT.plugins.tooltip = { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y === null ? '—' : ctx.parsed.y.toFixed(1) + '°C'}` } };
    nuovoGrafico('graficoTemperatureDispositivo', { type: 'line', data: { labels, datasets }, options: opzT }, 'legendaTemperature');

    // Attivita': kWh per gli AC, richiesta di calore per le stanze
    const opzA = G.opzioniBase({ maxTickX });
    opzA.plugins.mirino = false;
    const barre = (chiave) => ({ backgroundColor: G.colore(chiave), hoverBackgroundColor: G.colore(chiave), borderRadius: 3, maxBarThickness: 24 });
    if (tipo === 'meteo') {
      // nessun grafico di attivita' per un sensore
    } else if (tipo === 'ac') {
      document.getElementById('titoloAttivita').innerHTML = '<i class="bi bi-plug"></i>Consumo';
      document.getElementById('sottotitoloAttivita').textContent = 'kWh elettrici dal contatore del condizionatore';
      const energia = risoluzione === 'grezza' ? (dati.energia || []) : punti.map(x => ({ periodo: x.periodo, kwh: x.kwh }));
      opzA.scales.y.ticks.callback = v => `${v} kWh`;
      opzA.plugins.tooltip = { callbacks: { label: ctx => ` ${ctx.parsed.y.toFixed(2)} kWh` } };
      nuovoGrafico('graficoAttivita', {
        type: 'bar',
        data: { labels: energia.map(e => etichetta(e.periodo, risoluzione === 'grezza' ? 'oraria' : risoluzione)),
                datasets: [{ label: 'Consumo', data: energia.map(e => e.kwh), ...barre('ac') }] },
        options: opzA,
      });
    } else {
      document.getElementById('titoloAttivita').innerHTML = '<i class="bi bi-fire"></i>Richiesta di calore';
      document.getElementById('sottotitoloAttivita').textContent = risoluzione === 'grezza'
        ? '% di apertura chiesta dal termostato' : '% del tempo con richiesta di calore';
      opzA.scales.y.max = 100;
      opzA.scales.y.ticks.callback = v => `${v}%`;
      opzA.plugins.tooltip = { callbacks: { label: ctx => ` ${Math.round(ctx.parsed.y)}%` } };
      nuovoGrafico('graficoAttivita', {
        type: 'bar',
        data: { labels, datasets: [{ label: 'Richiesta',
          data: punti.map(x => risoluzione === 'grezza' ? ((x.extra || {}).richiesta_calore_pct ?? 0) : x.attivo_pct),
          ...barre('gas') }] },
        options: opzA,
      });
    }

    const opzU = G.opzioniBase({ tickY: v => `${v}%`, maxTickX });
    opzU.plugins.tooltip = { callbacks: { label: ctx => ` ${Math.round(ctx.parsed.y)}%` } };
    nuovoGrafico('graficoUmidita', {
      type: 'line',
      data: { labels, datasets: [{ label: 'Umidità', data: punti.map(x => x.umidita), ...linea('neutro', { fill: 'start' }) }] },
      options: opzU,
    });
  }

  // ── Comandi ────────────────────────────────────────────────────────────
  async function inviaAc(chiave, valore) {
    try {
      const r = await apiPostJson(`/api/dispositivi/ac/${enc(ident)}/comando`, { chiave, valore });
      messaggio('success', `Comando inviato.${testoPausa(r.pausa)}`);
      setTimeout(caricaDettaglio, 2500);
    } catch (e) {
      messaggio('danger', escapeHtml(e.message));
      caricaDettaglio();
    }
  }

  async function inviaStanza(azione, corpo) {
    try {
      const r = await apiPostJson(`/api/dispositivi/stanza/${enc(ident)}/${azione}`, corpo);
      const fine = r.fine ? ` fino alle ${oraDaEpoch(r.fine)}` : '';
      const testo = { setpoint: 'Temperatura impostata' + fine, boost: 'Boost attivo' + fine }[azione] || 'Stanza tornata al programma';
      messaggio('success', `${testo}.${testoPausa(r.pausa)}`);
    } catch (e) {
      messaggio('danger', escapeHtml(e.message));
    }
    setTimeout(caricaDettaglio, 1500);
  }

  // ── Comandi avanzati (admin): generati dallo schema delle definizioni ──
  let comandiAvanzati = null;

  function inputArgomento(a, i) {
    const etichetta = `${escapeHtml(a.nome)}${a.opzionale ? ' <span class="tp-faint">(opz.)</span>' : ''}`;
    const vuota = a.opzionale ? '<option value="">—</option>' : '';
    let campo;
    if (a.enum) {
      campo = `<select class="form-select form-select-sm" data-arg="${i}" data-tipo="${a.tipo}">
        ${vuota}${a.enum.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('')}</select>`;
    } else if (a.tipo === 'boolean') {
      campo = `<select class="form-select form-select-sm" data-arg="${i}" data-tipo="boolean">
        ${vuota}<option value="true">true</option><option value="false">false</option></select>`;
    } else if (a.tipo === 'integer' || a.tipo === 'number') {
      const intervallo = a.minimo !== undefined || a.massimo !== undefined ? `${a.minimo ?? ''}–${a.massimo ?? ''}` : '';
      campo = `<input type="number" class="form-control form-control-sm" data-arg="${i}" data-tipo="${a.tipo}"
        ${a.minimo !== undefined ? `min="${a.minimo}"` : ''} ${a.massimo !== undefined ? `max="${a.massimo}"` : ''}
        step="${a.tipo === 'integer' ? 1 : 'any'}" placeholder="${intervallo}">`;
    } else {
      campo = `<input type="text" class="form-control form-control-sm" data-arg="${i}" data-tipo="string" maxlength="100">`;
    }
    return `<label class="small tp-muted d-flex flex-column gap-1">${etichetta}${campo}</label>`;
  }

  function rigaAvanzata(c) {
    return `<div class="tp-avanzato" data-avanzato data-capability="${escapeHtml(c.capability)}" data-comando="${escapeHtml(c.comando)}">
      <code class="small">${escapeHtml(c.comando)}</code>
      <div class="tp-avanzato-arg">${c.argomenti.map(inputArgomento).join('')}</div>
      <button type="button" class="btn btn-sm btn-outline-primary" data-avanzato-invia>Invia</button>
    </div>`;
  }

  async function caricaAvanzati() {
    const box = document.getElementById('comandiAvanzati');
    if (!box || comandiAvanzati) return;
    box.innerHTML = '<div class="tp-muted small">Lettura delle definizioni…</div>';
    try {
      comandiAvanzati = (await apiGetJson(`/api/dispositivi/ac/${enc(ident)}/avanzati`)).comandi;
    } catch (e) {
      box.innerHTML = `<div class="text-danger small">${escapeHtml(e.message)}</div>`;
      return;
    }
    const gruppi = {};
    comandiAvanzati.forEach(c => { (gruppi[c.capability] = gruppi[c.capability] || []).push(c); });
    box.innerHTML = Object.keys(gruppi).length ? Object.entries(gruppi).map(([cap, cmd]) =>
      `<div class="tp-valori-gruppo">${escapeHtml(cap)}</div>${cmd.map(rigaAvanzata).join('')}`).join('')
      : '<div class="tp-muted small">Nessun comando disponibile.</div>';
  }

  async function inviaAvanzato(riga) {
    const argomenti = [...riga.querySelectorAll('[data-arg]')].map(el => {
      if (el.value === '') return null;
      if (el.dataset.tipo === 'boolean') return el.value === 'true';
      if (el.dataset.tipo === 'integer' || el.dataset.tipo === 'number') return Number(el.value);
      return el.value;
    });
    while (argomenti.length && argomenti[argomenti.length - 1] === null) argomenti.pop();
    const { capability, comando } = riga.dataset;
    if (!confirm(`Inviare ${capability}.${comando}(${argomenti.map(a => JSON.stringify(a)).join(', ')})?`)) return;
    try {
      const r = await apiPostJson(`/api/dispositivi/ac/${enc(ident)}/avanzato`, { capability, comando, argomenti });
      mostraMessaggio('msgAvanzati', 'success', `Comando inviato.${testoPausa(r.pausa)}`);
      setTimeout(caricaDettaglio, 2500);
    } catch (e) {
      mostraMessaggio('msgAvanzati', 'danger', escapeHtml(e.message));
    }
  }

  const sezioneAvanzati = document.getElementById('sezioneAvanzati');
  if (sezioneAvanzati) sezioneAvanzati.addEventListener('toggle', () => { if (sezioneAvanzati.open) caricaAvanzati(); });

  document.addEventListener('click', (e) => {
    const passo = e.target.closest('[data-passo-dir]');
    if (passo) {
      const box = passo.closest('.tp-stepper');
      const out = box.querySelector('output');
      const p = Number(box.dataset.passo);
      const nuovo = Math.min(Number(box.dataset.max), Math.max(Number(box.dataset.min),
        Number(out.textContent) + p * Number(passo.dataset.passoDir)));
      out.textContent = String(Math.round(nuovo * 100) / 100);
      return;
    }
    const applica = e.target.closest('[data-applica-stepper]');
    if (applica) {
      inviaAc(applica.dataset.acChiave, Number(applica.closest('.tp-stepper').querySelector('output').textContent));
      return;
    }
    const azione = e.target.closest('[data-azione]');
    if (azione) {
      if (azione.dataset.conferma && !confirm(azione.dataset.conferma)) return;
      inviaAc(azione.dataset.acChiave, null);
      return;
    }
    const scelta = e.target.closest('button[data-ac-chiave][data-valore]');
    if (scelta) {
      inviaAc(scelta.dataset.acChiave, scelta.dataset.valore);
      return;
    }
    if (e.target.closest('[data-stanza-setpoint]')) {
      const box = document.querySelector('#controlli .tp-stepper');
      inviaStanza('setpoint', { temp: Number(box.querySelector('output').textContent),
                                durata_min: Number(document.getElementById('durataStanza').value) });
      return;
    }
    if (e.target.closest('[data-stanza-boost]')) {
      inviaStanza('boost', { durata_min: Number(document.getElementById('durataBoost').value) });
      return;
    }
    const avanzato = e.target.closest('[data-avanzato-invia]');
    if (avanzato) {
      inviaAvanzato(avanzato.closest('[data-avanzato]'));
      return;
    }
    if (e.target.closest('[data-stanza-ripristina]')) {
      inviaStanza('ripristina', {});
      return;
    }
    const modo = e.target.closest('[data-casa-modo]');
    if (modo) {
      inviaModoCasa(modo.dataset.casaModo);
      return;
    }
    if (e.target.closest('[data-programma-applica]')) inviaProgramma();
  });

  document.addEventListener('change', (e) => {
    if (e.target.matches('[data-tipo-controllo="switch"]')) {
      inviaAc(e.target.dataset.acChiave, e.target.checked ? 'on' : 'off');
    } else if (e.target.id === 'mostraVuoti') {
      aggiornaVuoti();
    }
  });

  const copia = document.getElementById('copiaJson');
  if (copia) {
    copia.addEventListener('click', async () => {
      if (!ultimoDettaglio) return;
      try {
        await navigator.clipboard.writeText(JSON.stringify(ultimoDettaglio.grezzo, null, 2));
        copia.innerHTML = '<i class="bi bi-check2 me-1"></i>Copiato';
      } catch (e) {
        copia.innerHTML = '<i class="bi bi-x me-1"></i>Non copiato';
      }
      setTimeout(() => { copia.innerHTML = '<i class="bi bi-clipboard me-1"></i>Copia JSON'; }, 2000);
    });
  }

  document.querySelectorAll('[data-intervallo]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-intervallo]').forEach(b => {
        b.classList.toggle('active', b === btn);
        b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
      });
      intervallo = btn.dataset.intervallo;
      caricaGrafici();
    });
  });

  function ricarica() {
    if (pagina) caricaDettaglio(); else caricaElenco();
  }

  if (pagina) {
    caricaDettaglio();
    caricaGrafici();
    ogni(60 * 1000, caricaDettaglio);
    ogni(15 * 60 * 1000, caricaGrafici);
    ascoltaLive(caricaDettaglio);
  } else if (document.getElementById('paginaDispositivi')) {
    caricaElenco();
    ogni(60 * 1000, caricaElenco);
    ascoltaLive(caricaElenco);
  }
})();
