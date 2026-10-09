// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — pagina della stanza: termostato e condizionatore insieme, cosa fa
// l'automazione (con il calcolo dei setpoint), comandi, grafico combinato,
// registro e impostazioni (admin).

(function () {
  const pagina = document.getElementById('paginaStanza');
  if (!pagina) return;
  let roomId = pagina.dataset.room;
  const enc = encodeURIComponent;
  const MODI_STANZA = { manual: 'manuale', home: 'programma', max: 'boost', off: 'spento', hg: 'antigelo', away: 'assente' };
  const MODI_AC = { heat: 'riscaldamento', cool: 'raffrescamento', dry: 'deumidificazione', fan: 'ventilazione',
                    auto: 'automatico', wind: 'ventilazione' };
  const DURATE = [[60, '1 h'], [180, '3 h'], [480, '8 h'], [1440, '24 h']];
  let dati = null;

  const num = (v, d = 1) => (v === null || v === undefined) ? '—' : Number(v).toFixed(d).replace('.', ',');
  const segno = (v) => (v > 0 ? '+' : '') + num(v);
  function messaggio(id, tipo, testo) { mostraMessaggio(id, tipo, testo); }

  function testoPausa(pausa) {
    return pausa ? ` Automazione in pausa fino alle ${oraDaEpoch(pausa.fino)}.` : '';
  }

  // ── Stato ──────────────────────────────────────────────────────────────
  function blocco(titolo, icona, colore, righe) {
    return `<div class="tp-stanza-blocco">
      <div class="tp-dato-label"><i class="bi bi-${icona} tp-${colore}"></i> ${titolo}</div>
      ${righe}
    </div>`;
  }

  function renderStato(d) {
    const st = d.stanza || {};
    const ac = d.ac ? d.ac.stato || {} : null;
    const dec = d.decisione || {};
    const inclusa = d.zona.automazione !== false;
    const stato = !inclusa ? 'esclusa' : (d.pausa_fino ? 'pausa' : (dec.stato === 'esclusa' ? null : dec.stato));
    document.getElementById('badgeStanza').innerHTML = stato && d.automazione_attiva ? badgeStatoZona(stato, dec.simulazione) : '';
    document.getElementById('statoStanza').textContent = !d.automazione_attiva ? 'Automazione spenta'
      : !inclusa ? "Esclusa dall'automazione"
      : d.pausa_fino ? `In pausa fino alle ${oraDaEpoch(d.pausa_fino)}`
      : (dec.motivo || 'In attesa del prossimo controllo') + (dec.aggiornato ? ` · ${dec.aggiornato}` : '');

    const termostato = blocco('Termostato', 'thermometer-half', 'gas', `
      <div class="tp-stanza-valore">${gradi(st.temperatura_attuale)}</div>
      <div class="small tp-muted">${st.raggiungibile === false
        ? `<span class="text-danger">${escapeHtml(st.errore || 'non raggiungibile')}</span>`
        : `impostato ${gradi(st.setpoint)} · ${escapeHtml(MODI_STANZA[st.modalita] || st.modalita || '—')}${st.setpoint_fine ? ` fino alle ${oraDaEpoch(st.setpoint_fine)}` : ''}`}</div>
      <div class="small tp-muted"><i class="bi bi-droplet"></i> ${percento(st.umidita)}${st.richiesta_calore_pct ? ` · <span class="tp-gas"><i class="bi bi-fire"></i> richiesta ${percento(st.richiesta_calore_pct)}</span>` : ''}${st.finestra_aperta ? ' · <span class="text-danger"><i class="bi bi-wind"></i> finestra aperta</span>' : ''}</div>
      <a class="small" href="/dispositivi/stanza/${enc(roomId)}">Dettagli del termostato</a>`);
    const condizionatore = ac ? blocco('Condizionatore', 'snow', 'ac', `
      <div class="tp-stanza-valore">${gradi(ac.temperatura_ambiente, 0)}</div>
      <div class="small tp-muted">${ac.acceso
        ? `<span class="tp-ac">acceso</span> · ${escapeHtml(MODI_AC[ac.modalita] || ac.modalita || '')} a ${gradi(ac.setpoint_riscaldamento, 0)}`
        : 'spento'}</div>
      <div class="small tp-muted"><i class="bi bi-droplet"></i> ${percento(ac.umidita)}${ac.filtro_stato && ac.filtro_stato !== 'normal' ? ' · <span class="text-danger">filtro da pulire</span>' : ''}</div>
      <a class="small" href="/dispositivi/ac/${enc(d.ac.id)}">Dettagli di ${escapeHtml(d.ac.nome)}</a>`)
      : blocco('Condizionatore', 'snow', 'ac', '<div class="small tp-muted mt-2">Nessun condizionatore in questa stanza.</div>');
    let differenza = '';
    if (ac && st.temperatura_attuale != null && ac.temperatura_ambiente != null) {
      const diff = ac.temperatura_ambiente - st.temperatura_attuale;
      differenza = `<div class="small tp-muted">Il condizionatore legge ${segno(diff)} °C rispetto al termostato</div>`;
    }
    document.getElementById('temperatureStanza').innerHTML = `
      <div class="tp-stanza-blocchi">${termostato}${condizionatore}</div>
      <div class="d-flex flex-wrap justify-content-between gap-2 mt-2">
        <div class="small"><i class="bi bi-bullseye tp-green"></i> Target del programma <strong>${gradi(d.calcolo.target)}</strong></div>
        ${differenza}
      </div>`;
  }

  // ── Cosa fa TermoPilota ────────────────────────────────────────────────
  function renderCalcolo(d) {
    const c = d.calcolo;
    const dec = d.decisione || {};
    const righe = [];
    if (d.ac) {
      righe.push(`Quando conviene il condizionatore: <strong>AC a ${gradi(c.setpoint_ac_previsto)}</strong>
        (target ${gradi(c.target)} ${c.offset_ac >= 0 ? '+' : '−'} correzione ${num(Math.abs(c.offset_ac))} °C)
        e <strong>termostato a ${gradi(c.setpoint_termostato_in_ac)}</strong>
        ${c.modalita === 'affiancata' ? `(modalità affiancata: target − riserva ${num(c.riserva_gas_delta)} °C, la caldaia resta di riserva)`
          : '(modalità esclusiva: caldaia chiusa)'}.`);
      if (c.condiviso_con.length) {
        righe.push(`<i class="bi bi-share"></i> Il condizionatore serve anche ${escapeHtml(c.condiviso_con.join(', '))}:
          resta acceso finché una stanza lo chiede, al setpoint più alto richiesto.`);
      }
    } else {
      righe.push('Senza condizionatore la stanza è sempre scaldata dalla caldaia, secondo il programma Netatmo.');
    }
    if (dec.costo_gas) {
      righe.push(`Costo del calore adesso: <span class="tp-gas">caldaia €${dec.costo_gas.toFixed(3)}</span> ·
        <span class="tp-ac">pompa di calore €${dec.costo_ac.toFixed(3)}</span> per kWh termico.`);
    }
    const ds = d.differenza_sensori || {};
    if (ds.letture) {
      righe.push(`Negli ultimi 7 giorni, con il condizionatore acceso, il suo sensore ha letto in media
        <strong>${segno(ds.media)} °C</strong> rispetto al termostato (${ds.letture} letture)` +
        (ds.suggerita !== null && ds.suggerita !== c.offset_ac
          ? `: la correzione suggerita è <strong>${segno(ds.suggerita)} °C</strong> (ora ${segno(c.offset_ac)} °C).`
          : ds.suggerita !== null ? ': la correzione attuale è già quella giusta.' : '.'));
    }
    document.getElementById('calcoloStanza').innerHTML = righe.map(r => `<p class="mb-2">${r}</p>`).join('');

    const inclusa = d.zona.automazione !== false;
    document.getElementById('automazioneStanza').innerHTML = `
      <div class="form-check form-switch">
        <input class="form-check-input" type="checkbox" role="switch" id="inclusaStanza" ${inclusa ? 'checked' : ''}>
        <label class="form-check-label small" for="inclusaStanza">Inclusa nell'automazione</label>
      </div>
      ${inclusa ? `<div class="tp-segmented ms-auto" role="group" aria-label="Pausa">
        ${d.pausa_fino
          ? '<button type="button" data-pausa="0"><i class="bi bi-play-fill"></i>Riprendi</button>'
          : `<button type="button" data-pausa="1"><i class="bi bi-pause-fill"></i>1 h</button>
             <button type="button" data-pausa="3">3 h</button>`}
      </div>` : ''}`;
  }

  // ── Comandi ────────────────────────────────────────────────────────────
  function stepper(id, valore, min, max, passo, unita) {
    return `<div class="tp-stepper" id="${id}" data-min="${min}" data-max="${max}" data-passo="${passo}">
      <button type="button" class="btn btn-sm btn-outline-secondary" data-passo-dir="-1" aria-label="Diminuisci">−</button>
      <output>${valore}</output><span class="small tp-muted">${unita}</span>
      <button type="button" class="btn btn-sm btn-outline-secondary" data-passo-dir="1" aria-label="Aumenta">+</button>
    </div>`;
  }

  function renderComandi(d) {
    const st = d.stanza || {};
    let html = '';
    if (st.raggiungibile === false || !d.stanza) {
      html += `<div class="small text-danger mb-2"><i class="bi bi-wifi-off me-1"></i>${escapeHtml(st.errore || 'Termostato non raggiungibile')}: comandi del termostato non disponibili.</div>`;
    } else {
      html += `<div class="tp-controllo">
        <div><div class="tp-controllo-label">Termostato</div>
          <div class="small tp-muted">Temperatura manuale, poi torna da solo al programma</div></div>
        <div class="d-flex flex-wrap align-items-center gap-2">
          ${stepper('stepTermostato', st.setpoint ?? d.calcolo.target ?? 20, 5, 30, 0.5, '°C')}
          <select class="form-select form-select-sm w-auto" id="durataTermostato" aria-label="Durata">
            ${DURATE.map(([m, t]) => `<option value="${m}" ${m === 180 ? 'selected' : ''}>per ${t}</option>`).join('')}
          </select>
          <button type="button" class="btn btn-sm btn-primary" data-azione="setpoint">Imposta</button>
          <button type="button" class="btn btn-sm btn-outline-danger" data-azione="boost" title="Al massimo per 30 minuti"><i class="bi bi-fire"></i> Boost</button>
          <button type="button" class="btn btn-sm btn-outline-secondary" data-azione="ripristina" ${st.modalita === 'home' ? 'disabled' : ''}>Programma</button>
        </div>
      </div>`;
    }
    if (d.ac) {
      const controlli = Object.fromEntries((d.ac.controlli || []).map(c => [c.chiave, c]));
      const s = d.ac.stato || {};
      html += `<div class="tp-controllo">
        <div><div class="tp-controllo-label">Condizionatore</div>
          <div class="small tp-muted">${escapeHtml(d.ac.nome)}</div></div>
        <div class="d-flex flex-wrap align-items-center gap-2">
          ${controlli.accensione ? `<div class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" role="switch"
            id="acceso" ${s.acceso ? 'checked' : ''} aria-label="Accensione"></div>` : ''}
          ${controlli.modalita ? `<select class="form-select form-select-sm w-auto" id="modoAc" aria-label="Modalità">
            ${controlli.modalita.valori.map(v => `<option value="${escapeHtml(v)}" ${v === s.modalita ? 'selected' : ''}>${escapeHtml(controlli.modalita.etichette[v] || v)}</option>`).join('')}
          </select>` : ''}
          ${controlli.setpoint ? stepper('stepAc', s.setpoint_riscaldamento ?? controlli.setpoint.minimo, controlli.setpoint.minimo,
            controlli.setpoint.massimo, controlli.setpoint.passo, '°C') + '<button type="button" class="btn btn-sm btn-primary" data-azione="setpoint-ac">Imposta</button>' : ''}
          <a class="btn btn-sm btn-link" href="/dispositivi/ac/${enc(d.ac.id)}">Tutti i controlli</a>
        </div>
      </div>`;
    }
    document.getElementById('comandiStanza').innerHTML = html;
  }

  // ── Registro ───────────────────────────────────────────────────────────
  async function caricaRegistro(oggetti) {
    const p = new URLSearchParams({ limite: 20 });
    oggetti.forEach(o => p.append('oggetto', o));
    try {
      const r = await apiGetJson(`/api/registro?${p}`);
      document.getElementById('registroStanza').innerHTML = r.righe.length ? r.righe.map(e => `<div class="tp-log-item">
        <span class="tp-log-ts">${new Date(e.ts * 1000).toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>
        <span class="min-w-0">${escapeHtml(e.messaggio)}${e.utente ? ` <span class="tp-muted">(${escapeHtml(e.utente)})</span>` : ''}</span>
      </div>`).join('') : '<div class="tp-muted small">Nessun evento per questa stanza.</div>';
      document.getElementById('linkRegistro').href = `/registro?oggetto=${enc(oggetti[0] || '')}`;
    } catch (e) {
      document.getElementById('registroStanza').innerHTML = `<div class="text-danger small">${escapeHtml(e.message)}</div>`;
    }
  }

  // ── Impostazioni (admin) ───────────────────────────────────────────────
  function renderImpostazioni(d) {
    const box = document.getElementById('impostazioniStanza');
    if (!box || !d.opzioni) return;
    const z = d.zona;
    const ds = d.differenza_sensori || {};
    const termostati = d.opzioni.termostati.map(t => `<option value="${escapeHtml(t.id)}" ${t.id === z.room_id ? 'selected' : ''}
      ${t.usata_da ? 'disabled' : ''}>${escapeHtml(t.nome)}${t.usata_da ? ` (già in ${escapeHtml(t.usata_da)})` : ''}</option>`).join('');
    const condizionatori = '<option value="">Nessuno</option>' + d.opzioni.condizionatori.map(a => `<option value="${escapeHtml(a.id)}"
      ${a.id === z.ac_device_id ? 'selected' : ''}>${escapeHtml(a.nome)}${a.usato_da.length ? ` (anche ${escapeHtml(a.usato_da.join(', '))})` : ''}</option>`).join('');
    box.innerHTML = `<div class="row g-3">
      <div class="col-md-4"><label class="form-label small" for="impNome">Nome</label>
        <input class="form-control form-control-sm" id="impNome" maxlength="60" value="${escapeHtml(z.nome)}"></div>
      <div class="col-md-4"><label class="form-label small" for="impTermostato">Termostato (stanza Netatmo)</label>
        <select class="form-select form-select-sm" id="impTermostato">${termostati}</select></div>
      <div class="col-md-4"><label class="form-label small" for="impAc">Condizionatore</label>
        <select class="form-select form-select-sm" id="impAc">${condizionatori}</select>
        <div class="form-text">Lo stesso condizionatore può servire più stanze.</div></div>
      <div class="col-md-4"><label class="form-label small" for="impModalita">Modalità</label>
        <select class="form-select form-select-sm" id="impModalita">
          <option value="esclusiva" ${z.modalita !== 'affiancata' ? 'selected' : ''}>Esclusiva: con l'AC la caldaia si chiude</option>
          <option value="affiancata" ${z.modalita === 'affiancata' ? 'selected' : ''}>Affiancata: la caldaia resta di riserva</option>
        </select></div>
      <div class="col-md-4"><label class="form-label small" for="impRiserva">Riserva caldaia (°C sotto il target)</label>
        <input type="number" class="form-control form-control-sm" id="impRiserva" min="0.5" max="5" step="0.5"
          value="${z.riserva_gas_delta}" ${z.modalita === 'affiancata' ? '' : 'disabled'}></div>
      <div class="col-md-4"><label class="form-label small" for="impOffset">Correzione setpoint AC (°C)</label>
        <div class="input-group input-group-sm">
          <input type="number" class="form-control" id="impOffset" min="-3" max="3" step="0.5" value="${z.offset_ac}">
          ${ds.suggerita !== null && ds.suggerita !== undefined ? `<button type="button" class="btn btn-outline-secondary" id="usaSuggerita"
            data-valore="${ds.suggerita}">Usa ${segno(ds.suggerita)}</button>` : ''}
        </div>
        <div class="form-text">Si somma al target per il setpoint dell'AC: il suo sensore, in alto, di solito legge più caldo.</div></div>
    </div>
    <div class="d-flex flex-wrap gap-2 mt-3">
      <button type="button" class="btn btn-dark btn-sm" id="salvaStanza"><i class="bi bi-save me-1"></i>Salva</button>
      <button type="button" class="btn btn-outline-danger btn-sm ms-auto" id="eliminaStanza"><i class="bi bi-trash me-1"></i>Elimina stanza</button>
    </div>`;
  }

  async function salva() {
    const corpo = {
      nome: document.getElementById('impNome').value.trim(),
      room_id: document.getElementById('impTermostato').value,
      ac_device_id: document.getElementById('impAc').value,
      modalita: document.getElementById('impModalita').value,
      riserva_gas_delta: parseFloat(document.getElementById('impRiserva').value),
      offset_ac: parseFloat(document.getElementById('impOffset').value),
    };
    try {
      const r = await apiPostJson(`/api/stanze/${enc(roomId)}`, corpo);
      if (r.room_id !== roomId) { location.href = `/stanze/${enc(r.room_id)}`; return; }
      messaggio('msgImpostazioni', 'success', 'Impostazioni salvate.');
      carica();
    } catch (e) {
      messaggio('msgImpostazioni', 'danger', escapeHtml(e.message));
    }
  }

  async function elimina() {
    if (!confirm(`Eliminare la stanza "${dati.zona.nome}"? Il termostato torna al programma e TermoPilota smette di gestirla.`)) return;
    try {
      const res = await fetch(`/api/stanze/${enc(roomId)}`, { method: 'DELETE' });
      if (!res.ok) throw new Error((await res.json()).errore || `HTTP ${res.status}`);
      location.href = '/admin/zones';
    } catch (e) {
      messaggio('msgImpostazioni', 'danger', escapeHtml(e.message));
    }
  }

  // ── Grafici ────────────────────────────────────────────────────────────
  const grafici = {};
  let intervallo = '24h';

  function periodoDa(i) {
    const oggi = giornoLocale();
    if (i === '7g') return { da: giornoLocale(new Date(Date.now() - 6 * 86400000)), a: oggi, risoluzione: 'oraria' };
    if (i === '30g') return { da: giornoLocale(new Date(Date.now() - 29 * 86400000)), a: oggi, risoluzione: 'giornaliera' };
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
    let s;
    try {
      s = await apiGetJson(`/api/stanze/${enc(roomId)}/storico?da=${da}&a=${a}&risoluzione=${risoluzione}`);
    } catch (e) {
      return;
    }
    // Termostato e AC uniti per periodo (letture allineate a 15 minuti)
    const perPeriodo = {};
    s.stanza.forEach(p => { (perPeriodo[p.periodo] = perPeriodo[p.periodo] || {}).stanza = p; });
    s.ac.forEach(p => { (perPeriodo[p.periodo] = perPeriodo[p.periodo] || {}).ac = p; });
    let periodi = Object.keys(perPeriodo).sort();
    if (risoluzione === 'grezza') {
      const limite = new Date(Date.now() - 86400000);
      const p2 = (n) => String(n).padStart(2, '0');
      const soglia = `${giornoLocale(limite)}T${p2(limite.getHours())}:${p2(limite.getMinutes())}`;
      periodi = periodi.filter(p => p >= soglia);
    }
    document.getElementById('graficiVuoti').style.display = periodi.length ? 'none' : '';
    const righe = periodi.map(p => perPeriodo[p]);
    const labels = periodi.map(p => etichetta(p, risoluzione));
    const linea = (chiave, extra = {}) => ({
      borderColor: G.colore(chiave), backgroundColor: 'transparent', pointRadius: 0,
      pointHoverBackgroundColor: G.colore(chiave), pointHoverBorderColor: G.colore('pannello'), spanGaps: true, ...extra,
    });
    const maxTickX = G.mobile() ? 5 : 8;
    const val = (r, parte, chiave) => (r[parte] || {})[chiave] ?? null;
    const datasets = [
      { label: 'Termostato', coloreVar: '--gas-color', data: righe.map(r => val(r, 'stanza', 't_ambiente')), ...linea('gas', { borderWidth: 2.5 }) },
    ];
    if (s.ac.length) {
      datasets.push({ label: 'Sensore AC', coloreVar: '--ac-color', data: righe.map(r => val(r, 'ac', 't_ambiente')), ...linea('ac', { borderWidth: 2.5 }) });
      datasets.push({ label: 'Setpoint AC', coloreVar: '--chart-neutral',
        data: righe.map(r => (val(r, 'ac', 'attivo') === 0 ? null : val(r, 'ac', 'setpoint'))),
        ...linea('neutro', { stepped: true, borderDash: [5, 4] }) });
    }
    if (risoluzione === 'grezza') {
      datasets.push({ label: 'Target', coloreVar: '--green', data: righe.map(r => ((r.stanza || {}).extra || {}).target ?? null),
        ...linea('verde', { stepped: true, borderDash: [2, 3] }) });
    }
    const opzT = G.opzioniBase({ unitaY: '°C', tickY: v => `${v.toFixed(0)}°`, maxTickX });
    opzT.plugins.tooltip = { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y === null ? '—' : ctx.parsed.y.toFixed(1) + '°C'}` } };
    nuovoGrafico('graficoTemperatureStanza', { type: 'line', data: { labels, datasets }, options: opzT }, 'legendaTemperature');

    // Chi scalda: AC acceso e richiesta di calore della caldaia (% del periodo)
    const percAc = r => risoluzione === 'grezza' ? (val(r, 'ac', 'attivo') ? 100 : 0) : val(r, 'ac', 'attivo_pct');
    const percGas = r => risoluzione === 'grezza' ? (((r.stanza || {}).extra || {}).richiesta_calore_pct ?? 0) : val(r, 'stanza', 'attivo_pct');
    const barre = (chiave) => ({ backgroundColor: G.colore(chiave), hoverBackgroundColor: G.colore(chiave), borderRadius: 2, maxBarThickness: 18 });
    const datasetsA = [{ label: 'Caldaia', coloreVar: '--gas-color', data: righe.map(percGas), ...barre('gas') }];
    if (s.ac.length) datasetsA.unshift({ label: 'Condizionatore', coloreVar: '--ac-color', data: righe.map(percAc), ...barre('ac') });
    const opzA = G.opzioniBase({ maxTickX });
    opzA.plugins.mirino = false;
    opzA.scales.y.max = 100;
    opzA.scales.y.ticks.callback = v => `${v}%`;
    opzA.plugins.tooltip = { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${Math.round(ctx.parsed.y)}%` } };
    nuovoGrafico('graficoAttivitaStanza', { type: 'bar', data: { labels, datasets: datasetsA }, options: opzA }, 'legendaAttivita');

    const ds = (dati || {}).differenza_sensori || {};
    document.getElementById('differenzaSensori').textContent = ds.letture
      ? `°C · con l'AC acceso il suo sensore legge in media ${segno(ds.media)} °C`
      : '°C';
  }

  // ── Caricamento ────────────────────────────────────────────────────────
  async function carica() {
    try {
      const res = await fetch(`/api/stanze/${enc(roomId)}`);
      const d = await res.json();
      if (!res.ok) throw new Error(d.errore || `HTTP ${res.status}`);
      dati = d;
      document.title = `${d.zona.nome} · TermoPilota`;
      document.getElementById('nomeStanza').textContent = d.zona.nome;
      document.getElementById('erroriStanza').innerHTML = (d.errori || []).length
        ? `<div class="alert alert-warning py-2 small mb-0">${d.errori.map(escapeHtml).join('<br>')}</div>` : '';
      if (d.letto_alle) segnalaAggiornamento(`Letto alle ${d.letto_alle.slice(11, 16)}`, false);
      renderStato(d);
      renderCalcolo(d);
      renderComandi(d);
      if (!document.getElementById('impostazioniStanza')?.dataset.renderizzato) {
        renderImpostazioni(d);     // una volta sola: non si perdono le modifiche in corso
        const box = document.getElementById('impostazioniStanza');
        if (box && d.opzioni) box.dataset.renderizzato = '1';
      }
      caricaRegistro(d.oggetti_registro || []);
    } catch (e) {
      document.getElementById('erroriStanza').innerHTML = `<div class="alert alert-warning py-2 small mb-0">${escapeHtml(e.message)}</div>`;
    }
  }

  async function invia(url, corpo, ok) {
    try {
      const r = await apiPostJson(url, corpo);
      messaggio('msgStanza', 'success', `${ok(r)}.${testoPausa(r.pausa)}`);
    } catch (e) {
      messaggio('msgStanza', 'danger', escapeHtml(e.message));
    }
    setTimeout(carica, 2000);
  }

  document.addEventListener('click', (e) => {
    const passo = e.target.closest('[data-passo-dir]');
    if (passo) {
      const box = passo.closest('.tp-stepper');
      const out = box.querySelector('output');
      const p = Number(box.dataset.passo);
      out.textContent = String(Math.round(Math.min(Number(box.dataset.max), Math.max(Number(box.dataset.min),
        Number(out.textContent) + p * Number(passo.dataset.passoDir))) * 100) / 100);
      return;
    }
    const azione = e.target.closest('[data-azione]');
    if (azione) {
      const tipo = azione.dataset.azione;
      const valore = (id) => Number(document.querySelector(`#${id} output`).textContent);
      if (tipo === 'setpoint') {
        invia(`/api/dispositivi/stanza/${enc(roomId)}/setpoint`,
          { temp: valore('stepTermostato'), durata_min: Number(document.getElementById('durataTermostato').value) },
          r => `Termostato a ${valore('stepTermostato')} °C fino alle ${oraDaEpoch(r.fine)}`);
      } else if (tipo === 'boost') {
        invia(`/api/dispositivi/stanza/${enc(roomId)}/boost`, { durata_min: 30 }, r => `Boost fino alle ${oraDaEpoch(r.fine)}`);
      } else if (tipo === 'ripristina') {
        invia(`/api/dispositivi/stanza/${enc(roomId)}/ripristina`, {}, () => 'Termostato tornato al programma');
      } else if (tipo === 'setpoint-ac') {
        invia(`/api/dispositivi/ac/${enc(dati.ac.id)}/comando`, { chiave: 'setpoint', valore: valore('stepAc') },
          () => `Condizionatore a ${valore('stepAc')} °C`);
      }
      return;
    }
    const pausa = e.target.closest('[data-pausa]');
    if (pausa) {
      invia(`/api/automazione/zona/${enc(roomId)}/pausa`, { ore: Number(pausa.dataset.pausa) },
        r => (r.pausa_fino ? `In pausa fino alle ${oraDaEpoch(r.pausa_fino)}` : 'Automazione ripresa'));
      return;
    }
    if (e.target.closest('#salvaStanza')) salva();
    else if (e.target.closest('#eliminaStanza')) elimina();
    else if (e.target.closest('#usaSuggerita')) {
      document.getElementById('impOffset').value = e.target.closest('#usaSuggerita').dataset.valore;
    }
  });

  document.addEventListener('change', (e) => {
    if (e.target.id === 'inclusaStanza') {
      invia(`/api/automazione/zona/${enc(roomId)}/attiva`, { attiva: e.target.checked },
        r => (r.automazione ? "Stanza inclusa nell'automazione" : "Stanza esclusa dall'automazione"));
    } else if (e.target.id === 'acceso') {
      invia(`/api/dispositivi/ac/${enc(dati.ac.id)}/comando`, { chiave: 'accensione', valore: e.target.checked ? 'on' : 'off' },
        () => (e.target.checked ? 'Condizionatore acceso' : 'Condizionatore spento'));
    } else if (e.target.id === 'modoAc') {
      invia(`/api/dispositivi/ac/${enc(dati.ac.id)}/comando`, { chiave: 'modalita', valore: e.target.value },
        () => 'Modalità del condizionatore cambiata');
    } else if (e.target.id === 'impModalita') {
      document.getElementById('impRiserva').disabled = e.target.value !== 'affiancata';
    }
  });

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

  carica().then(caricaGrafici);
  ogni(60 * 1000, carica);
  ogni(15 * 60 * 1000, caricaGrafici);
  ascoltaLive(carica);
})();
