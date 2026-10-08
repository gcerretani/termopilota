// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — stile condiviso dei grafici Chart.js.
// I colori vengono dalle CSS variable del tema (opzioni "scriptable"): al cambio
// di tema basta un update() perche' i grafici si ricolorino.

const TPGrafici = (function () {
  let cache = null;

  function css(nome) {
    return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
  }

  function colori() {
    if (!cache) {
      cache = {
        gas: css('--gas-color'),
        ac: css('--ac-color'),
        verde: css('--green'),
        neutro: css('--chart-neutral'),
        testo: css('--text-muted'),
        testoForte: css('--text'),
        griglia: css('--chart-grid'),
        pannello: css('--panel'),
        bordo: css('--border'),
        font: css('--font-sans'),
      };
    }
    return cache;
  }

  function alfa(colore, a) {
    const hex = colore.replace('#', '');
    if (hex.length !== 6) return colore;
    const n = parseInt(hex, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  // Riempimento a gradiente verticale (scriptable: si ricalcola al resize e al cambio tema)
  function gradiente(chiaveColore, a0 = 0.28, a1 = 0) {
    return (ctx) => {
      const { chart } = ctx;
      const area = chart.chartArea;
      const colore = colori()[chiaveColore];
      if (!area) return alfa(colore, a0 / 2);
      const g = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
      g.addColorStop(0, alfa(colore, a0));
      g.addColorStop(1, alfa(colore, a1));
      return g;
    };
  }

  const colore = (chiave) => () => colori()[chiave];

  function applicaDefault() {
    const c = colori();
    Chart.defaults.font.family = c.font;
    Chart.defaults.font.size = 11;
    Chart.defaults.color = c.testo;
    Chart.defaults.borderColor = c.griglia;
    const t = Chart.defaults.plugins.tooltip;
    t.backgroundColor = c.pannello;
    t.titleColor = c.testoForte;
    t.bodyColor = c.testoForte;
    t.footerColor = c.testo;
    t.borderColor = c.bordo;
    t.borderWidth = 1;
    t.padding = 10;
    t.cornerRadius = 10;
    t.boxPadding = 4;
    t.usePointStyle = true;
    t.titleFont = { weight: '600' };
    t.footerFont = { weight: '400' };
  }

  const mobile = () => window.matchMedia('(max-width: 576px)').matches;

  // Opzioni comuni: un solo asse y, griglia orizzontale tenue, niente bordi asse
  function opzioniBase({ unitaY, tickY, maxTickX } = {}) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 6 } },
      plugins: { legend: { display: false }, mirino: {} },
      elements: {
        line: { borderWidth: 2, tension: 0.35, borderCapStyle: 'round' },
        point: { radius: 0, hoverRadius: 5, hitRadius: 8, hoverBorderWidth: 2 },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: { maxRotation: 0, autoSkipPadding: 14, maxTicksLimit: maxTickX || (mobile() ? 6 : 12) },
        },
        y: {
          border: { display: false },
          grid: { color: colore('griglia'), drawTicks: false },
          ticks: { padding: 8, maxTicksLimit: 5, callback: tickY },
          title: { display: !!unitaY && !mobile(), text: unitaY },
        },
      },
    };
  }

  // Linea verticale sotto il puntatore (mirino) — visibile durante hover/tocco
  const pluginMirino = {
    id: 'mirino',
    afterDatasetsDraw(chart) {
      const attivi = chart.tooltip && chart.tooltip.getActiveElements();
      if (!attivi || !attivi.length) return;
      const { ctx, chartArea } = chart;
      const x = attivi[0].element.x;
      ctx.save();
      ctx.strokeStyle = colori().testo;
      ctx.globalAlpha = 0.5;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      ctx.restore();
    },
  };

  // Linea "adesso": options.plugins.adesso = { indice }
  const pluginAdesso = {
    id: 'adesso',
    afterDatasetsDraw(chart, _args, opts) {
      if (opts == null || opts.indice == null || opts.indice < 0) return;
      const { ctx, chartArea, scales } = chart;
      const x = scales.x.getPixelForValue(opts.indice);
      if (x < chartArea.left || x > chartArea.right) return;
      ctx.save();
      ctx.strokeStyle = colori().testoForte;
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      if (opts.etichetta !== false) {
        ctx.setLineDash([]);
        ctx.font = `600 10px ${colori().font}`;
        const testo = 'adesso';
        const w = ctx.measureText(testo).width + 10;
        const xr = Math.min(Math.max(x - w / 2, chartArea.left), chartArea.right - w);
        ctx.fillStyle = colori().testoForte;
        ctx.beginPath();
        ctx.roundRect(xr, chartArea.top - 2, w, 15, 7);
        ctx.fill();
        ctx.fillStyle = colori().pannello;
        ctx.textBaseline = 'middle';
        ctx.fillText(testo, xr + 5, chartArea.top + 5.5);
      }
      ctx.restore();
    },
  };

  // Fasce di sfondo per la fonte consigliata: options.plugins.fasce = { valori: ['gas'|'ac', ...] }
  const pluginFasce = {
    id: 'fasce',
    beforeDatasetsDraw(chart, _args, opts) {
      if (!opts || !opts.valori) return;
      const { ctx, chartArea, scales } = chart;
      const n = opts.valori.length;
      if (n === 0) return;
      const passo = n > 1 ? scales.x.getPixelForValue(1) - scales.x.getPixelForValue(0) : chartArea.width;
      ctx.save();
      opts.valori.forEach((v, i) => {
        if (!v) return;
        const x0 = Math.max(scales.x.getPixelForValue(i) - passo / 2, chartArea.left);
        const x1 = Math.min(scales.x.getPixelForValue(i) + passo / 2, chartArea.right);
        ctx.fillStyle = alfa(colori()[v], 0.08);
        ctx.fillRect(x0, chartArea.top, x1 - x0, chartArea.bottom - chartArea.top);
      });
      ctx.restore();
    },
  };

  // Separatori di giorno: options.plugins.giorni = { indici: [i], etichette: ['domani'] }
  const pluginGiorni = {
    id: 'giorni',
    beforeDatasetsDraw(chart, _args, opts) {
      if (!opts || !opts.indici) return;
      const { ctx, chartArea, scales } = chart;
      ctx.save();
      opts.indici.forEach((i, k) => {
        const x = scales.x.getPixelForValue(i) - 0.5 * (scales.x.getPixelForValue(1) - scales.x.getPixelForValue(0));
        if (x <= chartArea.left || x >= chartArea.right) return;
        ctx.strokeStyle = colori().bordo;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, chartArea.top);
        ctx.lineTo(x, chartArea.bottom);
        ctx.stroke();
        const etichetta = opts.etichette && opts.etichette[k];
        if (etichetta) {
          ctx.fillStyle = colori().testo;
          ctx.font = `600 10px ${colori().font}`;
          ctx.textBaseline = 'top';
          ctx.fillText(etichetta, x + 4, chartArea.top + 2);
        }
      });
      ctx.restore();
    },
  };

  // Legenda HTML: chip cliccabili che mostrano/nascondono le serie
  function legenda(contenitore, chart) {
    const el = typeof contenitore === 'string' ? document.getElementById(contenitore) : contenitore;
    if (!el) return;
    el.innerHTML = '';
    chart.data.datasets.forEach((ds, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'tp-legend-chip';
      b.setAttribute('aria-pressed', 'true');
      b.innerHTML = `<span class="tp-legend-swatch" style="--sw:var(${ds.coloreVar || '--chart-neutral'})"></span>${escapeHtml(ds.label)}`;
      b.addEventListener('click', () => {
        const visibile = chart.isDatasetVisible(i);
        chart.setDatasetVisibility(i, !visibile);
        b.setAttribute('aria-pressed', visibile ? 'false' : 'true');
        chart.update();
      });
      el.appendChild(b);
    });
  }

  if (typeof Chart !== 'undefined') {
    Chart.register(pluginMirino, pluginAdesso, pluginFasce, pluginGiorni);
    applicaDefault();
    document.addEventListener('tema-cambiato', () => {
      cache = null;
      applicaDefault();
      Object.values(Chart.instances).forEach(c => c.update('none'));
    });
  }

  return { colori, colore, gradiente, alfa, opzioniBase, legenda, mobile };
})();
