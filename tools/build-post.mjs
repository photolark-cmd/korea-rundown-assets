#!/usr/bin/env node
/**
 * Convert a draft in drafts/ into the shape autoworker-script publishes:
 * blog/posts/<slug>/post.html + meta.json.
 *
 *   node tools/build-post.mjs drafts/<file>.md [options]
 *
 * post.html is an HTML fragment with inline styles (Blogger strips <style>),
 * matching the existing channel posts. See tools/README.md.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const RAW_BASE = 'https://raw.githubusercontent.com/photolark-cmd/korea-rundown-assets/main';

const HELP = `Convert a draft into post.html + meta.json.

  node tools/build-post.mjs drafts/<file>.md [options]

  --out <dir>            output root            (default: blog/posts)
  --slug <name>          folder name            (default: from the filename)
  --labels <a,b>         Blogger labels
  --description <text>   search description     (default: first paragraph, trimmed)
  --raw-base <url>       image host base        (default: this repo's raw URL)
  --youtube <url|id>     append the video embed and record it in meta.json
  -h, --help             this message

The draft's Korean pre-publish warning block and trailing checklist are
dropped: they are notes to the author, not part of the post.
`;

/* ---------------------------------------------------------------- options */

function parseArgs(argv) {
  const opts = { out: path.join(REPO_ROOT, 'blog', 'posts'), rawBase: RAW_BASE };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const value = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${arg} needs a value`);
      return v;
    };
    switch (arg) {
      case '--out': opts.out = path.resolve(value()); break;
      case '--slug': opts.slug = value(); break;
      case '--labels': opts.labels = value().split(',').map((s) => s.trim()).filter(Boolean); break;
      case '--description': opts.description = value(); break;
      case '--raw-base': opts.rawBase = value().replace(/\/$/, ''); break;
      case '--youtube': opts.youtube = value(); break;
      case '-h': case '--help': opts.help = true; break;
      default:
        if (arg.startsWith('--')) throw new Error(`unknown option: ${arg}`);
        rest.push(arg);
    }
  }
  opts.draft = rest[0];
  return opts;
}

/* ------------------------------------------------------------------ style */

const S = {
  note: 'background:#eef4fb;border-radius:8px;padding:12px 18px;margin:0 0 20px 0;font-size:0.95em;',
  fig: 'text-align:center;margin:22px 0;',
  img: 'max-width:100%;height:auto;border:0;border-radius:8px;',
  table: 'border-collapse:collapse;width:100%;margin:12px 0;',
  th: 'border:1px solid #ccc;padding:8px;',
  td: 'border:1px solid #ccc;padding:8px;',
  thead: 'background:#f0f0f0;',
  source: 'font-size:0.88em;color:#666;margin:6px 0 18px 0;',
  ul: 'margin:10px 0 18px 0;padding-left:20px;',
  embed: 'text-align:center;margin:24px 0 8px 0;',
  embedFrame: 'max-width:100%;border:0;',
  embedNote: 'font-size:0.9em;color:#666;margin-top:8px;',
};

/** youtu.be/ID · watch?v=ID · embed/ID · a bare ID */
function youtubeId(input) {
  const m = String(input).match(/(?:v=|youtu\.be\/|embed\/)([A-Za-z0-9_-]{11})/)
    || String(input).match(/^([A-Za-z0-9_-]{11})$/);
  if (!m) throw new Error(`could not read a YouTube id from: ${input}`);
  return m[1];
}

/**
 * Embed block per the channel convention: no responsive wrapper (Blogger
 * strips that CSS and the video disappears) and always a fallback link, since
 * ad blockers hide the iframe and leave a blank gap.
 */
function embedBlock(id) {
  const watch = `https://www.youtube.com/watch?v=${id}`;
  return `<div style="${S.embed}">
<iframe width="560" height="315" src="https://www.youtube.com/embed/${id}" style="${S.embedFrame}" allowfullscreen></iframe>
<p style="${S.embedNote}">영상이 보이지 않으면 <a href="${watch}" target="_blank" rel="noopener">유튜브에서 바로 보기</a></p>
</div>`;
}

/* ---------------------------------------------------------------- inline */

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Inline markdown -> HTML. Internal .md links lose their href (no URL yet). */
function inline(text, ctx) {
  let out = esc(text);
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, label, href) => {
    if (/^https?:/.test(href)) return `<a href="${href}" target="_blank" rel="noopener">${label}</a>`;
    ctx.pendingLinks.push(label.replace(/\s+/g, ' ').trim());
    return `<strong>${label}</strong>`;
  });
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  return out;
}

/* ------------------------------------------------------------------ block */

function stripAuthorNotes(lines) {
  // Drop the leading "> ## ⚠️ ..." blockquote and everything to its rule.
  let start = 0;
  if (lines[0]?.startsWith('>')) {
    const rule = lines.findIndex((l) => l.trim() === '---');
    if (rule > 0) start = rule + 1;
  }
  // Drop the trailing checklist section and the rule above it.
  let end = lines.length;
  const check = lines.findIndex((l, i) => i > start && /^##\s+발행 체크리스트/.test(l));
  if (check > 0) {
    end = check;
    while (end > start && (lines[end - 1].trim() === '' || lines[end - 1].trim() === '---')) end--;
  }
  return lines.slice(start, end);
}

function renderTable(rows, ctx) {
  const cells = (line) => line.replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const head = cells(rows[0]);
  const body = rows.slice(2).map(cells);
  const th = head.map((c) => `<th style="${S.th}">${inline(c, ctx)}</th>`).join('');
  const trs = body.map((r) =>
    `<tr>${r.map((c) => `<td style="${S.td}">${inline(c, ctx)}</td>`).join('')}</tr>`).join('\n');
  return `<table style="${S.table}">\n<tr style="${S.thead}">${th}</tr>\n${trs}\n</table>`;
}

function convert(md, opts) {
  const ctx = { pendingLinks: [], images: [] };
  const lines = stripAuthorNotes(md.replace(/\r\n/g, '\n').split('\n'));
  const out = [];
  let title = null;
  let firstPara = null;
  let i = 0;

  const flushParagraph = (buf) => {
    if (!buf.length) return;
    const text = buf.join(' ').trim();
    if (!text) return;
    if (!firstPara) firstPara = text.replace(/[*`\[\]]/g, '');
    out.push(`<p>${inline(text, ctx)}</p>`);
  };

  while (i < lines.length) {
    const line = lines[i];
    const t = line.trim();

    if (t === '' || t === '---') { i++; continue; }

    if (/^#\s+/.test(t)) { title = t.replace(/^#\s+/, '').trim(); i++; continue; }

    if (/^###\s+/.test(t)) { out.push(`<h3>${inline(t.replace(/^###\s+/, ''), ctx)}</h3>`); i++; continue; }
    if (/^##\s+/.test(t)) { out.push(`<h2>${inline(t.replace(/^##\s+/, ''), ctx)}</h2>`); i++; continue; }

    // The dateline: *Last updated: ... · Exchange rate ...*
    if (/^\*[^*].*\*$/.test(t) && /updated/i.test(t)) {
      out.push(`<p style="${S.note}">${inline(t.replace(/^\*|\*$/g, ''), ctx)}</p>`);
      i++; continue;
    }

    // Image on its own line
    const img = t.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
    if (img) {
      const rel = img[2].replace(/^\.\.\//, '').replace(/^\.\//, '');
      const src = /^https?:/.test(img[2]) ? img[2] : `${opts.rawBase}/${rel}`;
      ctx.images.push(rel);
      out.push(`<div style="${S.fig}">\n<img src="${src}" alt="${esc(img[1])}" style="${S.img}" />\n</div>`);
      i++; continue;
    }

    // Table
    if (t.startsWith('|') && lines[i + 1] && /^\|[\s:|-]+\|$/.test(lines[i + 1].trim())) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(lines[i].trim()); i++; }
      out.push(renderTable(rows, ctx));
      continue;
    }

    // Blockquote -> source note
    if (t.startsWith('>')) {
      const buf = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>\s?/, ''));
        i++;
      }
      out.push(`<p style="${S.source}">${inline(buf.join(' ').trim(), ctx)}</p>`);
      continue;
    }

    // List
    if (/^[-*]\s+/.test(t)) {
      const items = [];
      while (i < lines.length && (/^[-*]\s+/.test(lines[i].trim()) || /^\s{2,}\S/.test(lines[i]))) {
        if (/^[-*]\s+/.test(lines[i].trim())) items.push(lines[i].trim().replace(/^[-*]\s+/, ''));
        else items[items.length - 1] += ' ' + lines[i].trim();
        i++;
      }
      out.push(`<ul style="${S.ul}">\n` +
        items.map((it) => `<li>${inline(it, ctx)}</li>`).join('\n') + `\n</ul>`);
      continue;
    }

    // Paragraph
    const buf = [];
    while (i < lines.length) {
      const cur = lines[i].trim();
      if (cur === '' || cur === '---' || /^[#>|-]/.test(cur) || /^!\[/.test(cur)) break;
      buf.push(cur);
      i++;
    }
    flushParagraph(buf);
  }

  return { html: out.join('\n\n') + '\n', title, firstPara, ctx };
}

/* ------------------------------------------------------------------- main */

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help || !opts.draft) { process.stdout.write(HELP); return opts.draft ? 0 : 1; }

  const draftPath = path.resolve(opts.draft);
  if (!fs.existsSync(draftPath)) throw new Error(`draft not found: ${draftPath}`);

  const md = fs.readFileSync(draftPath, 'utf8');
  const { html: body, title, firstPara, ctx } = convert(md, opts);
  const videoId = opts.youtube ? youtubeId(opts.youtube) : null;
  const html = videoId ? `${body}\n${embedBlock(videoId)}\n` : body;
  if (!title) throw new Error(`${draftPath}: no "# Title" heading found`);

  const slug = opts.slug || path.basename(draftPath, '.md').replace(/^\d{4}-\d{2}-\d{2}-/, '');
  const dir = path.join(opts.out, slug);
  fs.mkdirSync(dir, { recursive: true });

  const description = opts.description
    || (firstPara || '').replace(/\s+/g, ' ').slice(0, 150).trim();

  const metaPath = path.join(dir, 'meta.json');
  const existing = fs.existsSync(metaPath) ? JSON.parse(fs.readFileSync(metaPath, 'utf8')) : {};
  const meta = {
    title,
    labels: opts.labels || existing.labels || [],
    search_description: opts.description || existing.search_description || description,
    ...(videoId
      ? { youtube_url: `https://www.youtube.com/watch?v=${videoId}` }
      : existing.youtube_url ? { youtube_url: existing.youtube_url } : {}),
    ...(existing.uploaded ? { uploaded: existing.uploaded } : {}),
  };

  fs.writeFileSync(path.join(dir, 'post.html'), html, 'utf8');
  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2) + '\n', 'utf8');

  console.log(`+ ${path.relative(process.cwd(), dir)}/`);
  console.log(`    title       ${title}`);
  console.log(`    images      ${ctx.images.length} (served from ${opts.rawBase.replace(/^https:\/\//, '')})`);
  console.log(`    video       ${videoId ? `embedded (${videoId})` : 'none - pass --youtube once the video is up'}`);
  if (!meta.labels.length) console.log(`    ! labels empty - set them in meta.json or pass --labels`);
  if (ctx.pendingLinks.length) {
    console.log(`    ! ${ctx.pendingLinks.length} internal link(s) rendered as plain text (no published URL yet):`);
    for (const l of ctx.pendingLinks) console.log(`        - ${l}`);
  }
  return 0;
}

try {
  process.exit(main());
} catch (err) {
  console.error(`error: ${err.message}`);
  process.exit(1);
}
