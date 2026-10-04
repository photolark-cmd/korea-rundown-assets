#!/usr/bin/env node
/**
 * One command for the whole reference round: collect, digest, commit, push.
 *
 *   node tools/refs-nightly.mjs
 *
 * Meant for the PC's night schedule. Once the results are pushed, any session
 * can read refs/ straight out of the repo - nobody has to paste a CSV into a
 * chat window. Anything after -- goes to collect-refs.mjs.
 *
 *   node tools/refs-nightly.mjs -- --recent-ratio 30
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const HELP = `Collect references, write the digest, commit and push.

  node tools/refs-nightly.mjs [options] [-- <collect-refs options>]

  --no-push        commit but do not push
  --no-commit      leave the files in the working tree
  --seeds <file>   seed list      (default: refs/seed-queries.txt)
  -h, --help       this message

Defaults passed to collect-refs.mjs: --no-shorts --min-subs 1000, a 180-second
duration floor, a 2-per-channel cap, and the subject exclusions that the
2026-08-27 round showed were needed. Anything after -- is appended, so a later
flag of the same name wins.
Needs YOUTUBE_API_KEY in the environment.
`;

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/* ---------------------------------------------------------------- options */

function parseArgs(argv) {
  const opts = { seeds: 'refs/seed-queries.txt', push: true, commit: true, passthrough: [] };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--') { opts.passthrough = argv.slice(i + 1); break; }
    switch (arg) {
      case '--no-push': opts.push = false; break;
      case '--no-commit': opts.commit = false; break;
      case '--seeds': opts.seeds = argv[++i]; break;
      case '-h': case '--help': opts.help = true; break;
      default: throw new Error(`unknown option: ${arg}`);
    }
  }
  if (!opts.seeds) throw new Error('--seeds needs a value');
  return opts;
}

/**
 * What the 2026-08-27 collection taught us about the ratio rule.
 *
 * views >= subs x 100 measures reach and nothing else, so a dinosaur cartoon
 * channel, a gameplay stream, and a Hindi folk tale all clear it. These are the
 * filters that survived that round; the 100x rule itself stays untouched.
 */
const SUBJECT_FILTERS = [
  '--min-duration', '180',      // 61-second vertical animation clears --no-shorts
  '--max-per-channel', '2',     // one 7k-subscriber channel took 38% of a table
  '--exclude', '키즈|kids|kidz|어린이|동요|만화|cartoon|toy|장난감',
  '--exclude', '게임|gameplay|로블록스|roblox|마인크래프트|minecraft|서브노티카|subnautica|붉은사막',
  '--exclude', '- Topic',
  '--exclude', '[ऀ-ॿ]|hindi|kahani|cerita|dongeng|satwa|nonton',
  '--exclude', '국뽕|대한민국의 위엄|레전드 대한민국|한국인만|외국인 반응',
  '--exclude', '낚시|fishing|손맛|조황',
];

/* ------------------------------------------------------------------ steps */

/** Run a command with its output going straight to this process's console. */
function run(cmd, args) {
  execFileSync(cmd, args, { cwd: ROOT, stdio: 'inherit' });
}

const git = (...args) => execFileSync('git', args, { cwd: ROOT, encoding: 'utf8' }).trim();

function newestCsv() {
  const dir = path.join(ROOT, 'refs');
  const files = fs.readdirSync(dir).filter((f) => /^refs-.*\.csv$/.test(f)).sort();
  if (!files.length) throw new Error('collect-refs.mjs produced no CSV');
  return path.join('refs', files[files.length - 1]);
}

/** Rows in the CSV, minus its header - for the commit message. */
function countRows(csv) {
  const lines = fs.readFileSync(path.join(ROOT, csv), 'utf8').split('\n').filter(Boolean);
  const recent = lines.filter((l) => l.startsWith('recent,')).length;
  return { kept: Math.max(0, lines.length - 1), recent };
}

function commitAndPush(opts, csv) {
  git('add', 'refs');
  // --quiet still sets the exit code, which is what tells us whether anything
  // actually changed; an unchanged run should not make an empty commit.
  const changed = execFileSync('git', ['status', '--porcelain', 'refs'], { cwd: ROOT, encoding: 'utf8' }).trim();
  if (!changed) {
    console.log('  git         nothing changed - no commit');
    return;
  }
  const { kept, recent } = countRows(csv);
  git('commit', '-m', `refs: ${path.basename(csv, '.csv').replace('refs-', '')} - ${kept} kept, ${recent} recent`);
  console.log(`  git         committed ${kept} rows (${recent} recent)`);
  if (!opts.push) return;
  const branch = git('rev-parse', '--abbrev-ref', 'HEAD');
  git('push', '-u', 'origin', 'HEAD');
  console.log(`  git         pushed to ${branch}`);
}

/* ------------------------------------------------------------------- main */

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) { console.log(HELP); return 0; }
  if (!process.env.YOUTUBE_API_KEY) {
    throw new Error('YOUTUBE_API_KEY is not set - the collector cannot run without it');
  }

  run('node', ['tools/collect-refs.mjs', '--seeds', opts.seeds,
    '--no-shorts', '--min-subs', '1000', ...SUBJECT_FILTERS, ...opts.passthrough]);

  const csv = newestCsv();
  run('node', ['tools/analyze-refs.mjs', csv]);

  if (opts.commit) commitAndPush(opts, csv);
  else console.log('  git         skipped (--no-commit)');
  return 0;
}

try {
  process.exit(main());
} catch (err) {
  console.error(`error: ${err.message}`);
  process.exit(1);
}
