#!/usr/bin/env node
/**
 * Pull stills out of an edited video for use as blog body images.
 *
 *   node tools/grab-frames.mjs <video> --sheet          # contact sheet, to pick moments
 *   node tools/grab-frames.mjs <video> --at 0:12,1:47   # grab those moments
 *
 * The finished video already carries the captions and graphics from editing,
 * so a frame is an on-brand body image with no new design work.
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { launchBrowser } from './lib/browser.mjs';

const HELP = `Pull stills out of an edited video.

  node tools/grab-frames.mjs <video> [options]

  --sheet              contact sheet of evenly spaced frames, to pick moments
  --tiles <n>          frames on the contact sheet (multiple of 3 reads best)\n                       (default: 12)
  --at <t,t,...>       grab these moments (0:12 · 1:47.5 · 95)
  --out <dir>          output directory                     (default: figures)
  --prefix <name>      filename prefix       (default: the video's basename)
  --width <px>         output width                         (default: 1280)
  --ffmpeg <path>      ffmpeg binary, if not on PATH
  -h, --help           this message

Grabbed frames land in figures/ so the repo's raw.githubusercontent URLs host
them — push before running build-post.mjs.
`;

/* ---------------------------------------------------------------- options */

function parseArgs(argv) {
  const opts = { out: 'figures', width: 1280, tiles: 12 };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const value = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${arg} needs a value`);
      return v;
    };
    switch (arg) {
      case '--sheet': opts.sheet = true; break;
      case '--tiles': opts.tiles = Number(value()); break;
      case '--at': opts.at = value().split(',').map((s) => s.trim()).filter(Boolean); break;
      case '--out': opts.out = value(); break;
      case '--prefix': opts.prefix = value(); break;
      case '--width': opts.width = Number(value()); break;
      case '--ffmpeg': opts.ffmpeg = value(); break;
      case '-h': case '--help': opts.help = true; break;
      default:
        if (arg.startsWith('--')) throw new Error(`unknown option: ${arg}`);
        rest.push(arg);
    }
  }
  opts.video = rest[0];
  if (!Number.isInteger(opts.width) || opts.width < 320) throw new Error('--width must be at least 320');
  if (!Number.isInteger(opts.tiles) || opts.tiles < 2) throw new Error('--tiles must be at least 2');
  return opts;
}

/* ----------------------------------------------------------------- ffmpeg */

function findFfmpeg(explicit) {
  const tried = [];
  const candidates = [];
  if (explicit) candidates.push(explicit);
  candidates.push('ffmpeg');
  // Playwright ships one; handy when the system has none.
  const pw = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  if (fs.existsSync(pw)) {
    for (const d of fs.readdirSync(pw).filter((n) => n.startsWith('ffmpeg'))) {
      for (const bin of ['ffmpeg-linux', 'ffmpeg-mac', 'ffmpeg-win64.exe', 'ffmpeg.exe']) {
        candidates.push(path.join(pw, d, bin));
      }
    }
  }
  for (const c of candidates) {
    try {
      execFileSync(c, ['-version'], { stdio: 'ignore' });
      return c;
    } catch { tried.push(c); }
  }
  throw new Error(
    'ffmpeg not found. Install it, or pass --ffmpeg <path>.\n' +
    `  tried: ${tried.join(', ')}`
  );
}

const run = (bin, args) => execFileSync(bin, args, { stdio: ['ignore', 'pipe', 'pipe'] });

/** "1:47.5" | "95" -> seconds */
function toSeconds(t) {
  const parts = String(t).split(':').map(Number);
  if (parts.some((n) => !Number.isFinite(n))) throw new Error(`bad timestamp: ${t}`);
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

const stamp = (sec) => {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const r = (s % 60).toFixed(1).replace(/\.0$/, '');
  return `${m}m${String(r).padStart(2, '0')}s`.replace(/[.]/g, '_');
};

function probeDuration(ffmpeg, video) {
  // No ffprobe in some minimal builds - read it off ffmpeg's own report.
  let out = '';
  try { run(ffmpeg, ['-i', video, '-f', 'null', '-']); }
  catch (e) { out = `${e.stderr || ''}${e.stdout || ''}`; }
  const m = out.match(/Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)/);
  if (!m) return null;
  return Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]);
}

/* ------------------------------------------------------------------- main */

/** mm:ss label for a contact-sheet tile. */
const stampLabel = (sec) => {
  const m = Math.floor(sec / 60);
  const r = Math.round(sec % 60);
  return `${m}:${String(r).padStart(2, '0')}`;
};

/** Lay the frames out in a labelled grid and screenshot it. */
async function composeSheet(shots, outFile) {
  // Inlined as data URIs: a setContent page sits on about:blank, where
  // file:// images do not load.
  const tiles = shots.map(({ file, label }) => `
    <figure>
      <img src="data:image/png;base64,${fs.readFileSync(file).toString('base64')}">
      <figcaption>${label}</figcaption>
    </figure>`).join('');
  const html = `<meta charset="utf-8"><style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{background:#0E2A33;padding:16px;display:grid;grid-template-columns:repeat(3,1fr);gap:12px;
         font-family:-apple-system,'Noto Sans KR',sans-serif;width:1512px}
    figure{position:relative;line-height:0}
    img{width:100%;height:auto;border-radius:6px;display:block}
    figcaption{position:absolute;left:8px;top:8px;line-height:1.2;
               background:rgba(0,0,0,.7);color:#fff;font-size:20px;font-weight:700;
               padding:4px 9px;border-radius:4px;font-variant-numeric:tabular-nums}
  </style>${tiles}`;

  const browser = await launchBrowser();
  try {
    const page = await browser.newPage({ viewport: { width: 1512, height: 800 } });
    await page.setContent(html, { waitUntil: 'load' });
    const broken = await page.evaluate(() =>
      [...document.images].filter((i) => !i.complete || i.naturalWidth === 0).length);
    if (broken) throw new Error(`${broken} frame(s) failed to load into the contact sheet`);
    const el = await page.$('body');
    await el.screenshot({ path: outFile });
  } finally {
    await browser.close();
  }
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help || !opts.video) { process.stdout.write(HELP); return opts.video ? 0 : 1; }
  if (!fs.existsSync(opts.video)) throw new Error(`video not found: ${opts.video}`);
  if (!opts.sheet && !opts.at) throw new Error('pass --sheet to pick moments, or --at <times> to grab them');

  const ffmpeg = findFfmpeg(opts.ffmpeg);
  const prefix = opts.prefix || path.basename(opts.video).replace(/\.[^.]+$/, '');
  fs.mkdirSync(opts.out, { recursive: true });

  const duration = probeDuration(ffmpeg, opts.video);
  if (duration) console.log(`  video ${Math.floor(duration / 60)}m${String(Math.round(duration % 60)).padStart(2, '0')}s`);

  if (opts.sheet) {
    if (!duration) throw new Error('could not read the video duration; use --at instead');
    const step = duration / (opts.tiles + 1);
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'frames-'));
    const shots = [];
    for (let n = 1; n <= opts.tiles; n++) {
      const sec = step * n;
      const file = path.join(tmp, `${String(n).padStart(2, '0')}.png`);
      // Only -ss/scale/png are used: minimal ffmpeg builds (Playwright's, for
      // one) ship without fps/drawtext/tile, so the grid is composed below.
      run(ffmpeg, ['-y', '-ss', String(sec), '-i', opts.video,
        '-frames:v', '1', '-vf', 'scale=480:-2', file]);
      shots.push({ file, label: stampLabel(sec) });
    }
    const out = path.join(opts.out, `${prefix}-sheet.png`);
    await composeSheet(shots, out);
    fs.rmSync(tmp, { recursive: true, force: true });
    console.log(`+ ${out}  (${opts.tiles} moments)`);
    console.log(`\n  시트를 보고 타임스탬프를 골라서 다시 실행하세요:`);
    console.log(`  node tools/grab-frames.mjs ${opts.video} --at 0:12,1:47`);
    return 0;
  }

  for (const t of opts.at) {
    const sec = toSeconds(t);
    const file = path.join(opts.out, `${prefix}-${stamp(sec)}.png`);
    run(ffmpeg, [
      '-y', '-ss', String(sec), '-i', opts.video,
      '-frames:v', '1', '-vf', `scale=${opts.width}:-2`, file,
    ]);
    const kb = Math.round(fs.statSync(file).size / 1024);
    console.log(`+ ${file}  (${t} → ${opts.width}px, ${kb}KB)`);
  }
  console.log(`\n  figures/ 를 커밋·푸시하면 raw.githubusercontent 주소로 본문에 쓸 수 있습니다.`);
  return 0;
}

main().then(
  (code) => process.exit(code),
  (err) => { console.error(`error: ${err.message}`); process.exit(1); }
);
