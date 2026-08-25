import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';

const require = createRequire(import.meta.url);

/* Chromium plumbing shared by the renderers. */

export function loadPlaywright() {
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
export function readFontCss(cssPath) {
  const css = fs.readFileSync(cssPath, 'utf8');
  const dir = path.dirname(cssPath);
  return css.replace(/url\((['"]?)([^'")]+)\1\)/g, (match, quote, url) => {
    if (/^(https?:|data:|file:)/.test(url)) return match;
    return `url(${pathToFileURL(path.resolve(dir, url)).href})`;
  });
}

/** Launch Chromium, honouring the standard proxy variables (it ignores them itself). */
export async function launchBrowser() {
  const { chromium } = loadPlaywright();
  const proxyServer = process.env.HTTPS_PROXY || process.env.https_proxy || null;
  return chromium.launch(proxyServer ? { proxy: { server: proxyServer } } : {});
}

/** Wait for the webfont stylesheet to arrive and its faces to finish loading. */
export async function waitForFonts(page, timeoutMs = 15000) {
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
