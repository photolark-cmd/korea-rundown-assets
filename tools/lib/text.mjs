/* Copy formatting shared by the renderers. */

export const escapeHtml = (s) =>
  s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

/** Plain text -> template HTML: **bold** becomes <b>, | and \n become line breaks. */
export const markup = (s) =>
  escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/\s*(\||\\n)\s*/g, '<br>');

export const hasHangul = (s) => /[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-퟿]/.test(s);
