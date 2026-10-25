import { createHash } from 'node:crypto';

let normalizationConfig = null;
export function configureNormalization(config) { normalizationConfig = structuredClone(config); }
function config() { if (!normalizationConfig) throw new Error('Normalization config not loaded. Call configureNormalization() first.'); return normalizationConfig; }

export function resolveUrl(href, baseUrl) {
  if (!href || /^\s*(javascript:|mailto:|tel:|data:|#)/i.test(href)) return null;
  try { return new URL(href, baseUrl).href; } catch { return null; }
}

export function normalizeUrl(input, baseUrl) {
  const resolved = resolveUrl(input, baseUrl);
  if (!resolved) return null;
  const url = new URL(resolved);
  const rules = config();
  if (!(rules.allowed_schemes || []).map(x => `${x}:`).includes(url.protocol)) return null;
  url.protocol = url.protocol.toLowerCase();
  url.hostname = url.hostname.toLowerCase();
  if (rules.remove_default_ports && ((url.protocol === 'http:' && url.port === '80') || (url.protocol === 'https:' && url.port === '443'))) url.port = '';
  if (rules.drop_fragments) url.hash = '';
  if (rules.strip_tracking_parameters) for (const [key] of [...url.searchParams]) {
    const lower = key.toLowerCase(); const names = (rules.tracking_parameter_names || []).map(x => x.toLowerCase()); const prefixes = (rules.tracking_parameter_prefixes || []).map(x => x.toLowerCase());
    if (names.includes(lower) || prefixes.some(prefix => lower.startsWith(prefix))) url.searchParams.delete(key);
  }
  if (rules.sort_query_parameters) { const sorted = [...url.searchParams.entries()].sort(([a, av], [b, bv]) => a.localeCompare(b) || av.localeCompare(bv)); url.search = ''; for (const [key, value] of sorted) url.searchParams.append(key, value); }
  if (rules.trailing_slash === 'preserve_root_only' && url.pathname.length > 1 && url.pathname.endsWith('/')) url.pathname = url.pathname.slice(0, -1);
  return url.href;
}

export function isSameSite(candidate, seedUrl) {
  try { return new URL(candidate).origin === new URL(seedUrl).origin; } catch { return false; }
}

export function nodeKey(normalizedUrl) {
  return normalizedUrl ? createHash('sha256').update(normalizedUrl).digest('hex').slice(0, 20) : null;
}

export function contentHash(content) {
  return createHash('sha256').update(content ?? '').digest('hex');
}
