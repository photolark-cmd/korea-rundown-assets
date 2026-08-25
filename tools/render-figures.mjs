#!/usr/bin/env node
/**
 * Batch-render in-article figures (comparison bars, trend columns, stat tiles)
 * from a CSV into PNGs sized for the blog body.
 *
 *   node tools/render-figures.mjs [options]
 *
 * The look lives in figure-template.html; this script fills it in and
 * screenshots the figure element, so each PNG is exactly as tall as it needs
 * to be. See tools/README.md.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readRows } from './lib/csv.mjs';
import { escapeHtml, hasHangul } from './lib/text.mjs';
import { readFontCss, waitForFonts, launchBrowser } from './lib/browser.mjs';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TYPES = ['bar', 'column', 'stat'];

const HELP = `Batch-render in-article figures from a CSV.

  node tools/render-figures.mjs [options]

  --csv <path>            input CSV        (default: figures.csv)
  --out <dir>             output directory (default: figures/)
  --template <path>       HTML template    (default: tools/figure-template.html)
  --theme <light|dark>    default theme for rows that don't set one (default: light)
  --width <px>            figure width in CSS px (default: 640)
  --only <slug,...>       render just these rows
  --scale <n>             device scale factor (default: 2, for retina)
  --concurrency <n>       figures rendered in parallel (default: 4)
  --skip-existing         leave already-rendered PNGs alone
  --font-css <path>       local @font-face stylesheet instead of Google Fonts
  --allow-fallback-fonts  render even if the brand faces fail to load
  --dry-run               parse and report, write nothing
  -h, --help              this message

CSV columns
  slug      required  output filename
  type      required  bar | column | stat
  series    required  "Label=value|Label=value"  (value is a number)
  title     required  figure heading
  subtitle            one line under the title
  highlight           label to emphasise (default: the first item)
  prefix / suffix     wrapped around every displayed value, e.g. $ or " a ride"
  note                source line, bottom left
  theme               light | dark   (overrides --theme for this row)
`;

/* ---------------------------------------------------------------- options */

function parseArgs(argv) {
  const opts = {
    csv: path.join(REPO_ROOT, 'figures.csv'),
    out: path.join(REPO_ROOT, 'figures'),
    template: path.join(REPO_ROOT, 'tools', 'figure-template.html'),
    theme: 'light',
    width: 640,
    only: null,
    scale: 2,
    concurrency: 4,
    skipExisting: false,
    fontCss: null,
    allowFallbackFonts: false,
    dryRun: false,
    help: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const value = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${arg} needs a value`);
      return v;
    };
    switch (arg) {
      case '--csv': opts.csv = path.resolve(value()); break;
      case '--out': opts.out = path.resolve(value()); break;
      case '--template': opts.template = path.resolve(value()); break;
      case '--theme': opts.theme = value(); break;
      case '--width': opts.width = Number(value()); break;
      case '--only': opts.only = value().split(',').map((s) => s.trim()).filter(Boolean); break;
      case '--scale': opts.scale = Number(value()); break;
      case '--concurrency': opts.concurrency = Number(value()); break;
      case '--skip-existing': opts.skipExisting = true; break;
      case '--font-css': opts.fontCss = path.resolve(value()); break;
      case '--allow-fallback-fonts': opts.allowFallbackFonts = true; break;
      case '--dry-run': opts.dryRun = true; break;
      case '-h': case '--help': opts.help = true; break;
      default: throw new Error(`unknown option: ${arg}`);
    }
  }
  if (!['light', 'dark'].includes(opts.theme)) throw new Error('--theme must be light or dark');
  if (!Number.isFinite(opts.width) || opts.width < 320) throw new Error('--width must be at least 320');
  if (!Number.isFinite(opts.scale) || opts.scale <= 0) throw new Error('--scale must be a positive number');
  if (!Number.isInteger(opts.concurrency) || opts.concurrency < 1) throw new Error('--concurrency must be a positive integer');
  return opts;
}

/* ------------------------------------------------------------------- data */

/** "Seoul=1.10|New York=2.90" -> [{label, value}] */
function parseSeries(raw, where) {
  const items = raw.split('|').map((s) => s.trim()).filter(Boolean).map((pair) => {
    const at = pair.lastIndexOf('=');
    if (at < 1) throw new Error(`${where}: "${pair}" should look like Label=value`);
    const label = pair.slice(0, at).trim();
    const written = pair.slice(at + 1).trim();
    const value = Number(written.replace(/,/g, ''));
    if (!Number.isFinite(value)) throw new Error(`${where}: "${pair}" has a non-numeric value`);
    if (value < 0) throw new Error(`${where}: "${pair}" is negative; bars grow from a zero baseline`);
    // Show the number exactly as written - the author controls decimals and commas.
    return { label, value, written };
  });
  if (!items.length) throw new Error(`${where}: series is empty`);
  return items;
}

/* -------------------------------------------------------------- rendering */

/**
 * Draw one figure inside the page. Runs in the browser so labels can be
 * measured before they are placed; figure-template.html owns every colour.
 */
function drawFigure(job) {
  const { type, title, subtitle, note, brand, items, highlight, prefix, suffix, vars } = job;
  const root = document.documentElement;
  root.setAttribute('data-theme', job.theme);
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);

  const set = (id, html, hideWhenEmpty) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (!html && hideWhenEmpty) { el.style.display = 'none'; return; }
    el.innerHTML = html;
  };
  set('title', title);
  set('subtitle', subtitle, true);
  set('note', note, true);
  set('brand', brand, true);

  // Defined here because this function is serialised into the page.
  const niceMax = (v) => {
    if (v <= 0) return 1;
    const e = 10 ** Math.floor(Math.log10(v));
    const m = v / e;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * e;
  };
  const withCommas = (n) => n.toLocaleString('en-US', { maximumFractionDigits: 2 });

  const display = (v) => prefix + v.display + suffix;
  const max = Math.max(...items.map((d) => d.value));
  const plot = document.getElementById('plot');
  plot.innerHTML = '';

  if (type === 'bar') {
    // Horizontal bars: long entity names read better on the left.
    for (const d of items) {
      const row = document.createElement('div');
      row.className = 'row';
      row.innerHTML =
        `<div class="row-label">${d.label}</div>` +
        `<div class="track">` +
        `<div class="fill ${d.label === highlight ? 'on' : 'off'}"></div>` +
        `<div class="row-value">${display(d)}</div>` +
        `</div>`;
      plot.appendChild(row);
    }
    // Measure the widest value, then cap bar length so no label runs off the edge.
    const tracks = [...plot.querySelectorAll('.track')];
    const trackW = tracks[0].getBoundingClientRect().width;
    const gapPx = 10;
    const widest = Math.max(...tracks.map((t) => t.querySelector('.row-value').getBoundingClientRect().width));
    const usable = Math.max(trackW - widest - gapPx, trackW * 0.25);
    tracks.forEach((t, i) => {
      const frac = max > 0 ? items[i].value / max : 0;
      const w = Math.max(frac * usable, 3);
      t.querySelector('.fill').style.width = `${w}px`;
      t.querySelector('.row-value').style.left = `${w + gapPx}px`;
    });
  } else if (type === 'column') {
    const top = niceMax(max);
    const wrap = document.createElement('div');
    wrap.className = 'colwrap';
    wrap.style.marginTop = '10px';

    // Left axis: 0 / half / top. These carry the columns we do not label directly.
    const yaxis = document.createElement('div');
    yaxis.className = 'yaxis';
    const ticks = [0, top / 2, top];
    yaxis.innerHTML = ticks.map((t) =>
      `<div class="ytick" style="bottom:${(t / top) * 100}%">${withCommas(t)}</div>`).join('');

    const colHolder = document.createElement('div');
    const cols = document.createElement('div');
    cols.className = 'cols';
    for (const t of ticks) {
      const grid = document.createElement('div');
      grid.className = 'gridline';
      grid.style.bottom = `${(t / top) * 100}%`;
      cols.appendChild(grid);
    }
    // Label selectively: the extreme and the endpoint, never every column.
    const maxIdx = items.reduce((best, d, i) => (d.value > items[best].value ? i : best), 0);
    const labelled = new Set([maxIdx, items.length - 1]);
    items.forEach((d, i) => {
      const col = document.createElement('div');
      col.className = 'col' + (i === items.length - 1 ? ' on' : '');
      const h = top > 0 ? Math.max((d.value / top) * 100, 1.5) : 0;
      col.innerHTML =
        (labelled.has(i) ? `<div class="cval">${display(d)}</div>` : '') +
        `<div class="cbar" style="height:${h}%"></div>`;
      cols.appendChild(col);
    });
    const axis = document.createElement('div');
    axis.className = 'xaxis';
    axis.innerHTML = items.map((d) => `<div class="xtick">${d.label}</div>`).join('');
    colHolder.appendChild(cols);
    colHolder.appendChild(axis);
    wrap.appendChild(yaxis);
    wrap.appendChild(colHolder);
    plot.appendChild(wrap);
    // Absolutely positioned ticks add no intrinsic width, so the column would
    // collapse and the labels would hang outside the figure's padding.
    const tickW = Math.max(...[...yaxis.querySelectorAll('.ytick')]
      .map((t) => t.getBoundingClientRect().width));
    yaxis.style.width = `${Math.ceil(tickW)}px`;
    // Seat each value label above its cap, nudged inward if it would leave the plot.
    const bounds = cols.getBoundingClientRect();
    cols.querySelectorAll('.col').forEach((col) => {
      const val = col.querySelector('.cval');
      if (!val) return;
      const bar = col.querySelector('.cbar');
      val.style.bottom = `${bar.getBoundingClientRect().height + 6}px`;
      const box = val.getBoundingClientRect();
      if (box.right > bounds.right) val.style.transform = `translateX(-${(box.right - bounds.right) + (box.width / 2)}px)`;
      else if (box.left < bounds.left) val.style.transform = `translateX(${(bounds.left - box.left) - (box.width / 2)}px)`;
    });
  } else {
    const tiles = document.createElement('div');
    tiles.className = 'tiles';
    tiles.innerHTML = items.map((d) => (
      `<div class="tile ${d.label === highlight ? 'on' : ''}">` +
      `<div class="tile-rule"></div>` +
      `<div class="tile-label">${d.label}</div>` +
      `<div class="tile-value">${display(d)}</div>` +
      (d.note ? `<div class="tile-note">${d.note}</div>` : '') +
      `</div>`
    )).join('');
    plot.appendChild(tiles);
  }

  // Report anything that would render clipped or overflowing.
  const fig = document.getElementById('fig');
  const overflow = [];
  if (fig.scrollWidth > fig.clientWidth + 1) overflow.push('figure is wider than its frame');
  const pad = parseFloat(getComputedStyle(fig).paddingLeft);
  const box = fig.getBoundingClientRect();
  const inner = { left: box.left + pad - 0.5, right: box.right - pad + 0.5 };
  for (const el of plot.querySelectorAll('.row-value, .tile-value, .cval, .xtick, .ytick, .row-label')) {
    if (el.scrollWidth > el.clientWidth + 1) overflow.push(`"${el.textContent}" is clipped`);
    const r = el.getBoundingClientRect();
    if (r.left < inner.left || r.right > inner.right) {
      overflow.push(`"${el.textContent}" sits outside the figure padding`);
    }
  }
  return { overflow, height: Math.round(fig.getBoundingClientRect().height) };
}

/* ------------------------------------------------------------------- jobs */

function buildJobs(opts) {
  let rows = readRows(opts.csv, ['slug', 'type', 'title', 'series']);
  if (opts.only) {
    const wanted = new Set(opts.only);
    const present = new Set(rows.map((r) => r.slug));
    for (const slug of wanted) if (!present.has(slug)) console.warn(`! --only "${slug}" matches no row`);
    rows = rows.filter((r) => wanted.has(r.slug));
  }

  const seen = new Set();
  const jobs = [];
  for (const row of rows) {
    const where = `${opts.csv}:${row.__line}`;
    if (!row.slug) throw new Error(`${where}: missing slug`);
    if (!/^[A-Za-z0-9._-]+$/.test(row.slug)) throw new Error(`${where}: invalid slug "${row.slug}"`);
    if (seen.has(row.slug)) throw new Error(`${where}: duplicate slug "${row.slug}"`);
    seen.add(row.slug);

    const type = (row.type || 'bar').toLowerCase();
    if (!TYPES.includes(type)) throw new Error(`${where}: type must be one of ${TYPES.join(', ')}`);
    if (!row.title) throw new Error(`${where}: missing title`);
    if (!row.series) throw new Error(`${where}: missing series`);

    const theme = (row.theme || opts.theme).toLowerCase();
    if (!['light', 'dark'].includes(theme)) throw new Error(`${where}: theme must be light or dark`);

    const items = parseSeries(row.series, where);
    if (type === 'stat' && items.length > 3) throw new Error(`${where}: stat takes at most 3 tiles`);
    if (type === 'bar' && items.length > 6) throw new Error(`${where}: bar takes at most 6 items - past that use a table`);

    const file = path.join(opts.out, `${row.slug}.png`);
    if (opts.skipExisting && fs.existsSync(file)) {
      console.log(`- ${row.slug}.png (exists, skipped)`);
      continue;
    }
    jobs.push({
      slug: row.slug,
      file,
      korean: hasHangul(Object.values(row).join('')),
      type,
      theme,
      title: escapeHtml(row.title),
      subtitle: escapeHtml(row.subtitle || ''),
      note: escapeHtml(row.note || ''),
      brand: escapeHtml(row.brand || 'KOREA RUNDOWN'),
      prefix: escapeHtml(row.prefix || ''),
      suffix: escapeHtml(row.suffix || ''),
      highlight: row.highlight || items[0].label,
      items: items.map((d) => ({
        label: escapeHtml(d.label),
        value: d.value,
        display: escapeHtml(d.written),
        note: '',
      })),
      vars: { '--fig-w': `${opts.width}px` },
    });
  }
  return jobs;
}

/* ------------------------------------------------------------------- main */

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) { process.stdout.write(HELP); return 0; }

  const inputs = [['CSV', opts.csv], ['Template', opts.template]];
  if (opts.fontCss) inputs.push(['Font CSS', opts.fontCss]);
  for (const [label, target] of inputs) {
    if (!fs.existsSync(target)) throw new Error(`${label} not found: ${target}`);
  }

  const jobs = buildJobs(opts);
  if (!jobs.length) { console.log('Nothing to render.'); return 0; }
  if (opts.dryRun) {
    for (const job of jobs) console.log(`would write ${path.relative(process.cwd(), job.file)}  (${job.type}, ${job.theme})`);
    return 0;
  }

  fs.mkdirSync(opts.out, { recursive: true });
  const templateUrl = pathToFileURL(opts.template).href;
  const fontCss = opts.fontCss ? readFontCss(opts.fontCss) : null;

  const browser = await launchBrowser();
  const context = await browser.newContext({
    viewport: { width: Math.ceil(opts.width) + 80, height: 900 },
    deviceScaleFactor: opts.scale,
  });
  if (fontCss) await context.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.abort());

  const problems = [];
  const missingFonts = new Set();
  const queue = jobs.slice();

  const worker = async () => {
    const page = await context.newPage();
    try {
      while (queue.length) {
        const job = queue.shift();
        await page.goto(templateUrl, { waitUntil: 'domcontentloaded' });
        if (fontCss) await page.addStyleTag({ content: fontCss });

        await page.evaluate(drawFigure, job);
        const loaded = await waitForFonts(page);
        // Re-draw once the real faces are in: label measurement depends on them.
        const result = await page.evaluate(drawFigure, job);

        if (!loaded.includes('Inter')) missingFonts.add('Inter');
        if (job.korean && !loaded.some((f) => /Noto Sans KR/i.test(f))) missingFonts.add('Noto Sans KR');
        for (const o of result.overflow) problems.push(`${job.slug}: ${o}`);

        await page.locator('#fig').screenshot({ path: job.file });
        console.log(`+ ${job.slug}.png  (${job.type}, ${job.theme}, ${opts.width}×${result.height} @${opts.scale}x)`);
      }
    } finally {
      await page.close();
    }
  };

  try {
    await Promise.all(Array.from({ length: Math.min(opts.concurrency, jobs.length) }, worker));
  } finally {
    await browser.close();
  }

  for (const p of problems) console.warn(`! ${p}`);

  if (missingFonts.size) {
    const message =
      `${[...missingFonts].join(', ')} did not load, so these figures use fallback fonts.\n` +
      `  Check network access to fonts.googleapis.com, or pass --font-css <path> ` +
      `(see tools/README.md).`;
    if (!opts.allowFallbackFonts) {
      throw new Error(`${message}\n  Re-run with --allow-fallback-fonts to keep this output anyway.`);
    }
    console.warn(`! ${message}`);
  }

  console.log(`\n${jobs.length} figure${jobs.length === 1 ? '' : 's'} -> ${path.relative(process.cwd(), opts.out) || '.'}`);
  return problems.length ? 1 : 0;
}

main().then(
  (code) => process.exit(code),
  (err) => { console.error(`error: ${err.message}`); process.exit(1); }
);
