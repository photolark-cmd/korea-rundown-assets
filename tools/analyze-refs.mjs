#!/usr/bin/env node
/**
 * Read a collect-refs CSV and write the digest a person would otherwise have
 * to assemble by hand: what broke out recently, which channels do it more than
 * once, what length they run, and which title moves keep showing up.
 *
 *   node tools/analyze-refs.mjs                    # newest refs/refs-*.csv
 *   node tools/analyze-refs.mjs refs/refs-2026-08-26.csv
 */

import fs from 'node:fs';
import path from 'node:path';
import { readRows } from './lib/csv.mjs';

const HELP = `Turn a reference CSV into a digest.

  node tools/analyze-refs.mjs [csv] [options]

  --out <file>     where the digest lands   (default: refs/digest-<날짜>.md)
  --top <n>        rows per table                            (default: 15)
  -h, --help       this message

With no CSV named, the newest refs/refs-*.csv is used.
`;

/* ---------------------------------------------------------------- options */

function parseArgs(argv) {
  const opts = { top: 15 };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const value = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${arg} needs a value`);
      return v;
    };
    switch (arg) {
      case '--out': opts.out = value(); break;
      case '--top': opts.top = Number(value()); break;
      case '-h': case '--help': opts.help = true; break;
      default:
        if (arg.startsWith('--')) throw new Error(`unknown option: ${arg}`);
        rest.push(arg);
    }
  }
  opts.csv = rest[0];
  if (!Number.isInteger(opts.top) || opts.top < 1) throw new Error('--top must be 1 or more');
  return opts;
}

function newestCsv(dir = 'refs') {
  if (!fs.existsSync(dir)) throw new Error(`no ${dir}/ directory - run collect-refs.mjs first`);
  const files = fs.readdirSync(dir).filter((f) => /^refs-.*\.csv$/.test(f)).sort();
  if (!files.length) throw new Error(`no refs-*.csv in ${dir}/ - run collect-refs.mjs first`);
  return path.join(dir, files[files.length - 1]);
}

/* ------------------------------------------------------------------ shape */

const seconds = (clock) => String(clock).split(':').reduce((acc, part) => acc * 60 + Number(part || 0), 0);

function load(csvPath) {
  return readRows(csvPath, ['ratio', 'views', 'title']).map((r) => ({
    recent: r.window === 'recent',
    ratio: Number(r.ratio),
    views: Number(r.views),
    subs: Number(r.subs),
    ageDays: Number(r.age_days),
    seconds: seconds(r.duration),
    published: r.published,
    channel: r.channel,
    title: r.title,
    url: r.url,
  })).filter((r) => Number.isFinite(r.ratio));
}

const median = (xs) => {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
};

/* Title moves worth counting. Grouped so near-synonyms land in one row. */
const HOOKS = [
  ['충격·소름', /충격|소름|경악|섬뜩|무서운/],
  ['실화·진짜', /실화|진짜|정말|사실은|알고보면|알고 보면/],
  ['왜·이유', /왜\s|이유|때문/],
  ['최상급', /최강|최대|최초|가장|제일|1위|역대급/],
  ['만약·가정', /만약|만약에|했다면|생긴다면|살아있다면/],
  ['정체·비밀', /정체|비밀|미스터리|밝혀|충격적인 진실|진실/],
  ['공포·위험', /위험|공격|잡아먹|포식|괴물|괴수/],
  ['크기·숫자', /\d+\s*(m|미터|톤|kg|배|년|만년|억년)/i],
  ['물음표', /\?/],
  ['대괄호·꺾쇠', /[[\]〈〉《》]/],
];

const PARTICLES = /(은|는|이|가|을|를|의|에|도|만|과|와|으로|로|에서|까지|부터|보다|처럼|라는|이라는)$/;
// Korean verbs inflect, so 멸종했다 / 멸종하지 / 멸종한 would otherwise count as
// three different words. Stripping the common tails collapses them onto the noun.
const VERB_TAILS = /(했을까|했습니다|합니다|시켰다|시킨|당했다|당한|하지|하는|한다|했다|되는|된다|됐다|한|해)$/;
const STOPWORDS = new Set([
  '그리고', '하지만', '그런데', '진짜', '정말', '실제', '우리', '사람', '이야기', '영상',
  '것은', '있는', '없는', '했던', '하는', '되는', '가장', '모든', '다른', '이런', '그런',
]);

function topicWords(rows, limit) {
  const counts = new Map();
  for (const r of rows) {
    const seen = new Set();
    for (const raw of r.title.split(/[^0-9A-Za-z가-힣]+/)) {
      if (raw.length < 2) continue;
      if (/^\d+$/.test(raw)) continue;
      const word = raw.replace(VERB_TAILS, '').replace(PARTICLES, '');
      if (word.length < 2 || STOPWORDS.has(word)) continue;
      if (seen.has(word)) continue;      // one title, one vote
      seen.add(word);
      counts.set(word, (counts.get(word) ?? 0) + 1);
    }
  }
  return [...counts].filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).slice(0, limit);
}

const BUCKETS = [
  ['1분 이하 (쇼츠)', 0, 60],
  ['1~5분', 60, 300],
  ['5~10분', 300, 600],
  ['10~20분', 600, 1200],
  ['20~40분', 1200, 2400],
  ['40분 이상', 2400, Infinity],
];

/* ----------------------------------------------------------------- output */

const num = (n) => Math.round(n).toLocaleString('en-US');
const ratioKo = (r) => (r >= 10 ? `${Math.round(r)}배` : `${r.toFixed(1)}배`);
const clock = (s) => (s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : '');
const esc = (s) => s.replace(/\|/g, '\\|');

const table = (rows) => [
  '| 배수 | 조회수 | 구독자 | 지난날 | 길이 | 채널 | 제목 |',
  '|---:|---:|---:|---:|---:|---|---|',
  ...rows.map((r) => `| ${[
    ratioKo(r.ratio), num(r.views), num(r.subs), `${r.ageDays}일`, clock(r.seconds),
    esc(r.channel), `[${esc(r.title)}](${r.url})`,
  ].join(' | ')} |`),
];

function digest(rows, opts, csvPath) {
  const today = new Date().toISOString().slice(0, 10);
  const recent = rows.filter((r) => r.recent);
  const older = rows.filter((r) => !r.recent);
  const out = [
    `# 레퍼런스 다이제스트 — ${today}`,
    '',
    `원본 \`${csvPath}\` · 채택 ${rows.length}편 (최근 창 ${recent.length}편)`,
    '',
  ];

  if (!rows.length) {
    out.push('문턱을 넘은 영상이 없습니다. `--recent-ratio`를 낮추거나 시드 검색어를 손보세요.', '');
    return out.join('\n');
  }

  out.push(
    '## 최근 창부터',
    '',
    ...(recent.length
      ? [
        `중앙값 ${ratioKo(median(recent.map((r) => r.ratio)))} · ` +
          `최고 ${ratioKo(Math.max(...recent.map((r) => r.ratio)))}`,
        '',
        ...table(recent.slice(0, opts.top)),
      ]
      : ['최근 창에 채택된 영상이 없습니다. 문턱(`--recent-ratio`) 문제인지 먼저 보세요.']),
    '',
    `## 그 이전 상위 ${Math.min(opts.top, older.length)}편`,
    '',
    ...(older.length ? table(older.slice(0, opts.top)) : ['없음.']),
    '',
  );

  // A channel that breaks out twice is a format, not an accident - those are
  // the ones worth watching end to end.
  const byChannel = new Map();
  for (const r of rows) byChannel.set(r.channel, [...(byChannel.get(r.channel) ?? []), r]);
  const repeats = [...byChannel].filter(([, rs]) => rs.length > 1)
    .sort((a, b) => b[1].length - a[1].length);
  out.push('## 두 번 이상 터진 채널', '');
  if (repeats.length) {
    out.push(
      '| 채널 | 편수 | 중앙 배수 | 구독자 |',
      '|---|---:|---:|---:|',
      ...repeats.map(([name, rs]) =>
        `| ${esc(name)} | ${rs.length} | ${ratioKo(median(rs.map((r) => r.ratio)))} | ${num(rs[0].subs)} |`),
      '',
      '이쪽은 우연이 아니라 포맷입니다. `--channel @핸들`로 전체 업로드를 훑어 보세요.',
      '',
    );
  } else {
    out.push('아직 없습니다. 수집을 몇 번 더 돌리면 반복 채널이 드러납니다.', '');
  }

  const timed = rows.filter((r) => r.seconds > 0);
  out.push('## 길이', '');
  if (timed.length) {
    out.push(
      '| 구간 | 편수 | 중앙 배수 |',
      '|---|---:|---:|',
      ...BUCKETS.map(([label, lo, hi]) => {
        const inBucket = timed.filter((r) => r.seconds > lo && r.seconds <= hi);
        return inBucket.length
          ? `| ${label} | ${inBucket.length} | ${ratioKo(median(inBucket.map((r) => r.ratio)))} |`
          : null;
      }).filter(Boolean),
      '',
    );
  } else {
    out.push('길이 정보가 없습니다.', '');
  }

  out.push('## 제목이 쓰는 수', '', '| 수 | 편수 | 비중 |', '|---|---:|---:|');
  for (const [label, re] of HOOKS) {
    const hits = rows.filter((r) => re.test(r.title)).length;
    if (hits) out.push(`| ${label} | ${hits} | ${Math.round((hits / rows.length) * 100)}% |`);
  }
  out.push('');

  const words = topicWords(rows, 20);
  if (words.length) {
    out.push(
      '## 반복되는 소재어',
      '',
      words.map(([word, n]) => `${word} ${n}`).join(' · '),
      '',
      '제목에 두 번 이상 나온 단어입니다. 시드 검색어를 다음 회차에 여기 맞춰 조이세요.',
      '',
    );
  }

  out.push(
    '---',
    '',
    '배수는 **오늘의 구독자 수** 기준이고 API가 유효숫자 3자리로 반올림해 줍니다.',
    '최근 창 영상은 조회수가 덜 쌓여 배수가 낮게 잡히므로, 같은 배수라도 최근 것이 더 셉니다.',
    '',
  );
  return out.join('\n');
}

/* ------------------------------------------------------------------- main */

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) { console.log(HELP); return 0; }

  const csvPath = opts.csv ?? newestCsv();
  const rows = load(csvPath);
  const outPath = opts.out ?? path.join(path.dirname(csvPath), `digest-${new Date().toISOString().slice(0, 10)}.md`);
  fs.writeFileSync(outPath, digest(rows, opts, csvPath));

  console.log(`  read        ${csvPath} - ${rows.length} rows`);
  console.log(`  wrote       ${outPath}`);
  return 0;
}

try {
  process.exit(main());
} catch (err) {
  console.error(`error: ${err.message}`);
  process.exit(1);
}
