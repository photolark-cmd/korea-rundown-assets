#!/usr/bin/env node
/**
 * Collect science-storytelling videos that outperformed their own channel.
 *
 *   node tools/collect-refs.mjs --seeds refs/seed-queries.txt
 *   node tools/collect-refs.mjs --query "우주 썰" --channel @some-handle
 *
 * The keeper rule is one line: views >= subscribers x 100 (--ratio). A video
 * that clears it travelled well past the channel's own audience, which is the
 * only reason it is worth studying as a reference.
 *
 * Needs a YouTube Data API v3 key in YOUTUBE_API_KEY (or --key).
 */

import fs from 'node:fs';
import path from 'node:path';

const API = 'https://www.googleapis.com/youtube/v3';

const HELP = `Collect reference videos whose views beat their channel's subscribers.

  node tools/collect-refs.mjs [options]

  --seeds <file>       queries and channels, one per line (# comments allowed)
  --query <text>       search term - repeatable
  --channel <id|@handle|url>
                       sweep one channel's uploads - repeatable
  --ratio <n>          keep views / subscribers >= n            (default: 100)
  --recent <days>      the priority window: these sort first and get their
                       own search pass. 0 turns it off           (default: 30)
  --recent-ratio <n>   bar inside that window          (default: same --ratio)
  --since <YYYY-MM-DD> ignore anything published before this date
  --pages <n>          search result pages per query, 50 each   (default: 2)
  --recent-pages <n>   pages for the recent-window pass          (default: 1)
  --uploads <n>        newest uploads to read per channel       (default: 200)
  --min-subs <n>       floor on subscribers - drops 200-sub channels that
                       clear 100x on one fluke                   (default: 0)
  --min-views <n>      floor on views                            (default: 0)
  --no-shorts          drop anything 60 seconds or under
  --exclude <pattern>  drop titles or channels matching this (case-insensitive
                       regex) - repeatable. The ratio rule says nothing about
                       subject, so kids' cartoons and gameplay clear it easily
  --region <cc>        search region                            (default: KR)
  --lang <code>        search relevance language                (default: ko)
  --out <dir>          where the CSV and Markdown land          (default: refs)
  --name <slug>        output basename            (default: refs-<today>)
  --key <key>          API key, if not in YOUTUBE_API_KEY
  --dry-run            print the plan and the quota it would cost
  -h, --help           this message

Recency: a search ordered by view count is dominated by years-old hits, so the
last 30 days get a second search pass of their own with a date bound. Views
also need time to pile up, which means a 3-day-old video clears a lower ratio
than an old one at the same trajectory - if the recent section comes back thin,
--recent-ratio is the knob.

Quota: a search page costs 100 units, every other call costs 1, and the free
daily allowance is 10,000. With the defaults a query costs 300 - two general
pages plus one recent page - so about 30 queries a day. Channel sweeps are
nearly free; prefer them once you know whose work you are studying.
`;

/* ---------------------------------------------------------------- options */

function parseArgs(argv) {
  const opts = {
    queries: [], channels: [], ratio: 100, recent: 30, pages: 2, recentPages: 1,
    uploads: 200, minSubs: 0, minViews: 0, region: 'KR', lang: 'ko', out: 'refs',
    exclude: [],
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const value = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${arg} needs a value`);
      return v;
    };
    switch (arg) {
      case '--seeds': opts.seeds = value(); break;
      case '--query': opts.queries.push(value()); break;
      case '--channel': opts.channels.push(value()); break;
      case '--ratio': opts.ratio = Number(value()); break;
      case '--recent': opts.recent = Number(value()); break;
      case '--recent-ratio': opts.recentRatio = Number(value()); break;
      case '--since': opts.since = value(); break;
      case '--pages': opts.pages = Number(value()); break;
      case '--recent-pages': opts.recentPages = Number(value()); break;
      case '--uploads': opts.uploads = Number(value()); break;
      case '--min-subs': opts.minSubs = Number(value()); break;
      case '--min-views': opts.minViews = Number(value()); break;
      case '--no-shorts': opts.noShorts = true; break;
      case '--exclude': opts.exclude.push(value()); break;
      case '--region': opts.region = value(); break;
      case '--lang': opts.lang = value(); break;
      case '--out': opts.out = value(); break;
      case '--name': opts.name = value(); break;
      case '--key': opts.key = value(); break;
      case '--dry-run': opts.dryRun = true; break;
      case '-h': case '--help': opts.help = true; break;
      default: throw new Error(`unknown option: ${arg}`);
    }
  }
  if (opts.seeds) readSeeds(opts.seeds, opts);
  if (!(opts.ratio > 0)) throw new Error('--ratio must be a positive number');
  if (!Number.isInteger(opts.recent) || opts.recent < 0) throw new Error('--recent must be 0 or more days');
  if (opts.recentRatio !== undefined && !(opts.recentRatio > 0)) throw new Error('--recent-ratio must be a positive number');
  if (!Number.isInteger(opts.pages) || opts.pages < 1) throw new Error('--pages must be 1 or more');
  if (!Number.isInteger(opts.recentPages) || opts.recentPages < 1) throw new Error('--recent-pages must be 1 or more');
  if (!Number.isInteger(opts.uploads) || opts.uploads < 1) throw new Error('--uploads must be 1 or more');
  if (opts.since && !/^\d{4}-\d{2}-\d{2}$/.test(opts.since)) throw new Error('--since wants YYYY-MM-DD');
  opts.excludeRe = opts.exclude.map((p) => {
    try { return new RegExp(p, 'i'); } catch { throw new Error(`--exclude is not a valid regex: ${p}`); }
  });
  opts.recentRatio ??= opts.ratio;
  // The window boundary is fixed once, so every row is measured against the
  // same instant even if the sweep runs for a while.
  opts.recentAfter = opts.recent
    ? new Date(Date.now() - opts.recent * 86400000).toISOString()
    : undefined;
  opts.name ||= `refs-${new Date().toISOString().slice(0, 10)}`;
  return opts;
}

/** A seed file is queries, one per line; lines starting with @, UC, or a URL are channels. */
function readSeeds(file, opts) {
  const lines = fs.readFileSync(file, 'utf8').split('\n')
    .map((l) => l.replace(/#.*$/, '').trim())
    .filter(Boolean);
  for (const line of lines) {
    if (/^(@|UC[\w-]{22}$|https?:\/\/)/.test(line)) opts.channels.push(line);
    else opts.queries.push(line);
  }
}

/* -------------------------------------------------------------------- api */

let quota = 0;

async function api(endpoint, params, cost = 1) {
  const url = new URL(`${API}/${endpoint}`);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  }
  const res = await fetch(url);
  quota += cost;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(`${endpoint} ${res.status}: ${body?.error?.message ?? res.statusText}`);
    // The caller tells a dead handle apart from a spent quota by this.
    err.reason = body?.error?.errors?.[0]?.reason;
    throw err;
  }
  return body;
}

/**
 * Search pages for one query, ordered by view count.
 *
 * `after` bounds the pass by publish date. Ordering by views without a bound
 * returns the all-time hits for the term, which is why the recent window needs
 * a pass of its own rather than a filter over these results.
 */
async function searchVideos(query, opts, { pages, after } = {}) {
  const ids = [];
  let pageToken;
  const publishedAfter = [after, opts.since && `${opts.since}T00:00:00Z`]
    .filter(Boolean).sort().pop();
  for (let page = 0; page < (pages ?? opts.pages); page++) {
    const body = await api('search', {
      key: opts.key, part: 'id', type: 'video', q: query, maxResults: 50,
      order: 'viewCount', regionCode: opts.region, relevanceLanguage: opts.lang,
      publishedAfter,
      pageToken,
    }, 100);
    for (const item of body.items ?? []) if (item.id?.videoId) ids.push(item.id.videoId);
    pageToken = body.nextPageToken;
    if (!pageToken) break;
  }
  return ids;
}

/** Resolve @handle / channel URL / UC... id to a channel id. */
async function resolveChannel(ref, opts) {
  if (/^UC[\w-]{22}$/.test(ref)) return ref;
  const handle = ref.startsWith('http')
    ? decodeURIComponent(new URL(ref).pathname.split('/').filter(Boolean).pop() ?? '')
    : ref;
  if (/^UC[\w-]{22}$/.test(handle)) return handle;
  const body = await api('channels', {
    key: opts.key, part: 'id', forHandle: handle.startsWith('@') ? handle : `@${handle}`,
  });
  const id = body.items?.[0]?.id;
  if (!id) throw new Error(`could not resolve channel: ${ref}`);
  return id;
}

/** Newest uploads for a channel, walking its uploads playlist. */
async function channelUploads(channelId, opts) {
  const info = await api('channels', { key: opts.key, part: 'contentDetails', id: channelId });
  const playlist = info.items?.[0]?.contentDetails?.relatedPlaylists?.uploads;
  if (!playlist) throw new Error(`no uploads playlist for ${channelId}`);
  const ids = [];
  let pageToken;
  while (ids.length < opts.uploads) {
    const body = await api('playlistItems', {
      key: opts.key, part: 'contentDetails', playlistId: playlist, maxResults: 50, pageToken,
    });
    for (const item of body.items ?? []) {
      const published = item.contentDetails?.videoPublishedAt;
      if (opts.since && published && published.slice(0, 10) < opts.since) continue;
      ids.push(item.contentDetails.videoId);
    }
    pageToken = body.nextPageToken;
    if (!pageToken) break;
  }
  return ids.slice(0, opts.uploads);
}

const chunk = (arr, n) => Array.from({ length: Math.ceil(arr.length / n) }, (_, i) => arr.slice(i * n, i * n + n));

async function fetchVideos(ids, opts) {
  const out = [];
  for (const group of chunk([...new Set(ids)], 50)) {
    const body = await api('videos', {
      key: opts.key, part: 'snippet,statistics,contentDetails', id: group.join(','),
    });
    out.push(...(body.items ?? []));
  }
  return out;
}

async function fetchChannels(ids, opts) {
  const byId = new Map();
  for (const group of chunk([...new Set(ids)], 50)) {
    const body = await api('channels', {
      key: opts.key, part: 'snippet,statistics', id: group.join(','),
    });
    for (const item of body.items ?? []) byId.set(item.id, item);
  }
  return byId;
}

/* ----------------------------------------------------------------- filter */

/** PT1H2M3S -> seconds. Live and upcoming items have no duration; treat as 0. */
function durationSeconds(iso) {
  const m = /^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(iso ?? '');
  if (!m) return 0;
  const [, d, h, min, s] = m.map((v) => Number(v ?? 0));
  return ((d * 24 + h) * 60 + min) * 60 + s;
}

function keepers(videos, channels, opts) {
  const rows = [];
  const skipped = { hiddenSubs: 0, belowRatio: 0, floors: 0, shorts: 0, excluded: 0 };
  // Videos in the recent window that missed the bar but are within reach of it:
  // views are still accumulating there, so these are worth a second look.
  const building = [];
  const now = Date.now();
  for (const video of videos) {
    const channel = channels.get(video.snippet?.channelId);
    // A channel that hides its count reports subscriberCount as 0 or omits it;
    // dividing by that would hand back Infinity and pass every filter.
    if (!channel || channel.statistics?.hiddenSubscriberCount) { skipped.hiddenSubs++; continue; }
    const subs = Number(channel.statistics?.subscriberCount ?? 0);
    const views = Number(video.statistics?.viewCount ?? 0);
    if (!subs) { skipped.hiddenSubs++; continue; }
    const subject = `${video.snippet.title} ${channel.snippet.title}`;
    if (opts.excludeRe.some((re) => re.test(subject))) { skipped.excluded++; continue; }
    const seconds = durationSeconds(video.contentDetails?.duration);
    if (opts.noShorts && seconds > 0 && seconds <= 60) { skipped.shorts++; continue; }
    if (subs < opts.minSubs || views < opts.minViews) { skipped.floors++; continue; }
    const ratio = views / subs;
    const publishedAt = video.snippet.publishedAt;
    const recent = Boolean(opts.recentAfter) && publishedAt >= opts.recentAfter;
    const bar = recent ? opts.recentRatio : opts.ratio;
    if (ratio < bar) {
      skipped.belowRatio++;
      if (recent && ratio >= bar / 4) building.push(ratio);
      continue;
    }
    rows.push({
      ratio, views, subs, seconds, recent,
      ageDays: Math.max(0, Math.floor((now - Date.parse(publishedAt)) / 86400000)),
      published: publishedAt.slice(0, 10),
      title: video.snippet.title,
      channelTitle: channel.snippet.title,
      channelId: channel.id,
      videoId: video.id,
    });
  }
  // The recent window sits on top; ratio orders within each block.
  rows.sort((a, b) => Number(b.recent) - Number(a.recent) || b.ratio - a.ratio);
  return { rows, skipped, building };
}

/* ----------------------------------------------------------------- output */

const num = (n) => n.toLocaleString('en-US');
const ratioText = (r) => (r >= 10 ? `${Math.round(r)}x` : `${r.toFixed(1)}x`);
const ratioKo = (r) => ratioText(r).replace('x', '배');

function clock(seconds) {
  if (!seconds) return '';
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`;
}

/** Quote any field that could shift a column - the repo has been bitten by this. */
const cell = (v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));

function writeCsv(file, rows) {
  const header = ['window', 'ratio', 'views', 'subs', 'published', 'age_days',
    'duration', 'channel', 'title', 'url', 'channel_url'];
  const lines = [header.join(',')];
  for (const r of rows) {
    lines.push([
      r.recent ? 'recent' : 'older',
      r.ratio.toFixed(1), r.views, r.subs, r.published, r.ageDays, clock(r.seconds),
      r.channelTitle, r.title,
      `https://www.youtube.com/watch?v=${r.videoId}`,
      `https://www.youtube.com/channel/${r.channelId}`,
    ].map(cell).join(','));
  }
  fs.writeFileSync(file, `${lines.join('\n')}\n`);
}

const TABLE_HEAD = [
  '| 배수 | 조회수 | 구독자 | 공개일 | 지난날 | 길이 | 채널 | 제목 |',
  '|---:|---:|---:|---|---:|---:|---|---|',
];

const tableRows = (rows) => rows.map((r) => `| ${[
  ratioText(r.ratio), num(r.views), num(r.subs), r.published, `${r.ageDays}일`, clock(r.seconds),
  r.channelTitle.replace(/\|/g, '\\|'),
  `[${r.title.replace(/\|/g, '\\|')}](https://www.youtube.com/watch?v=${r.videoId})`,
].join(' | ')} |`);

function writeMarkdown(file, rows, opts) {
  const recent = rows.filter((r) => r.recent);
  const older = rows.filter((r) => !r.recent);
  const out = [
    `# 레퍼런스 — 구독자 대비 조회수 ${opts.ratio}배 이상`,
    '',
    `수집일 ${new Date().toISOString().slice(0, 10)} · ${rows.length}편` +
      (opts.since ? ` · ${opts.since} 이후 공개분` : ''),
    '',
    '> 구독자 수는 API가 유효숫자 3자리로 반올림해 주고, 배수는 **오늘의 구독자 수**',
    '> 기준입니다. 채널이 그 뒤로 컸다면 실제 터진 배수는 이보다 큽니다.',
    '',
  ];
  if (opts.recent) {
    out.push(
      `## 최근 ${opts.recent}일 — ${recent.length}편`,
      '',
      '> 조회수는 시간이 지나야 쌓입니다. **여기 배수는 아직 덜 자란 값이고**,',
      `> 사흘 된 영상의 ${ratioKo(opts.recentRatio)}는 2년 된 영상의 같은 숫자보다 훨씬 셉니다.` +
        (opts.recentRatio === opts.ratio ? ' 얇게 나오면 `--recent-ratio`로 문턱을 낮추세요.' : ''),
      '',
      ...(recent.length ? [...TABLE_HEAD, ...tableRows(recent)]
        : [`_${opts.recent}일 안에 ${ratioKo(opts.recentRatio)}를 넘긴 영상이 없습니다._`]),
      '',
      `## 그 이전 — ${older.length}편`,
      '',
    );
  }
  out.push(...(older.length || !opts.recent ? [...TABLE_HEAD, ...tableRows(older)] : ['_없음._']));
  fs.writeFileSync(file, `${out.join('\n')}\n`);
}

/* ------------------------------------------------------------------- main */

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) { console.log(HELP); return; }

  if (!opts.queries.length && !opts.channels.length) {
    console.log(HELP);
    throw new Error('nothing to collect - pass --seeds, --query, or --channel');
  }

  // When --since already starts inside the window, both passes would ask the
  // same question - skip the second one rather than spend 100 units on it.
  const needRecentPass = Boolean(opts.recent) &&
    opts.recentAfter > (opts.since ? `${opts.since}T00:00:00Z` : '');

  if (opts.dryRun) {
    // Quote the same skip the sweep will make, or the estimate reads 100 units
    // per query too high and a budget check made against it is meaningless.
    const pagesPerQuery = opts.pages + (needRecentPass ? opts.recentPages : 0);
    const searchCost = opts.queries.length * pagesPerQuery * 100;
    const sweepCost = opts.channels.length * (2 + Math.ceil(opts.uploads / 50));
    console.log(`  queries     ${opts.queries.length ? opts.queries.join(' · ') : 'none'}`);
    console.log(`  channels    ${opts.channels.length ? opts.channels.join(' · ') : 'none'}`);
    console.log(`  keep        views >= subs x ${opts.ratio}` +
      (opts.minSubs ? `, subs >= ${num(opts.minSubs)}` : '') +
      (opts.minViews ? `, views >= ${num(opts.minViews)}` : '') +
      (opts.noShorts ? ', no shorts' : '') +
      (opts.exclude.length ? `, excluding ${opts.exclude.length} pattern(s)` : ''));
    console.log(`  priority    ${opts.recent
      ? `last ${opts.recent} days, kept at ${opts.recentRatio}x` +
        (needRecentPass ? ', own search pass'
          : ', no second pass - --since already starts inside the window')
      : 'off - one flat list by ratio'}`);
    console.log(`  quota       ~${num(searchCost + sweepCost)} units of the 10,000 daily`);
    return;
  }

  opts.key ||= process.env.YOUTUBE_API_KEY;
  if (!opts.key) throw new Error('no API key - set YOUTUBE_API_KEY or pass --key');

  const ids = [];
  // A seed list carries handles nobody has verified, and a renamed channel
  // would otherwise throw away every result collected before it. One dead seed
  // costs a warning, not the run. A spent quota does end the sweep - but the
  // rows already in hand still get written.
  let spent = false;
  const sweep = async (label, collect) => {
    if (spent) return;
    try {
      const found = await collect();
      console.log(`  ${label} - ${found.length} videos`);
      ids.push(...found);
    } catch (err) {
      if (err.reason === 'quotaExceeded') {
        spent = true;
        console.warn(`  ! quota spent - stopping the sweep, keeping what came back`);
      } else {
        console.warn(`  ! ${label} - ${err.message}`);
      }
    }
  };

  for (const query of opts.queries) {
    if (needRecentPass) {
      await sweep(`last ${String(opts.recent).padEnd(3)}d   ${query}`,
        () => searchVideos(query, opts, { pages: opts.recentPages, after: opts.recentAfter }));
    }
    await sweep(`search      ${query}`, () => searchVideos(query, opts));
  }
  for (const ref of opts.channels) {
    await sweep(`channel     ${ref}`, async () => channelUploads(await resolveChannel(ref, opts), opts));
  }
  if (!ids.length) throw new Error('nothing came back - every search and channel failed');

  const videos = await fetchVideos(ids, opts);
  const channels = await fetchChannels(videos.map((v) => v.snippet.channelId), opts);
  const { rows, skipped, building } = keepers(videos, channels, opts);

  fs.mkdirSync(opts.out, { recursive: true });
  const csvPath = path.join(opts.out, `${opts.name}.csv`);
  const mdPath = path.join(opts.out, `${opts.name}.md`);
  writeCsv(csvPath, rows);
  writeMarkdown(mdPath, rows, opts);

  const recentKept = rows.filter((r) => r.recent).length;
  console.log(`  looked at   ${num(videos.length)} videos on ${num(channels.size)} channels`);
  console.log(`  kept        ${num(rows.length)} at ${opts.ratio}x or better` +
    (opts.recent ? `, ${num(recentKept)} of them from the last ${opts.recent} days` : ''));
  if (building.length) {
    console.log(`  building    ${num(building.length)} more inside the window sit between ` +
      `${ratioText(opts.recentRatio / 4)} and ${ratioText(opts.recentRatio)} - views are still ` +
      `piling up there (best ${ratioText(Math.max(...building))}); --recent-ratio lowers the bar`);
  }
  console.log(`  dropped     ${num(skipped.belowRatio)} under the bar` +
    (skipped.floors ? `, ${num(skipped.floors)} under the floors` : '') +
    (skipped.shorts ? `, ${num(skipped.shorts)} shorts` : '') +
    (skipped.excluded ? `, ${num(skipped.excluded)} excluded by pattern` : '') +
    (skipped.hiddenSubs ? `, ${num(skipped.hiddenSubs)} with hidden subscriber counts` : ''));
  console.log(`  wrote       ${csvPath}`);
  console.log(`              ${mdPath}`);
  console.log(`  quota used  ${num(quota)} units`);
}

main().then(
  () => process.exit(0),
  (err) => { console.error(`error: ${err.message}`); process.exit(1); }
);
