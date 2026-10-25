import { mkdir, readFile, writeFile, access, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';
import { readSimpleYaml } from './config.mjs';
import { configureNormalization, isSameSite, normalizeUrl } from './normalize.mjs';
import { buildPrimaryAliasMap } from './node_identity.mjs';
import { EXTRACT_PAGE_SCRIPT } from './extract.mjs';
import { extractAccessibilityFromPage } from './accessibility_extract.mjs';

const sleep = ms => new Promise(r => setTimeout(r, ms));
const exists = async path => { try { await access(path); return true; } catch { return false; } };
const readJsonl = async path => (await readFile(path, 'utf8')).trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
const csv = rows => {
  const keys = [...new Set(rows.flatMap(Object.keys))];
  const quote = value => {
    const text = value == null ? '' : String(value);
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  return [keys.join(','), ...rows.map(row => keys.map(key => quote(row[key])).join(','))].join('\n') + '\n';
};
const edgeKey = (source, target) => `${source}\u0000${target}`;
const jaccard = (a, b) => {
  const union = new Set([...a, ...b]);
  return union.size ? [...a].filter(value => b.has(value)).length / union.size : 1;
};

function shortestDistances(nodes, edges, start) {
  if (!start || !nodes.has(start)) return new Map();
  const next = new Map([...nodes].map(node => [node, []]));
  for (const edge of edges.values()) if (nodes.has(edge.source) && nodes.has(edge.target)) next.get(edge.source).push(edge.target);
  const distance = new Map([[start, 0]]);
  const queue = [start];
  for (let i = 0; i < queue.length; i += 1) {
    for (const target of next.get(queue[i]) || []) if (!distance.has(target)) {
      distance.set(target, distance.get(queue[i]) + 1);
      queue.push(target);
    }
  }
  return distance;
}

function graphSummary(label, nodes, edges, seed) {
  const distance = shortestDistances(nodes, edges, seed);
  const values = [...distance.values()].sort((a, b) => a - b);
  return {
    representation: label,
    edge_count: edges.size,
    reachable_fraction: nodes.size ? distance.size / nodes.size : null,
    mean_shortest_path: values.length ? values.reduce((a, b) => a + b, 0) / values.length : null,
    median_shortest_path: values.length ? values[Math.floor(values.length / 2)] : null,
    unreachable_node_count: nodes.size - distance.size
  };
}

function resolvePrimary(value, aliases, nodes) {
  if (!value) return null;
  const normalized = normalizeUrl(value);
  return aliases.get(value) || (normalized && aliases.get(normalized)) || (normalized && nodes.has(normalized) ? normalized : null);
}

async function aliasesForSite(siteDir, pages) {
  const aliases = buildPrimaryAliasMap(pages, normalizeUrl);
  const aliasPath = join(siteDir, 'url_aliases.jsonl');
  if (await exists(aliasPath)) {
    for (const row of await readJsonl(aliasPath)) {
      if (!row.requested_url || !row.normalized_final_url) continue;
      aliases.set(row.requested_url, row.normalized_final_url);
      const normalized = normalizeUrl(row.requested_url);
      if (normalized) aliases.set(normalized, row.normalized_final_url);
    }
  }
  return aliases;
}

function asGraphEdge({ source, absoluteUrl, seedUrl, aliases, nodes, metadata = {} }) {
  const normalized = normalizeUrl(absoluteUrl, source);
  if (!normalized || !isSameSite(normalized, seedUrl)) return null;
  const target = resolvePrimary(absoluteUrl, aliases, nodes) || resolvePrimary(normalized, aliases, nodes);
  if (!target || !nodes.has(target)) return null;
  return { source, target, ...metadata };
}

async function captureSameStateRepresentations({ page, source, seedUrl, aliases, nodes }) {
  const rendered = await page.evaluate(EXTRACT_PAGE_SCRIPT);
  const aria = await extractAccessibilityFromPage(page);
  const maps = new Map([
    ['rendered_all', new Map()],
    ['rendered_visible_followable', new Map()],
    ['rendered_aria_followable', new Map()]
  ]);
  const records = [];

  for (const anchor of rendered.anchors) {
    if (!anchor.absolute_url) continue;
    const edge = asGraphEdge({ source, absoluteUrl: anchor.absolute_url, seedUrl, aliases, nodes, metadata: { href: anchor.href, anchor_text: anchor.anchor_text, document_position: anchor.document_position } });
    records.push({ representation: 'rendered_all', source, absolute_url: anchor.absolute_url, href: anchor.href, anchor_text: anchor.anchor_text, visible: anchor.visible, followable: anchor.followable, graph_edge: !!edge });
    if (edge) maps.get('rendered_all').set(edgeKey(edge.source, edge.target), edge);
    if (anchor.visible && anchor.followable) {
      records.push({ representation: 'rendered_visible_followable', source, absolute_url: anchor.absolute_url, href: anchor.href, anchor_text: anchor.anchor_text, visible: anchor.visible, followable: anchor.followable, graph_edge: !!edge });
      if (edge) maps.get('rendered_visible_followable').set(edgeKey(edge.source, edge.target), edge);
    }
  }

  for (const link of aria) {
    if (!link.included || !link.absolute_url) continue;
    const edge = asGraphEdge({ source, absoluteUrl: link.absolute_url, seedUrl, aliases, nodes, metadata: { href: link.href, anchor_text: link.accessible_name, document_position: link.document_position } });
    records.push({ representation: 'rendered_aria_followable', source, absolute_url: link.absolute_url, href: link.href, anchor_text: link.accessible_name, aria_snapshot: link.aria_snapshot, graph_edge: !!edge });
    if (edge) maps.get('rendered_aria_followable').set(edgeKey(edge.source, edge.target), edge);
  }
  return { maps, records };
}

function mergeMaps(target, source) {
  for (const [key, value] of source) target.set(key, value);
}

export async function runStep05({
  root = process.cwd(),
  configPath = 'config/agent_validation.yaml',
  output = 'results/step0_5_interface_validation_v2',
  sites = null
} = {}) {
  root = resolve(root);
  const out = resolve(root, output);
  await mkdir(out, { recursive: true });
  const [config, normalization, crawlPolicy] = await Promise.all([
    readSimpleYaml(resolve(root, configPath)),
    readSimpleYaml(resolve(root, 'config/url_normalization.yaml')),
    readSimpleYaml(resolve(root, 'config/crawl_policy.yaml'))
  ]);
  configureNormalization(normalization);

  const selectedSites = sites?.length ? sites : config.sites;
  const tasks = JSON.parse(await readFile(resolve(root, config.evaluator_task_file), 'utf8'));
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {})
  });
  const captureRows = [], siteRows = [], pairRows = [], taskRows = [], perPageRecords = [];
  const provenance = {
    schema_version: '2.0',
    generated_at: new Date().toISOString(),
    method: 'live same-state browser validation',
    note: 'No BFS expansion. Each configured frozen URL is revisited once; rendered-all, CSS/geometry heuristic, and Playwright ARIA-role link exposure are extracted from the same fully loaded page state.',
    browser_version: await browser.version(),
    playwright_version: (await import('playwright/package.json', { with: { type: 'json' } })).default.version,
    sites: selectedSites
  };

  try {
    for (const site of selectedSites) {
      const runDir = config.frozen_snapshots?.[site];
      if (!runDir) throw new Error(`No configured frozen snapshot for ${site}`);
      const siteDir = resolve(root, runDir, site);
      const pages = await readJsonl(join(siteDir, 'pages.jsonl'));
      const nodes = new Set(pages.map(row => row.normalized_final_url).filter(Boolean));
      const aliases = await aliasesForSite(siteDir, pages);
      const seed = pages.find(row => row.crawl_depth === 0)?.normalized_final_url || pages[0]?.normalized_final_url;
      const seedUrl = seed;
      const siteMaps = new Map([
        ['rendered_all', new Map()],
        ['rendered_visible_followable', new Map()],
        ['rendered_aria_followable', new Map()]
      ]);

      const context = await browser.newContext({
        userAgent: crawlPolicy.user_agent,
        locale: crawlPolicy.locale,
        viewport: { width: Number(crawlPolicy.viewport_width), height: Number(crawlPolicy.viewport_height) }
      });
      const page = await context.newPage();
      try {
        for (const frozenPage of pages) {
          const source = frozenPage.normalized_final_url;
          const started = Date.now();
          try {
            const response = await page.goto(frozenPage.final_url || source, { waitUntil: 'domcontentloaded', timeout: Number(crawlPolicy.navigation_timeout_ms) });
            await sleep(Number(crawlPolicy.post_load_wait_ms || 0));
            const liveFinal = normalizeUrl(page.url());
            const resolvedSource = resolvePrimary(liveFinal, aliases, nodes);
            const sourceStable = resolvedSource === source;
            if (!response || response.status() >= 400 || !sourceStable) {
              captureRows.push({ site_id: site, source_url: source, live_final_url: liveFinal, status: response?.status() ?? null, source_stable: sourceStable, included: false, error: sourceStable ? `HTTP_${response?.status() ?? 'NO_RESPONSE'}` : 'LIVE_SOURCE_DRIFT' });
              continue;
            }
            const captured = await captureSameStateRepresentations({ page, source, seedUrl, aliases, nodes });
            for (const [label, map] of captured.maps) mergeMaps(siteMaps.get(label), map);
            for (const record of captured.records) perPageRecords.push({ site_id: site, source_url: source, ...record });
            captureRows.push({ site_id: site, source_url: source, live_final_url: liveFinal, status: response.status(), source_stable: true, included: true, latency_ms: Date.now() - started, rendered_all_records: captured.records.filter(r => r.representation === 'rendered_all').length, visible_followable_records: captured.records.filter(r => r.representation === 'rendered_visible_followable').length, aria_followable_records: captured.records.filter(r => r.representation === 'rendered_aria_followable').length });
          } catch (error) {
            captureRows.push({ site_id: site, source_url: source, live_final_url: null, status: null, source_stable: false, included: false, error: String(error), latency_ms: Date.now() - started });
          }
          await sleep(Number(crawlPolicy.rate_limit_ms || 0));
        }
      } finally {
        await context.close();
      }

      for (const [label, edges] of siteMaps) siteRows.push({ site_id: site, ...graphSummary(label, nodes, edges, seed), node_count: nodes.size, successful_live_pages: captureRows.filter(row => row.site_id === site && row.included).length, attempted_live_pages: pages.length });
      for (const [a, b] of [['rendered_all', 'rendered_visible_followable'], ['rendered_all', 'rendered_aria_followable'], ['rendered_visible_followable', 'rendered_aria_followable']]) {
        const A = new Set(siteMaps.get(a).keys()), B = new Set(siteMaps.get(b).keys());
        pairRows.push({ site_id: site, comparison: `${a}_vs_${b}`, edge_count_a: A.size, edge_count_b: B.size, edge_jaccard: jaccard(A, B), retained_edge_fraction: A.size ? [...A].filter(key => B.has(key)).length / A.size : null });
      }

      for (const task of tasks.filter(row => row.site === site)) {
        const start = resolvePrimary(task.start_url, aliases, nodes);
        const target = resolvePrimary(task.target_url, aliases, nodes);
        for (const [label, edges] of siteMaps) {
          const distance = shortestDistances(nodes, edges, start);
          taskRows.push({ site_id: site, task_id: task.task_id, representation: label, start_url: task.start_url, target_url: task.target_url, start_resolved: !!start, target_resolved: !!target, reachable: !!target && distance.has(target), shortest_path: target ? (distance.get(target) ?? null) : null });
        }
      }
    }
  } finally {
    await browser.close();
  }

  await Promise.all([
    writeFile(join(out, 'capture_status.csv'), csv(captureRows)),
    writeFile(join(out, 'per_site_graph_summary.csv'), csv(siteRows)),
    writeFile(join(out, 'pairwise_edge_comparison.csv'), csv(pairRows)),
    writeFile(join(out, 'task_level_path_comparison.csv'), csv(taskRows)),
    writeFile(join(out, 'per_page_link_records.jsonl'), perPageRecords.map(JSON.stringify).join('\n') + (perPageRecords.length ? '\n' : '')),
    writeFile(join(out, 'validation_summary.json'), JSON.stringify({ provenance, site_summary: siteRows, pairwise_edge_comparison: pairRows, task_level_path_comparison: taskRows }, null, 2) + '\n')
  ]);

  const report = [
    '# Step 0.5 v2: real browser interface validation',
    '',
    'This validation intentionally does not produce an automatic GO/MODIFY/STOP decision.',
    '',
    'All three representations are extracted from the same fully loaded live Playwright page state. `rendered_aria_followable` is defined by Playwright `getByRole("link")` exposure plus a usable HTTP(S) href. Frozen URLs define the node universe; no BFS expansion is performed.',
    '',
    'The previous offline HTML replay result was removed because external CSS, layout and JavaScript state were not preserved and therefore could not validate browser accessibility exposure.',
    '',
    'Review `capture_status.csv`, `pairwise_edge_comparison.csv`, and the task-specific start→target paths before changing the formal Agent treatment.'
  ];
  await writeFile(join(out, 'README.md'), report.join('\n') + '\n');
  return { output: out, provenance, siteRows, pairRows, taskRows };
}

function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--sites') options.sites = (argv[++i] || '').split(/[,\s]+/).map(x => x.trim()).filter(Boolean);
    else if (argv[i] === '--output') options.output = argv[++i];
    else if (argv[i] === '--config') options.configPath = argv[++i];
  }
  return options;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const result = await runStep05(parseArgs(process.argv.slice(2)));
  console.log(JSON.stringify({ output: result.output, provenance: result.provenance, sites: [...new Set(result.siteRows.map(row => row.site_id))] }, null, 2));
}
