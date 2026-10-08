// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Copia in src/termopilota/static/vendor/ i file front-end delle librerie dichiarate in
// package.json (versioni bloccate da package-lock.json). La cartella non e'
// nel repository: si genera con `npm ci && npm run vendor` (la fa anche il
// Dockerfile e la CI). Cosi' Dependabot vede le librerie e le aggiorna.
//
// Percorsi di destinazione: sono quelli usati dai template e da static/sw.js.
'use strict';

const fs = require('fs');
const path = require('path');

const radice = path.resolve(__dirname, '..');
const nodeModules = path.join(radice, 'node_modules');
const destinazione = path.join(radice, 'src', 'termopilota', 'static', 'vendor');

// [destinazione relativa a static/vendor, possibili sorgenti in node_modules]
// Per chart.js si prova prima la build gia' minificata, poi quella UMD (nella
// 4.4.x chart.umd.js e' gia' minificata).
const FILE = [
  ['bootstrap/bootstrap.min.css',            ['bootstrap/dist/css/bootstrap.min.css']],
  ['bootstrap/bootstrap.bundle.min.js',      ['bootstrap/dist/js/bootstrap.bundle.min.js']],
  ['bootstrap-icons/bootstrap-icons.min.css', ['bootstrap-icons/font/bootstrap-icons.min.css']],
  ['bootstrap-icons/fonts/bootstrap-icons.woff2', ['bootstrap-icons/font/fonts/bootstrap-icons.woff2']],
  ['bootstrap-icons/fonts/bootstrap-icons.woff',  ['bootstrap-icons/font/fonts/bootstrap-icons.woff']],
  ['chartjs/chart.umd.min.js',               ['chart.js/dist/chart.umd.min.js', 'chart.js/dist/chart.umd.js']],
];

function fallisci(messaggio) {
  console.error(`vendor: ${messaggio}`);
  process.exit(1);
}

if (!fs.existsSync(nodeModules)) {
  fallisci('node_modules non trovato: esegui prima `npm ci`.');
}

// Parte da una cartella pulita, cosi' non restano file di versioni precedenti
fs.rmSync(destinazione, { recursive: true, force: true });

for (const [dest, sorgenti] of FILE) {
  const sorgente = sorgenti.map((s) => path.join(nodeModules, s)).find((p) => fs.existsSync(p));
  if (!sorgente) {
    fallisci(`nessuno dei sorgenti trovato per ${dest}: ${sorgenti.join(', ')}. ` +
             'La struttura del pacchetto e\' cambiata? Aggiorna scripts/vendor.js.');
  }
  const percorso = path.join(destinazione, dest);
  fs.mkdirSync(path.dirname(percorso), { recursive: true });
  fs.copyFileSync(sorgente, percorso);
  console.log(`vendor: ${path.relative(radice, percorso)}`);
}
