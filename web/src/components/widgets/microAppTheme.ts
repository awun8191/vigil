/**
 * MCP Apps-style host CSS token bridge for sandboxed micro-app and HTML preview iframes.
 *
 * Strips external Google Font stylesheets, replaces LLM-default slate-navy (#0f172a / #1e293b)
 * backgrounds with Vigil's monochrome carbon/alabaster tokens, and injects Vigil's active
 * light/dark CSS custom properties from src/styles/tokens.css.
 */

const GOOGLE_FONTS_LINK_RE = /<link\b[^>]*href=["'][^"']*fonts\.googleapis\.com[^"']*["'][^>]*>/gi;

const SLATE_NAVY_COLOR_RE =
  /#(?:0f172a|1e293b|111827|0b0f19|0d1117|161b22|1e2433|0b0d12|141821)\b/gi;

const HOST_TOKEN_NAMES = [
  '--bg',
  '--surface',
  '--surface-solid',
  '--panel-bg',
  '--panel-border',
  '--panel-inset',
  '--panel-inset-strong',
  '--card',
  '--text',
  '--text-secondary',
  '--text-muted',
  '--border',
  '--border-strong',
  '--accent',
  '--accent-ink',
  '--accent-soft',
  '--btn-primary-bg',
  '--btn-primary-ink',
  '--font-sans',
  '--r-inner',
  '--r-control',
  '--r-card',
] as const;

function readActiveHostVariables(): { theme: string; cssVars: string } {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return {
      theme: 'dark',
      cssVars: [
        '--surface-solid: #101010;',
        '--surface: #101010;',
        '--bg: #101010;',
        '--panel-bg: rgba(20, 20, 20, 0.72);',
        '--panel-border: rgba(255, 255, 255, 0.09);',
        '--panel-inset: rgba(255, 255, 255, 0.055);',
        '--panel-inset-strong: rgba(255, 255, 255, 0.09);',
        '--card: rgba(26, 26, 26, 0.70);',
        '--text: #f5f5f5;',
        '--text-secondary: #c7c7c7;',
        '--text-muted: #8f8f8f;',
        '--border: rgba(255, 255, 255, 0.09);',
        '--border-strong: rgba(255, 255, 255, 0.20);',
        '--accent: #f5f5f5;',
        '--accent-ink: #0a0a0a;',
        '--accent-soft: rgba(255, 255, 255, 0.10);',
        '--btn-primary-bg: #000000;',
        '--btn-primary-ink: #ffffff;',
        "--font-sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
        '--r-inner: 12px;',
      ].join('\n    '),
    };
  }

  const root = document.documentElement;
  const theme = root.getAttribute('data-theme') || 'dark';
  const computed = window.getComputedStyle(root);
  const declarations: string[] = [];

  for (const name of HOST_TOKEN_NAMES) {
    const val = computed.getPropertyValue(name).trim();
    if (val) {
      declarations.push(`${name}: ${val};`);
    }
  }

  return {
    theme,
    cssVars: declarations.join('\n    '),
  };
}

export function buildThemedMicroAppSrcDoc(rawHtml: string): string {
  const { theme, cssVars } = readActiveHostVariables();
  const replacementSurface = theme === 'light' ? '#f7f7f5' : '#141414';
  const sanitized = (rawHtml || '')
    .replace(GOOGLE_FONTS_LINK_RE, '')
    .replace(SLATE_NAVY_COLOR_RE, replacementSurface);

  const styleBlock = `<style id="vigil-host-tokens">
  :root {
    color-scheme: ${theme === 'light' ? 'light' : 'dark'};
    ${cssVars}
    /* Standard MCP Apps / Generative UI semantic aliases */
    --background: var(--surface-solid);
    --card-border: var(--border);
    --foreground: var(--text);
    --muted-foreground: var(--text-muted);
    --primary: var(--accent);
  }
  *, *::before, *::after {
    box-sizing: border-box;
  }
  html, body {
    margin: 0;
    padding: 0;
    background: var(--surface-solid) !important;
    color: var(--text) !important;
    font-family: var(--font-sans, ui-sans-serif, -apple-system, BlinkMacSystemFont, sans-serif) !important;
    font-size: 13px;
    line-height: 1.45;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
  }
</style>`;

  if (/<head\b[^>]*>/i.test(sanitized)) {
    return sanitized.replace(/<head\b[^>]*>/i, (match) => `${match}\n${styleBlock}`);
  }
  if (/<html\b[^>]*>/i.test(sanitized)) {
    return sanitized.replace(/<html\b[^>]*>/i, (match) => `${match}\n<head>${styleBlock}</head>`);
  }
  return `<!DOCTYPE html><html data-theme="${theme}"><head>${styleBlock}</head><body>${sanitized}</body></html>`;
}
