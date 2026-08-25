#!/usr/bin/env node
/**
 * Batch-render Korea Rundown thumbnails from a CSV into 1200x630 PNGs.
 *
 *   node tools/render-thumbnails.mjs [options]
 *
 * The look lives in _template.html - this script only fills it in, shrinks the
 * type until it fits, and screenshots it. See tools/README.md.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';

const require = createRequire(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const WIDTH = 1200;
const HEIGHT = 630;
const FIGURE_MAX = 150, FIGURE_MIN = 56;   // px, matches _template.html
const KICKER_MAX = 37, KICKER_MIN = 22;

/* ---------------------------------------------------------------- options */

const HELP = `Batch-render Korea Rundown thumbnails from a CSV.

  node tools/render-thumbnails.mjs [options]

  --csv <path>            input CSV                (default: thumbnails.csv)
  --out <dir>             output directory         (default: repo root)
  --template <path>       HTML template            (default: _template.html)
  --only <slug,...>       render just these rows
  --scale <n>             device scale factor, 2 = retina (default: 1)
  --concurrency <n>       pages rendered in parallel     (default: 4)
  --skip-existing         leave already-rendered PNGs alone
  --font-css <path>       use a local @font-face stylesheet instead of
                          Google Fonts (offline / reproducible rendering)
  --allow-fallback-fonts  render even if the brand faces fail to load
  --dry-run               parse and report, write nothing
  -h, --help              this message
`;

function parseArgs(argv) {
  const opts = {
    csv: path.join(REPO_ROOT, 'thumbnails.csv'),
    out: REPO_ROOT,
    template: path.join(REPO_ROOT, '_template.html'),
    only: null,
    scale: 1,
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
  if (!Number.isFinite(opts.scale) || opts.scale <= 0) throw new Error('--scale must be a positive number');
  if (!Number.isInteger(opts.concurrency) || opts.concurrency < 1) throw new Error('--concurrency must be a positive integer');
  return opts;
}

/* ------------------------------------------------------------------- csv */

/** Minimal RFC4180 parser: quoted fields, "" escapes, newlines inside quotes. */
function parseCsv(text) {
  const rows = [];
  let row = [], field = '', quoted = false, i = 0;
  if (text.charCodeAt(0) === 0xfeff) i = 1; // strip BOM

  const endField = () => { row.push(field); field = ''; };
  const endRow = () => { endField(); rows.push(row); row = []; };

  for (; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else quoted = false;
      } else field += c;
      continue;
    }
    if (c === '"' && field === '') { quoted = true; continue; }
    if (c === ',') { endField(); continue; }
    if (c === '\r') { if (text[i + 1] === '\n') i++; endRow(); continue; }
    if (c === '\n') { endRow(); continue; }
    field += c;
  }
  if (field !== '' || row.length) endRow();
  return rows.filter((r) => r.some((cell) => cell.trim() !== ''));
}

function readRows(csvPath) {
  const rows = parseCsv(fs.readFileSync(csvPath, 'utf8'));
  if (!rows.length) throw new Error(`${csvPath} is empty`);
  const header = rows[0].map((h) => h.trim().toLowerCase());
  for (const key of ['slug', 'figure', 'kicker']) {
    if (!header.includes(key)) throw new Error(`${csvPath} is missing a "${key}" column`);
  }
  return rows.slice(1).map((cells, idx) => {
    const record = { __line: idx + 2 };
    header.forEach((key, col) => { record[key] = (cells[col] ?? '').trim(); });
    return record;
  });
}

/* ------------------------------------------------------------------ text */

const escapeHtml = (s) =>
  s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

/** Plain text -> template HTML: **bold** becomes <b>, | and \n become line breaks. */
const markup = (s) =>
  escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/\s*(\||\\n)\s*/g, '<br>');

const hasHangul = (s) => /[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-퟿]/.test(s);

/* ------------------------------------------------------------- playwright */

function loadPlaywright() {
  const candidates = ['playwright', 'playwright-core'];
  for (const name of candidates) {
    try { return require(name); } catch { /* try next */ }
  }
  try {
    const globalRoot = execSync('npm root -g', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    for (const name of candidates) {
      try { return require(path.join(globalRoot, name)); } catch { /* try next */ }
    }
  } catch { /* npm unavailable */ }
  throw new Error(
    'Playwright not found. Install it with:\n' +
    '  npm install --prefix tools\n' +
    '  npx playwright install chromium'
  );
}

/**
 * Read a local @font-face stylesheet and make its url() references absolute,
 * so it can be injected into a page served from a different directory.
 */
function readFontCss(cssPath) {
  const css = fs.readFileSync(cssPath, 'utf8');
  const dir = path.dirname(cssPath);
  return css.replace(/url\((['"]?)([^'")]+)\1\)/g, (match, quote, url) => {
    if (/^(https?:|data:|file:)/.test(url)) return match;
    return `url(${pathToFileURL(path.resolve(dir, url)).href})`;
  });
}

/**
 * Fill the template and shrink type until it fits the 1200x630 frame.
 * Runs inside the page, so _template.html stays the single source of truth
 * for the design - this only overrides font-size.
 */
function applyRow([data, limits]) {
  const set = (id, html) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  };

  set('series', data.series);
  const part = document.getElementById('part');
  if (part) {
    if (data.part) part.innerHTML = '&middot; ' + data.part;
    else part.style.display = 'none';
  }
  set('brand', data.brand);
  set('sub', data.sub);
  set('kicker', data.kicker);

  const figure = document.getElementById('figure');
  figure.innerHTML = data.figure + (data.unit ? `<span class="unit" id="unit">${data.unit}</span>` : '');

  const kicker = document.getElementById('kicker');
  const inner = document.querySelector('.inner');
  const bodyStyle = getComputedStyle(document.body);
  const available = document.body.clientWidth
    - parseFloat(bodyStyle.paddingLeft) - parseFloat(bodyStyle.paddingRight);

  const shrink = (el, start, floor, fits) => {
    let size = start;
    el.style.fontSize = `${size}px`;
    while (size > floor && !fits()) {
      size -= 1;
      el.style.fontSize = `${size}px`;
    }
    return size;
  };
  const tooTall = () => inner.scrollHeight > inner.clientHeight;

  // 1. The figure is nowrap, so it must fit the content width.
  let figureSize = shrink(figure, limits.figureMax, limits.figureMin,
    () => figure.scrollWidth <= Math.ceil(available));
  // 2. Then the kicker must not push the block out of the 630px frame.
  const kickerSize = shrink(kicker, limits.kickerMax, limits.kickerMin, () => !tooTall());
  // 3. Still overflowing: give height back by shrinking the figure further.
  if (tooTall()) {
    figureSize = shrink(figure, figureSize, limits.figureMin, () => !tooTall());
  }

  return { figureSize, kickerSize, overflow: tooTall() };
}

/** Wait for the webfont stylesheet to arrive and its faces to finish loading. */
async function waitForFonts(page, timeoutMs = 15000) {
  return page.evaluate(async (deadline) => {
    const start = Date.now();
    // document.fonts.ready resolves immediately while the stylesheet is still
    // in flight (no faces registered yet), so wait for registration first.
    while (document.fonts.size === 0 && Date.now() - start < deadline) {
      await new Promise((r) => setTimeout(r, 50));
    }
    await document.fonts.ready;
    return [...document.fonts]
      .filter((f) => f.status === 'loaded')
      .map((f) => f.family.replace(/^['"]|['"]$/g, ''));
  }, timeoutMs);
}

/* ------------------------------------------------------------------- main */

function buildJobs(opts) {
  let rows = readRows(opts.csv);
  if (opts.only) {
    const wanted = new Set(opts.only);
    const present = new Set(rows.map((r) => r.slug));
    for (const slug of wanted) if (!present.has(slug)) console.warn(`! --only "${slug}" matches no row`);
    rows = rows.filter((r) => wanted.has(r.slug));
  }

  const seen = new Set();
  const jobs = [];
  for (const row of rows) {
    const problem =
      !row.slug ? 'missing slug'
      : !/^[A-Za-z0-9._-]+$/.test(row.slug) ? `invalid slug "${row.slug}" (use letters, digits, . _ -)`
      : seen.has(row.slug) ? `duplicate slug "${row.slug}"`
      : !row.figure ? `row "${row.slug}" has no figure`
      : null;
    if (problem) throw new Error(`${opts.csv}:${row.__line}: ${problem}`);
    seen.add(row.slug);

    const file = path.join(opts.out, `${row.slug}.png`);
    if (opts.skipExisting && fs.existsSync(file)) {
      console.log(`- ${row.slug}.png (exists, skipped)`);
      continue;
    }
    const fields = {
      series: row.series || 'Why Korea Is So Convenient',
      part: row.part || '',
      figure: row.figure,
      unit: row.unit || '',
      kicker: row.kicker || '',
      brand: row.brand || 'KOREA RUNDOWN',
      sub: row.sub || 'korearundown.blogspot.com',
    };
    jobs.push({
      slug: row.slug,
      file,
      korean: hasHangul(Object.values(fields).join('')),
      data: {
        series: markup(fields.series),
        part: escapeHtml(fields.part),
        figure: markup(fields.figure),
        unit: markup(fields.unit),
        kicker: markup(fields.kicker),
        brand: markup(fields.brand),
        sub: markup(fields.sub),
      },
    });
  }
  return jobs;
}

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
    for (const job of jobs) console.log(`would write ${path.relative(process.cwd(), job.file)}`);
    return 0;
  }

  fs.mkdirSync(opts.out, { recursive: true });
  const templateUrl = pathToFileURL(opts.template).href;
  const fontCss = opts.fontCss ? readFontCss(opts.fontCss) : null;

  const { chromium } = loadPlaywright();
  // Honour the standard proxy variables; Chromium does not read them itself.
  const proxyServer = process.env.HTTPS_PROXY || process.env.https_proxy || null;
  const browser = await chromium.launch(proxyServer ? { proxy: { server: proxyServer } } : {});
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: opts.scale,
  });
  // With local fonts there is nothing to fetch; don't stall on a blocked network.
  if (fontCss) await context.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.abort());

  const limits = {
    figureMax: FIGURE_MAX, figureMin: FIGURE_MIN,
    kickerMax: KICKER_MAX, kickerMin: KICKER_MIN,
  };
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

        // Fill first so the faces the copy actually needs start downloading,
        // then re-fit once they are in: metrics change under the real face.
        await page.evaluate(applyRow, [job.data, limits]);
        const loaded = await waitForFonts(page);
        const fitted = await page.evaluate(applyRow, [job.data, limits]);

        if (!loaded.includes('Inter')) missingFonts.add('Inter');
        if (job.korean && !loaded.some((f) => /Noto Sans KR/i.test(f))) missingFonts.add('Noto Sans KR');
        if (fitted.overflow) problems.push(`${job.slug}: copy is too long to fit even at minimum type size`);

        await page.screenshot({ path: job.file, clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT } });

        const notes = [];
        if (fitted.figureSize !== FIGURE_MAX) notes.push(`figure ${fitted.figureSize}px`);
        if (fitted.kickerSize !== KICKER_MAX) notes.push(`kicker ${fitted.kickerSize}px`);
        console.log(`+ ${job.slug}.png${notes.length ? `  (auto-fit: ${notes.join(', ')})` : ''}`);
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
    const list = [...missingFonts].join(', ');
    const message =
      `${list} did not load, so these PNGs use fallback fonts and do not match the ` +
      `existing thumbnails.\n  Check network access to fonts.googleapis.com, or pass ` +
      `--font-css <path> to render from local font files (see tools/README.md).`;
    if (!opts.allowFallbackFonts) {
      throw new Error(`${message}\n  Re-run with --allow-fallback-fonts to keep this output anyway.`);
    }
    console.warn(`! ${message}`);
  }

  console.log(`\n${jobs.length} thumbnail${jobs.length === 1 ? '' : 's'} -> ${path.relative(process.cwd(), opts.out) || '.'}`);
  return problems.length ? 1 : 0;
}

main().then(
  (code) => process.exit(code),
  (err) => { console.error(`error: ${err.message}`); process.exit(1); }
);
