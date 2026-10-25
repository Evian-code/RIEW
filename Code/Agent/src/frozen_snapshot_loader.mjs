import { access, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { configureNormalization, normalizeUrl } from './normalize.mjs';
import { buildPrimaryAliasMap } from './node_identity.mjs';
import { readSimpleYaml } from './config.mjs';

const REPRESENTATIONS = new Set(['rendered_all_anchors', 'rendered_all', 'rendered_visible_followable', 'rendered_aria_followable']);

const readJsonl = async path => (await readFile(path, 'utf8')).trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
const exists = async path => { try { await access(path); return true; } catch { return false; } };

function decodeHtml(text) {
  return text.replace(/&nbsp;|&#160;/gi, ' ').replace(/&amp;/gi, '&').replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'").replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
    .replace(/&#(\d+);/g, (_, value) => String.fromCodePoint(Number(value)))
    .replace(/&#x([0-9a-f]+);/gi, (_, value) => String.fromCodePoint(Number.parseInt(value, 16)));
}

function plainText(html) {
  return decodeHtml(html.replace(/<script\b[\s\S]*?<\/script>/gi, ' ').replace(/<style\b[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();
}

function headings(html) {
  return Object.freeze([...html.matchAll(/<h([1-6])\b[^>]*>([\s\S]*?)<\/h\1>/gi)]
    .map(match => Object.freeze({ level: Number(match[1]), text: plainText(match[2]) })).filter(item => item.text));
}

function nonLinkText(html) {
  return plainText(html.replace(/<a\b[^>]*>[\s\S]*?<\/a>/gi, ' '));
}

function resolvePrimary(value, aliases, nodes) {
  if (!value) return null;
  const normalized = normalizeUrl(value);
  return aliases.get(value) || (normalized && aliases.get(normalized)) || (normalized && nodes.has(normalized) ? normalized : null);
}

function linkKey(record, destinationUrl) {
  return [record.document_position ?? '', destinationUrl, record.anchor_text || '', record.source_href || ''].join('\u0000');
}

function stableLinks(records, aliases, nodes) {
  const unique = new Map();
  for (const record of records) {
    if (record.boundary || !record.target_normalized_url) continue;
    const destinationUrl = resolvePrimary(record.target_requested_url || record.target_normalized_url, aliases, nodes)
      || resolvePrimary(record.target_normalized_url, aliases, nodes);
    if (!destinationUrl || !nodes.has(destinationUrl)) continue;
    const key = linkKey(record, destinationUrl);
    if (!unique.has(key)) unique.set(key, Object.freeze({
      stable_key: key,
      source_url: resolvePrimary(record.source_normalized_final_url || record.source_url, aliases, nodes),
      destination_url: destinationUrl,
      label: (record.anchor_text || record.title || '').replace(/\s+/g, ' ').trim(),
      document_position: record.document_position,
      target_normalized_url: destinationUrl,
      boundary: false
    }));
  }
  return [...unique.values()].sort((left, right) => (left.document_position ?? Number.MAX_SAFE_INTEGER) - (right.document_position ?? Number.MAX_SAFE_INTEGER)
    || left.destination_url.localeCompare(right.destination_url) || left.label.localeCompare(right.label));
}

export async function loadFrozenSiteSnapshot({ root = process.cwd(), site, runDir }) {
  const siteDir = resolve(root, runDir, site);
  const [pageRecords, linkRecords] = await Promise.all([readJsonl(join(siteDir, 'pages.jsonl')), readJsonl(join(siteDir, 'links.jsonl'))]);
  const pageByNode = new Map();
  for (const page of pageRecords) if (page.normalized_final_url && !pageByNode.has(page.normalized_final_url)) pageByNode.set(page.normalized_final_url, page);
  const nodes = new Set(pageByNode.keys());
  const aliases = buildPrimaryAliasMap(pageRecords, normalizeUrl);
  const aliasPath = join(siteDir, 'url_aliases.jsonl');
  if (await exists(aliasPath)) for (const alias of await readJsonl(aliasPath)) if (alias.requested_url && alias.normalized_final_url) aliases.set(alias.requested_url, alias.normalized_final_url);

  const grouped = new Map([...nodes].map(node => [node, { rendered_all_anchors: [], rendered_visible_followable: [], rendered_aria_followable: [] }]));
  for (const record of linkRecords) {
    if (!REPRESENTATIONS.has(record.representation)) continue;
    const canonicalRepresentation = record.representation === 'rendered_all' ? 'rendered_all_anchors' : record.representation;
    const source = resolvePrimary(record.source_normalized_final_url || record.source_url, aliases, nodes);
    if (source && grouped.has(source)) grouped.get(source)[canonicalRepresentation].push(record);
  }

  const pages = new Map();
  for (const [url, page] of pageByNode) {
    const pageDir = join(siteDir, 'pages', page.page_id);
    const [html, metadata] = await Promise.all([readFile(join(pageDir, 'rendered.html'), 'utf8'), readFile(join(pageDir, 'metadata.json'), 'utf8').then(JSON.parse)]);
    const allLinks = stableLinks(grouped.get(url).rendered_all_anchors, aliases, nodes);
    const allByKey = new Map(allLinks.map(link => [link.stable_key, link]));
    const visibleRaw = stableLinks(grouped.get(url).rendered_visible_followable, aliases, nodes);
    const visibleLinks = visibleRaw.map(link => allByKey.get(link.stable_key)).filter(Boolean);
    const ariaRaw = stableLinks(grouped.get(url).rendered_aria_followable, aliases, nodes);
    const ariaLinks = ariaRaw.map(link => allByKey.get(link.stable_key) || link).filter(Boolean);
    pages.set(url, Object.freeze({
      url,
      site,
      page_id: page.page_id,
      title: metadata?.rendered?.title || '',
      text: plainText(html),
      headings: headings(html),
      non_link_text: nonLinkText(html),
      summary: null,
      rendered_all_links: Object.freeze(allLinks),
      rendered_visible_followable_links: Object.freeze(visibleLinks),
      rendered_aria_followable_links: Object.freeze(ariaLinks),
      link_integrity: Object.freeze({ visible_record_count: visibleRaw.length, visible_matched_to_all_count: visibleLinks.length, aria_record_count: ariaRaw.length, aria_matched_to_all_count: ariaLinks.filter(link => allByKey.has(link.stable_key)).length })
    }));
  }
  return Object.freeze({ site, run_dir: resolve(root, runDir), pages, aliases, node_count: pages.size, source: 'frozen_filesystem_artifacts' });
}

export async function loadConfiguredFrozenSnapshots({ root = process.cwd(), configPath = 'config/agent_validation.yaml' } = {}) {
  const config = await readSimpleYaml(resolve(root, configPath));
  configureNormalization(await readSimpleYaml(resolve(root, 'config/url_normalization.yaml')));
  const snapshots = new Map();
  for (const site of config.sites) {
    const runDir = config.frozen_snapshots?.[site];
    if (!runDir) throw new Error(`No frozen snapshot configured for ${site}`);
    snapshots.set(site, await loadFrozenSiteSnapshot({ root, site, runDir }));
  }
  return { config, snapshots };
}
