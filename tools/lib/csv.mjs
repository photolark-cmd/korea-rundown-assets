import fs from 'node:fs';

/* CSV reading shared by the thumbnail and figure renderers. */

/** Minimal RFC4180 parser: quoted fields, "" escapes, newlines inside quotes. */
export function parseCsv(text) {
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

/**
 * Read a CSV into row objects keyed by lower-cased header.
 * `required` names the columns the caller cannot work without.
 */
export function readRows(csvPath, required = []) {
  const rows = parseCsv(fs.readFileSync(csvPath, 'utf8'));
  if (!rows.length) throw new Error(`${csvPath} is empty`);
  const header = rows[0].map((h) => h.trim().toLowerCase());
  for (const key of required) {
    if (!header.includes(key)) throw new Error(`${csvPath} is missing a "${key}" column`);
  }
  return rows.slice(1).map((cells, idx) => {
    const line = idx + 2;
    // More cells than headers almost always means an unquoted comma inside a
    // field, which silently shifts every value after it into the wrong column.
    if (cells.length > header.length) {
      console.warn(`! ${csvPath}:${line}: ${cells.length} values for ${header.length} columns ` +
        `- a field containing a comma probably needs wrapping in "quotes"`);
    }
    const record = { __line: line };
    header.forEach((key, col) => { record[key] = (cells[col] ?? '').trim(); });
    return record;
  });
}
