// TermoPilota — helper condivisi dalle pagine admin.

async function apiPostJson(url, payload) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let dettaglio = '';
    try { dettaglio = (await res.json()).errore || ''; } catch (e) { /* noop */ }
    throw new Error(dettaglio || `HTTP ${res.status}`);
  }
  return res.json();
}

async function apiGetJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function mostraMessaggio(containerId, tipo, testo) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = `<div class="alert alert-${tipo} py-2 mb-3 small">${testo}</div>`;
  setTimeout(() => { if (el) el.innerHTML = ''; }, 5000);
}
