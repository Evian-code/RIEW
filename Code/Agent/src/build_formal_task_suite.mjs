import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { loadConfiguredFrozenSnapshots } from './frozen_snapshot_loader.mjs';

const OUTPUTS = Object.freeze({
  evaluator: 'docs/agent_formal_task_suite_v1.json',
  prompts: 'docs/agent_formal_task_prompts_v1.json'
});

const TASK_SPECS = Object.freeze([
  {
    task_id: 'mdn_formal_01', site: 'mdn_js', structural_tier: 'delta_0_stable',
    start_url: 'https://developer.mozilla.org/en-US/docs/Web',
    target_url: 'https://developer.mozilla.org/en-US/docs/Glossary/Node.js',
    question: 'Starting from the supplied MDN Web technology page, navigate within MDN to find the glossary page about the cross-platform JavaScript runtime commonly used for server-side applications. Report the main visible heading on the destination page.',
    expected: [2, 2, 1, 1, 1, 1]
  },
  {
    task_id: 'usa_gov_formal_01', site: 'usa_gov', structural_tier: 'delta_0_stable',
    start_url: 'https://www.usa.gov/about', target_url: 'https://www.usa.gov/education',
    question: 'Starting from the supplied USA.gov page about USAGov, navigate within USA.gov to find the topic page that collects information about schools, financial aid, student loans, and studying in the United States. Report the main visible heading on the destination page.',
    expected: [2, 2, 1, 1, 1, 1]
  },
  {
    task_id: 'wikipedia_formal_01', site: 'wikipedia', structural_tier: 'delta_0_stable',
    start_url: 'https://en.wikipedia.org/wiki/Crawl_frontier', target_url: 'https://en.wikipedia.org/wiki/Website',
    question: 'Starting from the supplied Wikipedia article about a crawl frontier, navigate within Wikipedia to find the article about a collection of interlinked web pages and related content identified by a common domain name. Report the main visible heading on the destination page.',
    expected: [2, 2, 2, 2, 2, 2]
  },
  {
    task_id: 'mdn_formal_02', site: 'mdn_js', structural_tier: 'delta_0_redundancy_reduced',
    start_url: 'https://developer.mozilla.org/en-US/docs/Web/HTML',
    target_url: 'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/eval',
    question: 'Starting from the supplied MDN page about HTML, navigate within MDN to find the JavaScript reference page for the function that evaluates a string as JavaScript code. Report the main visible heading on the destination page.',
    expected: [2, 2, 12, 1, 12, 1]
  },
  {
    task_id: 'usa_gov_formal_02', site: 'usa_gov', structural_tier: 'delta_0_redundancy_reduced',
    start_url: 'https://www.usa.gov/life-events', target_url: 'https://www.usa.gov/contact-us',
    question: 'Starting from the supplied USA.gov life-events page, navigate within USA.gov to find the page that lists ways to call or chat with USAGov and report a website issue. Report the main visible heading on the destination page.',
    expected: [2, 2, 5, 3, 5, 3]
  },
  {
    task_id: 'wikipedia_formal_02', site: 'wikipedia', structural_tier: 'delta_0_redundancy_reduced',
    start_url: 'https://en.wikipedia.org/wiki/Web_content', target_url: 'https://en.wikipedia.org/wiki/List_of_search_engines',
    question: 'Starting from the supplied Wikipedia article about web content, navigate within Wikipedia to find the article that lists search engines by topic, data type, and model. Report the main visible heading on the destination page.',
    expected: [2, 2, 2, 1, 2, 1]
  },
  {
    task_id: 'mdn_formal_03', site: 'mdn_js', structural_tier: 'delta_plus_1',
    start_url: 'https://developer.mozilla.org/en-US/docs/Glossary/DOM',
    target_url: 'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Object',
    question: 'Starting from the supplied MDN glossary page about the DOM, navigate within MDN to find the JavaScript reference page for the fundamental object type from which many other objects inherit. Report the main visible heading on the destination page.',
    expected: [2, 3, 11, 3, 11, 3]
  },
  {
    task_id: 'mdn_formal_04', site: 'mdn_js', structural_tier: 'delta_plus_1',
    start_url: 'https://developer.mozilla.org/en-US/docs/Glossary/Garbage_collection',
    target_url: 'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Function/toString',
    question: 'Starting from the supplied MDN glossary page about garbage collection, navigate within MDN to find the JavaScript reference page for the Function method that returns a string representing a function source. Report the main visible heading on the destination page.',
    expected: [2, 3, 1, 3, 1, 3]
  },
  {
    task_id: 'usa_gov_formal_03', site: 'usa_gov', structural_tier: 'delta_plus_1',
    start_url: 'https://www.usa.gov/website-analytics', target_url: 'https://www.usa.gov/benefit-finder',
    question: 'Starting from the supplied USA.gov website-analytics page, navigate within USA.gov to find the tool for locating government benefits and financial assistance. Report the main visible heading on the destination page.',
    expected: [2, 3, 1, 4, 1, 2]
  },
  {
    task_id: 'usa_gov_formal_04', site: 'usa_gov', structural_tier: 'delta_plus_1',
    start_url: 'https://www.usa.gov/', target_url: 'https://www.usa.gov/es',
    question: 'Starting from the supplied English USA.gov home page, navigate within USA.gov to find its Spanish-language government-information home page. Report the main visible heading on the destination page.',
    expected: [1, 2, 1, 2, 1, 2]
  },
  {
    task_id: 'wikipedia_formal_03', site: 'wikipedia', structural_tier: 'delta_plus_1',
    start_url: 'https://en.wikipedia.org/wiki/Internet_bot', target_url: 'https://en.wikipedia.org/wiki/FOAF',
    question: 'Starting from the supplied Wikipedia article about Internet bots, navigate within Wikipedia to find the article about the machine-readable ontology for describing people, their activities, and relations to other people and objects. Report the main visible heading on the destination page.',
    expected: [2, 3, 1, 2, 1, 2]
  },
  {
    task_id: 'wikipedia_formal_04', site: 'wikipedia', structural_tier: 'delta_plus_1',
    start_url: 'https://en.wikipedia.org/wiki/List_of_search_engines', target_url: 'https://en.wikipedia.org/wiki/Crawl_frontier',
    question: 'Starting from the supplied Wikipedia list of search engines, navigate within Wikipedia to find the article about the data structure a web crawler uses to manage URLs awaiting visits. Report the main visible heading on the destination page.',
    expected: [2, 3, 1, 1, 1, 1]
  },
  {
    task_id: 'mdn_formal_05', site: 'mdn_js', structural_tier: 'delta_plus_2',
    start_url: 'https://developer.mozilla.org/en-US/docs/Glossary/API',
    target_url: 'https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch',
    question: 'Starting from the supplied MDN glossary page about APIs, navigate within MDN to find the guide explaining how to make network requests with the Fetch API. Report the main visible heading on the destination page.',
    expected: [1, 3, 1, 1, 1, 1]
  },
  {
    task_id: 'mdn_formal_06', site: 'mdn_js', structural_tier: 'delta_plus_2',
    start_url: 'https://developer.mozilla.org/en-US/docs/Learn_web_development',
    target_url: 'https://developer.mozilla.org/en-US/docs/Learn_web_development/Getting_started/Your_first_website/Adding_interactivity',
    question: 'Starting from the supplied MDN Learn web development page, navigate within MDN to find the first-website lesson about adding JavaScript-driven interactivity. Report the main visible heading on the destination page.',
    expected: [1, 3, 1, 3, 1, 3]
  },
  {
    task_id: 'mdn_formal_07', site: 'mdn_js', structural_tier: 'delta_plus_2',
    start_url: 'https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/CSS_layout',
    target_url: 'https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Animations/Using',
    question: 'Starting from the supplied MDN learning page about CSS layout, navigate within MDN to find the guide for configuring and using CSS animations. Report the main visible heading on the destination page.',
    expected: [1, 3, 1, 4, 1, 4]
  },
  {
    task_id: 'usa_gov_formal_05', site: 'usa_gov', structural_tier: 'delta_plus_2',
    start_url: 'https://www.usa.gov/es/llamenos', target_url: 'https://www.usa.gov/phone',
    question: 'Starting from the supplied Spanish USA.gov contact-by-phone page, navigate within USA.gov to find the English page with USAGov phone contact details and operating hours. Report the main visible heading on the destination page.',
    expected: [1, 3, 1, 13, 1, 1]
  },
  {
    task_id: 'wikipedia_formal_05', site: 'wikipedia', structural_tier: 'delta_plus_2',
    start_url: 'https://en.wikipedia.org/wiki/List_of_search_engines', target_url: 'https://en.wikipedia.org/wiki/Search_engine_indexing',
    question: 'Starting from the supplied Wikipedia list of search engines, navigate within Wikipedia to find the article about collecting, parsing, and storing data to support fast information retrieval by a search engine. Report the main visible heading on the destination page.',
    expected: [1, 3, 1, 1, 1, 1]
  },
  {
    task_id: 'wikipedia_formal_06', site: 'wikipedia', structural_tier: 'delta_plus_2',
    start_url: 'https://en.wikipedia.org/wiki/Recursion', target_url: 'https://en.wikipedia.org/wiki/HTML',
    question: 'Starting from the supplied Wikipedia article about recursion, navigate within Wikipedia to find the article about the standard markup language used for documents designed for display in a web browser. Report the main visible heading on the destination page.',
    expected: [2, 4, 1, 3, 1, 1]
  }
]);

function graphMetrics(pages, startUrl, targetUrl, linkField) {
  if (!pages.has(startUrl) || !pages.has(targetUrl)) throw new Error(`Missing endpoint: ${startUrl} -> ${targetUrl}`);
  const distance = new Map([[startUrl, 0]]);
  const pathCount = new Map([[startUrl, 1]]);
  const firstHops = new Map([[startUrl, new Set()]]);
  const parent = new Map();
  const queue = [startUrl];
  for (let head = 0; head < queue.length; head += 1) {
    const current = queue[head];
    const nextDistance = distance.get(current) + 1;
    const neighbors = [...new Set((pages.get(current)?.[linkField] || []).map(link => link.destination_url))];
    for (const neighbor of neighbors) {
      if (!pages.has(neighbor)) continue;
      const candidateFirstHops = current === startUrl ? new Set([neighbor]) : firstHops.get(current);
      if (!distance.has(neighbor)) {
        distance.set(neighbor, nextDistance);
        pathCount.set(neighbor, pathCount.get(current));
        firstHops.set(neighbor, new Set(candidateFirstHops));
        parent.set(neighbor, current);
        queue.push(neighbor);
      } else if (distance.get(neighbor) === nextDistance) {
        pathCount.set(neighbor, pathCount.get(neighbor) + pathCount.get(current));
        for (const firstHop of candidateFirstHops) firstHops.get(neighbor).add(firstHop);
      }
    }
  }
  if (!distance.has(targetUrl)) throw new Error(`Target is unreachable through ${linkField}: ${startUrl} -> ${targetUrl}`);
  const path = [];
  for (let current = targetUrl; current; current = parent.get(current)) path.push(current);
  path.reverse();
  return Object.freeze({
    distance: distance.get(targetUrl),
    shortest_path_count: pathCount.get(targetUrl),
    first_hop_count: firstHops.get(targetUrl).size,
    reference_path: Object.freeze(path)
  });
}

export async function buildFormalTaskSuite({ root = process.cwd(), write = false } = {}) {
  const { snapshots } = await loadConfiguredFrozenSnapshots({ root, configPath: 'config/agent_pilot_aria.yaml' });
  const tasks = TASK_SPECS.map(spec => {
    const snapshot = snapshots.get(spec.site);
    const all = graphMetrics(snapshot.pages, spec.start_url, spec.target_url, 'rendered_all_links');
    const aria = graphMetrics(snapshot.pages, spec.start_url, spec.target_url, 'rendered_aria_followable_links');
    const actual = [all.distance, aria.distance, all.shortest_path_count, aria.shortest_path_count, all.first_hop_count, aria.first_hop_count];
    if (JSON.stringify(actual) !== JSON.stringify(spec.expected)) {
      throw new Error(`${spec.task_id} structural metrics changed: expected ${spec.expected.join(',')}; got ${actual.join(',')}`);
    }
    const targetPage = snapshot.pages.get(spec.target_url);
    const mainHeading = targetPage.headings.find(heading => heading.level === 1)?.text;
    if (!mainHeading) throw new Error(`${spec.task_id} target has no H1 answer`);
    const { expected, ...publicSpec } = spec;
    return Object.freeze({
      ...publicSpec,
      answer_key: Object.freeze({ field: 'main_visible_heading', value: mainHeading }),
      d_all: all.distance,
      d_aria: aria.distance,
      delta_d: aria.distance - all.distance,
      shortest_path_count_rendered_all: all.shortest_path_count,
      shortest_path_count_rendered_aria: aria.shortest_path_count,
      first_hop_count_rendered_all: all.first_hop_count,
      first_hop_count_rendered_aria: aria.first_hop_count,
      reference_path_rendered_all: all.reference_path,
      reference_path_rendered_aria: aria.reference_path,
      snapshot_ref: 'data/runs/agent_interface_aria_v1_2026-09-20',
      editorial_status: 'supervisor_confirmed_formal_v1'
    });
  });
  const prompts = tasks.map(({ task_id, site, start_url, question }) => ({ task_id, site, start_url, question }));
  if (write) {
    await Promise.all([
      writeFile(resolve(root, OUTPUTS.evaluator), `${JSON.stringify(tasks, null, 2)}\n`, { flag: 'wx' }),
      writeFile(resolve(root, OUTPUTS.prompts), `${JSON.stringify(prompts, null, 2)}\n`, { flag: 'wx' })
    ]);
  }
  return Object.freeze({ task_count: tasks.length, tasks: Object.freeze(tasks), prompts: Object.freeze(prompts), outputs: OUTPUTS });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const result = await buildFormalTaskSuite({ write: process.argv.includes('--write') });
  console.log(JSON.stringify({ task_count: result.task_count, outputs: result.outputs, wrote_files: process.argv.includes('--write') }, null, 2));
}
