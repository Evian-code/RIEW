const CONDITIONS = new Set([
  'rendered_all',
  'rendered_visible_followable',
  'rendered_aria_followable',
  'information_all_unrestricted_navigation',
  'information_all_aria_restricted_navigation',
  'affordance_all_all',
  'affordance_all_aria_unknown',
  'affordance_all_aria_labeled',
  'affordance_aria_aria'
]);

export const RESTRICTED_LINK_ERROR_TYPE = 'link_not_available_in_current_interface';

export class RecoverableToolError extends Error {
  constructor({ type, currentUrl, requestedLinkId, requestedTargetUrl, observation }) {
    super(type);
    this.name = 'RecoverableToolError';
    this.recoverable_tool_error = true;
    this.tool_error = Object.freeze({
      type,
      current_url: currentUrl,
      requested_link_id: requestedLinkId,
      requested_target_url: requestedTargetUrl
    });
    this.observation = observation;
  }
}

// For the new four-arm experiment only, construct a *common* action universe.
// The original ARIA and rendered-all captures do not always share stable_key
// (e.g. document positions or accessible names differ). Reconcile in passes,
// prioritizing exact instances and never using one all-link twice.
// Last-resort destination matching is explicitly reported in diagnostics.
const canonicalCache = new WeakMap();
export function canonicalAriaAvailability(page) {
  const cached = canonicalCache.get(page);
  if (cached) return cached;
  const all = page.rendered_all_links;
  const aria = page.rendered_aria_followable_links;
  const matchedIndices = new Set();
  const takenAria = new Set();
  const diagnostics = { exact:0, position:0, label:0, destination:0, unmatched:0, aria_total:aria.length };
  const passes = [
    ['exact', (a,b)=>a.stable_key===b.stable_key],
    ['position', (a,b)=>a.destination_url===b.destination_url && a.document_position===b.document_position],
    ['label', (a,b)=>a.destination_url===b.destination_url && a.label===b.label],
    ['destination', (a,b)=>a.destination_url===b.destination_url]
  ];
  for (const [name, equivalent] of passes) {
    for (let i=0;i<aria.length;i++) {
      if (takenAria.has(i)) continue;
      const j=all.findIndex((a,k)=>!matchedIndices.has(k)&&equivalent(a,aria[i]));
      if (j<0) continue;
      takenAria.add(i);matchedIndices.add(j);diagnostics[name]++;
    }
  }
  diagnostics.unmatched=aria.length-takenAria.size;
  const allowedKeys=new Set([...matchedIndices].map(i=>all[i].stable_key));
  const result=Object.freeze({allowedKeys,diagnostics:Object.freeze(diagnostics)});
  canonicalCache.set(page,result);
  return result;
}

export const AGENT_OBSERVATION_SCHEMA_VERSION = '1.0';
export const AGENT_OBSERVATION_FIELDS = Object.freeze(['url', 'title', 'text', 'headings', 'links']);

export const AGENT_TOOL_SCHEMA = Object.freeze([
  Object.freeze({ name: 'start', description: 'Open the fixed start page for this task.', input_schema: Object.freeze({ type: 'object', properties: Object.freeze({}), additionalProperties: false }) }),
  Object.freeze({ name: 'read_current_page', description: 'Read the current page again without navigating.', input_schema: Object.freeze({ type: 'object', properties: Object.freeze({}), additionalProperties: false }) }),
  Object.freeze({ name: 'follow_link', description: 'Follow one link exposed on the current page.', input_schema: Object.freeze({ type: 'object', properties: Object.freeze({ link_id: Object.freeze({ type: 'integer', minimum: 1 }) }), required: Object.freeze(['link_id']), additionalProperties: false }) })
]);

function publicPageView(page, links, allowedKeys = null) {
  return Object.freeze({
    url: page.url,
    title: page.title,
    // Link labels are represented exactly once in `links`. Keeping them out of
    // `text` prevents links omitted by the active condition from leaking back
    // into the observation through the full rendered-page text.
    text: page.non_link_text,
    headings: page.headings,
    links: Object.freeze(links.map((link, index) => Object.freeze({
      link_id: index + 1, label: link.label, url: link.destination_url,
      ...(allowedKeys === null ? {} : { executable: allowedKeys.has(link.stable_key) })
    })))
  });
}

export class SnapshotInterface {
  #pages;
  #condition;
  #startUrl;
  #currentUrl = null;
  #started = false;
  #visited = [];
  #events = [];
  #onTransition;

  constructor({ pages, condition, startUrl, onTransition = null }) {
    if (!(pages instanceof Map)) throw new Error('pages must be a Map loaded from a frozen snapshot');
    if (!CONDITIONS.has(condition)) throw new Error(`Unsupported internal condition: ${condition}`);
    if (!pages.has(startUrl)) throw new Error('The fixed start URL is not in the frozen snapshot');
    this.#pages = pages;
    this.#condition = condition;
    this.#startUrl = startUrl;
    this.#onTransition = onTransition;
  }

  #linksFor(page) {
    if (this.#condition === 'rendered_all'
      || this.#condition === 'information_all_unrestricted_navigation'
      || this.#condition === 'information_all_aria_restricted_navigation'
      || this.#condition === 'affordance_all_all'
      || this.#condition === 'affordance_all_aria_unknown'
      || this.#condition === 'affordance_all_aria_labeled') return page.rendered_all_links;
    if (this.#condition === 'rendered_aria_followable') return page.rendered_aria_followable_links;
    if (this.#condition === 'affordance_aria_aria') {
      const {allowedKeys}=canonicalAriaAvailability(page);
      return page.rendered_all_links.filter(l=>allowedKeys.has(l.stable_key));
    }
    return page.rendered_visible_followable_links;
  }

  #isRestrictedLinkAllowed(page, link) {
    if (['affordance_all_aria_unknown','affordance_all_aria_labeled'].includes(this.#condition)) {
      return canonicalAriaAvailability(page).allowedKeys.has(link.stable_key);
    }
    if (this.#condition !== 'information_all_aria_restricted_navigation') return true;
    return page.rendered_aria_followable_links.some(candidate => candidate.stable_key === link.stable_key);
  }

  #view(url) {
    const page = this.#pages.get(url);
    if (!page) throw new Error('Navigation destination is not in the frozen snapshot');
    const allowedKeys = this.#condition === 'affordance_all_aria_labeled'
      ? canonicalAriaAvailability(page).allowedKeys : null;
    return publicPageView(page, this.#linksFor(page), allowedKeys);
  }

  #record(event) {
    const frozen = Object.freeze({ ...event, visited_urls: Object.freeze([...this.#visited]) });
    this.#events.push(frozen);
    this.#onTransition?.(frozen);
  }

  #rejectFollowLink({ sourceUrl, linkId, destinationUrl, message }) {
    this.#record({
      step_index: this.#events.length,
      current_url: sourceUrl,
      action: 'follow_link',
      selected_link_id: linkId,
      destination_url: destinationUrl,
      tool_result_status: 'error',
      tool_result_error: message
    });
    throw new Error(message);
  }

  start() {
    if (this.#started) throw new Error('The fixed start page has already been opened');
    this.#started = true;
    this.#currentUrl = this.#startUrl;
    this.#visited.push(this.#startUrl);
    this.#record({ step_index: 0, current_url: this.#startUrl, action: 'start', selected_link_id: null, destination_url: this.#startUrl, tool_result_status: 'ok', tool_result_error: null });
    return this.#view(this.#currentUrl);
  }

  read_current_page() {
    if (!this.#started) throw new Error('start must be called before reading the current page');
    this.#record({ step_index: this.#events.length, current_url: this.#currentUrl, action: 'read_current_page', selected_link_id: null, destination_url: this.#currentUrl, tool_result_status: 'ok', tool_result_error: null });
    return this.#view(this.#currentUrl);
  }

  follow_link(linkId) {
    if (!this.#started) throw new Error('start must be called before following a link');
    if (!Number.isInteger(linkId) || linkId < 1) throw new Error('link_id must be a positive integer');
    const sourceUrl = this.#currentUrl;
    const page = this.#pages.get(sourceUrl);
    const link = this.#linksFor(page)[linkId - 1];
    if (!link) {
      return this.#rejectFollowLink({
        sourceUrl,
        linkId,
        destinationUrl: null,
        message: 'link_id is not exposed on the current page'
      });
    }
    if (!this.#isRestrictedLinkAllowed(page, link)) {
      const error = RESTRICTED_LINK_ERROR_TYPE;
      this.#record({
        step_index: this.#events.length,
        current_url: sourceUrl,
        action: 'follow_link',
        selected_link_id: linkId,
        destination_url: link.destination_url,
        tool_result_status: 'error',
        tool_result_error: error
      });
      throw new RecoverableToolError({
        type: error,
        currentUrl: sourceUrl,
        requestedLinkId: linkId,
        requestedTargetUrl: link.destination_url,
        observation: this.#view(sourceUrl)
      });
    }
    if (!this.#pages.has(link.destination_url)) {
      return this.#rejectFollowLink({
        sourceUrl,
        linkId,
        destinationUrl: link.destination_url,
        message: 'Exposed link destination is outside the frozen snapshot'
      });
    }
    this.#currentUrl = link.destination_url;
    this.#visited.push(this.#currentUrl);
    this.#record({ step_index: this.#events.length, current_url: sourceUrl, action: 'follow_link', selected_link_id: linkId, destination_url: this.#currentUrl, tool_result_status: 'ok', tool_result_error: null });
    return this.#view(this.#currentUrl);
  }

  get visitedUrls() { return Object.freeze([...this.#visited]); }
  get transitionLog() { return Object.freeze([...this.#events]); }
  get currentUrl() { return this.#currentUrl; }
}
