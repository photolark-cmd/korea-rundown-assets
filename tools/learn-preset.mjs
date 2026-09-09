#!/usr/bin/env node
/**
 * Learn a correction preset from before/after pairs.
 *
 * The pairs are the training material: the same photo straight out of the
 * camera and the same photo after you finished editing it. For each pair we
 * match the histograms channel by channel, which recovers whatever the edit did
 * to white balance, exposure, black point and tone curve as a single 256-entry
 * table per channel. Averaging those tables over the pairs is the preset.
 *
 * Usage:
 *   node tools/learn-preset.mjs <pairs-dir> --name "야간 편의점" [--id cvs-mine]
 *
 * <pairs-dir> may be either
 *   pairs/before/IMG_1234.jpg + pairs/after/IMG_1234.jpg   (matching names)
 *   pairs/IMG_1234.before.jpg + pairs/IMG_1234.after.jpg   (flat)
 *
 * The pair must be the same crop and framing — crop and rotate after
 * correcting, not before, or the histograms describe different scenes.
 */

import fs from 'node:fs';
import path from 'node:path';
import { launchBrowser } from './lib/browser.mjs';

const WORK_EDGE = 480;   // images are analysed downscaled; histograms don't need detail
const SMOOTH = 9;        // moving-average window applied to the learned curve

function parseArgs(argv) {
  const out = { dir: null, name: null, id: null, presets: 'tools/photo-fix/presets.js' };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--name') out.name = argv[++i];
    else if (a === '--id') out.id = argv[++i];
    else if (a === '--out') out.presets = argv[++i];
    else if (!a.startsWith('-') && !out.dir) out.dir = a;
    else throw new Error(`알 수 없는 인자: ${a}`);
  }
  if (!out.dir) throw new Error('사용법: node tools/learn-preset.mjs <pairs-dir> --name "이름"');
  if (!out.name) out.name = path.basename(path.resolve(out.dir));
  if (!out.id) out.id = 'learned-' + path.basename(path.resolve(out.dir)).toLowerCase().replace(/[^a-z0-9]+/g, '-');
  return out;
}

const IMG = /\.(jpe?g|png|webp)$/i;

function collectPairs(dir) {
  const beforeDir = path.join(dir, 'before');
  const afterDir = path.join(dir, 'after');
  const pairs = [];
  if (fs.existsSync(beforeDir) && fs.existsSync(afterDir)) {
    const afters = new Map(
      fs.readdirSync(afterDir).filter(f => IMG.test(f)).map(f => [f.replace(IMG, ''), path.join(afterDir, f)]),
    );
    for (const f of fs.readdirSync(beforeDir).filter(f => IMG.test(f))) {
      const key = f.replace(IMG, '');
      if (afters.has(key)) pairs.push({ key, before: path.join(beforeDir, f), after: afters.get(key) });
    }
  } else {
    const files = fs.readdirSync(dir).filter(f => IMG.test(f));
    const afters = new Map(
      files.filter(f => /\.after\.[^.]+$/i.test(f)).map(f => [f.replace(/\.after\.[^.]+$/i, ''), path.join(dir, f)]),
    );
    for (const f of files.filter(f => /\.before\.[^.]+$/i.test(f))) {
      const key = f.replace(/\.before\.[^.]+$/i, '');
      if (afters.has(key)) pairs.push({ key, before: path.join(dir, f), after: afters.get(key) });
    }
  }
  return pairs.sort((a, b) => a.key.localeCompare(b.key));
}

function dataUrl(file) {
  const ext = path.extname(file).toLowerCase();
  const mime = ext === '.png' ? 'image/png' : ext === '.webp' ? 'image/webp' : 'image/jpeg';
  return `data:${mime};base64,${fs.readFileSync(file).toString('base64')}`;
}

/* Runs inside the browser: decode both images and match their histograms. */
async function matchPair(page, beforeUrl, afterUrl) {
  return page.evaluate(async ([bUrl, aUrl, edge]) => {
    const load = src => new Promise((res, rej) => {
      const img = new Image();
      img.onload = () => res(img);
      img.onerror = () => rej(new Error('디코딩 실패'));
      img.src = src;
    });
    const [b, a] = await Promise.all([load(bUrl), load(aUrl)]);
    const k = Math.min(1, edge / Math.max(b.naturalWidth, b.naturalHeight));
    const w = Math.max(1, Math.round(b.naturalWidth * k));
    const h = Math.max(1, Math.round(b.naturalHeight * k));

    const read = img => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      c.getContext('2d', { willReadFrequently: true }).drawImage(img, 0, 0, w, h);
      return c.getContext('2d').getImageData(0, 0, w, h).data;
    };
    const bd = read(b), ad = read(a);

    const cdf = data => {
      const out = [];
      for (let c = 0; c < 3; c++) {
        const hist = new Float64Array(256);
        for (let i = c; i < data.length; i += 4) hist[data[i]]++;
        let acc = 0;
        const total = data.length / 4;
        const table = new Float64Array(256);
        for (let v = 0; v < 256; v++) { acc += hist[v]; table[v] = acc / total; }
        out.push(table);
      }
      return out;
    };
    const cb = cdf(bd), ca = cdf(ad);

    const lut = [];
    for (let c = 0; c < 3; c++) {
      const t = new Array(256);
      let j = 0;
      for (let v = 0; v < 256; v++) {
        while (j < 255 && ca[c][j] < cb[c][v]) j++;
        t[v] = j;
      }
      lut.push(t);
    }
    const ratio = (x, y) => Math.abs(x / y - 1);
    return {
      lut,
      aspectMismatch: ratio(b.naturalWidth / b.naturalHeight, a.naturalWidth / a.naturalHeight) > 0.02,
    };
  }, [beforeUrl, afterUrl, WORK_EDGE]);
}

function smoothMonotonic(curve) {
  const half = (SMOOTH - 1) / 2;
  const out = new Array(256);
  for (let i = 0; i < 256; i++) {
    let sum = 0, n = 0;
    for (let j = Math.max(0, i - half); j <= Math.min(255, i + half); j++) { sum += curve[j]; n++; }
    out[i] = sum / n;
  }
  let last = 0;
  for (let i = 0; i < 256; i++) {
    last = Math.max(last, Math.round(out[i]));
    out[i] = Math.min(255, last);
  }
  return out;
}

function readPresets(file) {
  if (!fs.existsSync(file)) return [];
  const src = fs.readFileSync(file, 'utf8');
  const m = src.match(/window\.LEARNED_PRESETS\s*=\s*(\[[\s\S]*\]);/);
  if (!m) return [];
  try { return JSON.parse(m[1]); } catch { return []; }
}

function writePresets(file, presets) {
  const body = presets
    .map(p => `  {"id":${JSON.stringify(p.id)},"name":${JSON.stringify(p.name)},"lut":[${p.lut.map(c => `[${c.join(',')}]`).join(',')}]}`)
    .join(',\n');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file,
    '/* Generated by tools/learn-preset.mjs — do not edit by hand. */\n' +
    'window.LEARNED_PRESETS = [\n' + body + '\n];\n');
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const pairs = collectPairs(args.dir);
  if (!pairs.length) {
    console.error(`${args.dir} 에서 before/after 쌍을 찾지 못했습니다.`);
    console.error('  before/ + after/ 하위 폴더에 같은 파일명으로 넣거나,');
    console.error('  이름.before.jpg / 이름.after.jpg 형식으로 넣으세요.');
    process.exit(1);
  }
  console.log(`${pairs.length}쌍 발견 — 분석 시작`);

  const browser = await launchBrowser();
  const page = await browser.newPage();
  await page.setContent('<!doctype html><meta charset="utf-8"><title>learn</title>');

  const luts = [];
  for (const pair of pairs) {
    try {
      const res = await matchPair(page, dataUrl(pair.before), dataUrl(pair.after));
      if (res.aspectMismatch) {
        console.warn(`  ! ${pair.key} — 원본과 보정본의 가로세로 비율이 다릅니다 (크롭됨). 건너뜁니다.`);
        continue;
      }
      luts.push({ key: pair.key, lut: res.lut });
      console.log(`  · ${pair.key}`);
    } catch (e) {
      console.warn(`  ! ${pair.key} — ${e.message}`);
    }
  }
  await browser.close();

  if (!luts.length) { console.error('쓸 수 있는 쌍이 없습니다.'); process.exit(1); }

  const avg = [0, 1, 2].map(c => {
    const curve = new Array(256).fill(0);
    for (const { lut } of luts) for (let v = 0; v < 256; v++) curve[v] += lut[c][v] / luts.length;
    return smoothMonotonic(curve);
  });

  // Flag pairs whose edit disagrees with the rest; they usually mean a photo
  // that was edited for a different reason, and they drag the average around.
  if (luts.length > 2) {
    const scored = luts.map(({ key, lut }) => {
      let d = 0;
      for (let c = 0; c < 3; c++) for (let v = 0; v < 256; v++) d += Math.abs(lut[c][v] - avg[c][v]);
      return { key, dev: d / (3 * 256) };
    }).sort((a, b) => b.dev - a.dev);
    const mean = scored.reduce((s, x) => s + x.dev, 0) / scored.length;
    const odd = scored.filter(x => x.dev > mean * 2 && x.dev > 8);
    if (odd.length) {
      console.log('\n다른 쌍들과 보정 방향이 크게 다른 사진 (빼는 편이 나을 수 있음):');
      for (const o of odd) console.log(`  ${o.key}  (평균과의 차이 ${o.dev.toFixed(1)}단계)`);
    }
    console.log(`\n쌍들의 일관성: 평균 ${mean.toFixed(1)}단계 차이` +
      (mean > 25 ? ' — 편차가 큽니다. 상황별로 폴더를 나눠 따로 학습시키세요.' : ''));
  }

  const presets = readPresets(args.presets).filter(p => p.id !== args.id);
  presets.push({ id: args.id, name: args.name, lut: avg });
  writePresets(args.presets, presets);
  console.log(`\n${args.presets} 에 "${args.name}" (id: ${args.id}) 저장 — ${luts.length}쌍 학습`);
  console.log('tools/photo-fix/index.html 을 열면 프리셋 목록에 나타납니다.');
}

main().catch(e => { console.error(e.message); process.exit(1); });
