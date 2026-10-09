// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — Registro eventi: filtri, elenco per giorno, dettagli (admin),
// "carica altri", scarica JSON, aggiornamento live.

(function () {
  const PAGINA = 100;
  const CATEGORIE = {
    automazione: ['Automazione', 'lightning-charge'],
    comando: ['Comando', 'hand-index'],
    evento: ['Evento', 'broadcast'],
    sistema: ['Sistema', 'gear'],
  };
  const LIVELLI = { debug: 'debug', info: '', warning: 'avviso', errore: 'errore' };

  // Filtri iniziali anche dall'URL (es. /registro?categoria=evento dalla pagina Credenziali)
  const parametri = new URLSearchParams(location.search);
  const filtri = {
    categoria: parametri.get('categoria') || '',
    livello: parametri.get('livello') || 'info',
    oggetto: parametri.get('oggetto') || '',
    q: parametri.get('q') || '',
  };
  let righe = [];
  let finite = false;
  let caricamento = false;

  function url(primaDi) {
    const p = new URLSearchParams({ limite: PAGINA, livello: filtri.livello });
    if (filtri.categoria) p.set('categorie', filtri.categoria);
    if (filtri.oggetto) p.set('oggetto', filtri.oggetto);
    if (filtri.q) p.set('q', filtri.q);
    if (primaDi) p.set('prima_di', primaDi);
    return `/api/registro?${p}`;
  }

  function giorno(ts) {
    return new Date(ts * 1000).toLocaleDateString('it-IT', { weekday: 'long', day: 'numeric', month: 'long' });
  }

  function riga(r, i) {
    const [nomeCat, icona] = CATEGORIE[r.categoria] || [r.categoria, 'dot'];
    const ora = new Date(r.ts * 1000).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const livello = LIVELLI[r.livello] ? `<span class="tp-badge-stato ${r.livello === 'debug' ? '' : 'danger'}">${LIVELLI[r.livello]}</span>` : '';
    const dettagli = r.dati && window.utenteAdmin;
    return `<div class="tp-registro-riga liv-${r.livello}${dettagli ? ' espandibile' : ''}" data-indice="${i}" ${dettagli ? 'tabindex="0" role="button" aria-expanded="false"' : ''}>
      <span class="tp-log-ts">${ora}</span>
      <span class="tp-registro-cat cat-${escapeHtml(r.categoria)}" title="${escapeHtml(nomeCat)}"><i class="bi bi-${icona}"></i></span>
      <div class="min-w-0 flex-grow-1">
        <div>${r.oggetto ? `<strong>${escapeHtml(r.oggetto)}</strong> · ` : ''}${escapeHtml(r.messaggio)} ${livello}</div>
        ${r.utente ? `<div class="small tp-muted"><i class="bi bi-person"></i> ${escapeHtml(r.utente)}</div>` : ''}
        ${dettagli ? `<pre class="tp-registro-dati" hidden>${escapeHtml(JSON.stringify(r.dati, null, 2))}</pre>` : ''}
      </div>
      ${dettagli ? '<i class="bi bi-chevron-down tp-muted"></i>' : ''}
    </div>`;
  }

  function render() {
    const lista = document.getElementById('registroLista');
    let html = '';
    let ultimoGiorno = null;
    righe.forEach((r, i) => {
      const g = giorno(r.ts);
      if (g !== ultimoGiorno) {
        html += `<div class="tp-valori-gruppo">${escapeHtml(g)}</div>`;
        ultimoGiorno = g;
      }
      html += riga(r, i);
    });
    lista.innerHTML = html;
    document.getElementById('registroVuoto').style.display = righe.length ? 'none' : '';
    document.getElementById('caricaAltri').style.display = finite || !righe.length ? 'none' : '';
  }

  function aggiornaOggetti(oggetti) {
    const sel = document.getElementById('filtroOggetto');
    const valori = new Set([...oggetti, filtri.oggetto].filter(Boolean));
    sel.innerHTML = '<option value="">Tutte le stanze e i dispositivi</option>'
      + [...valori].sort().map(o => `<option value="${escapeHtml(o)}"${o === filtri.oggetto ? ' selected' : ''}>${escapeHtml(o)}</option>`).join('');
  }

  async function carica(altri = false) {
    if (caricamento) return;
    caricamento = true;
    try {
      const primaDi = altri && righe.length ? righe[righe.length - 1].ts : null;
      const d = await apiGetJson(url(primaDi));
      righe = altri ? righe.concat(d.righe) : d.righe;
      finite = d.righe.length < PAGINA;
      aggiornaOggetti(d.oggetti || []);
      render();
    } catch (e) {
      document.getElementById('registroLista').innerHTML = `<div class="text-danger small">${escapeHtml(e.message)}</div>`;
    } finally {
      caricamento = false;
    }
  }

  // Aggiornamento: solo se si guardano le voci piu' recenti (non dopo "carica altri")
  function ricarica() {
    if (righe.length <= PAGINA) carica(false);
  }

  function sincronizzaControlli() {
    document.querySelectorAll('[data-categoria]').forEach(b => {
      const attivo = b.dataset.categoria === filtri.categoria;
      b.classList.toggle('active', attivo);
      b.setAttribute('aria-pressed', attivo ? 'true' : 'false');
    });
    document.getElementById('filtroLivello').value = filtri.livello;
    document.getElementById('filtroTesto').value = filtri.q;
  }

  function aggiornaUrl() {
    const p = new URLSearchParams();
    if (filtri.categoria) p.set('categoria', filtri.categoria);
    if (filtri.livello !== 'info') p.set('livello', filtri.livello);
    if (filtri.oggetto) p.set('oggetto', filtri.oggetto);
    if (filtri.q) p.set('q', filtri.q);
    history.replaceState(null, '', p.toString() ? `?${p}` : location.pathname);
  }

  function cambiaFiltro(campo, valore) {
    filtri[campo] = valore;
    aggiornaUrl();
    sincronizzaControlli();
    carica(false);
  }

  document.querySelectorAll('[data-categoria]').forEach(b =>
    b.addEventListener('click', () => cambiaFiltro('categoria', b.dataset.categoria)));
  document.getElementById('filtroLivello').addEventListener('change', e => cambiaFiltro('livello', e.target.value));
  document.getElementById('filtroOggetto').addEventListener('change', e => cambiaFiltro('oggetto', e.target.value));
  let attesaTesto = null;
  document.getElementById('filtroTesto').addEventListener('input', e => {
    clearTimeout(attesaTesto);
    attesaTesto = setTimeout(() => cambiaFiltro('q', e.target.value.trim()), 350);
  });
  document.getElementById('caricaAltri').addEventListener('click', () => carica(true));

  function espandi(el) {
    const dati = el.querySelector('.tp-registro-dati');
    if (!dati) return;
    dati.hidden = !dati.hidden;
    el.setAttribute('aria-expanded', dati.hidden ? 'false' : 'true');
  }
  const lista = document.getElementById('registroLista');
  lista.addEventListener('click', e => {
    if (e.target.closest('.tp-registro-dati')) return;    // si puo' selezionare il testo
    const el = e.target.closest('.espandibile');
    if (el) espandi(el);
  });
  lista.addEventListener('keydown', e => {
    const el = e.target.closest('.espandibile');
    if (el && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); espandi(el); }
  });

  document.getElementById('scaricaRegistro').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(righe, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `termopilota-registro-${giornoLocale()}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  sincronizzaControlli();
  carica(false);
  ogni(30000, ricarica, 10000);
  ascoltaLive(ricarica);
})();
