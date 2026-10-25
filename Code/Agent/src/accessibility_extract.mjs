/**
 * Accessibility/ARIA-facing URL extraction for Step 0.5 v2.
 *
 * This module deliberately does NOT reconstruct accessibility state from saved
 * HTML.  It operates on a live, fully loaded Playwright Page so CSS, layout,
 * JavaScript and browser ARIA semantics all come from the same browser state as
 * the rendered-DOM baseline.
 */

export function isUsableNavigationHref(href) {
  return typeof href === 'string' && href.trim() !== '' && !/^\s*(javascript:|mailto:|tel:|data:|#)/i.test(href);
}

export function resolveNavigationHref(href, baseUrl) {
  if (!isUsableNavigationHref(href)) return null;
  try {
    const resolved = new URL(href, baseUrl);
    return ['http:', 'https:'].includes(resolved.protocol) ? resolved.href : null;
  } catch {
    return null;
  }
}

/**
 * Return URL-bearing links exposed by Playwright's ARIA role engine.
 * `getByRole('link')` uses browser accessibility semantics and excludes hidden
 * elements by default.  We still require a usable HTTP(S) href because the
 * primary graph is a URL navigation graph, not a general action graph.
 */
export async function extractAccessibilityFromPage(page) {
  const locator = page.getByRole('link');
  // One role-engine query plus one page-side pass is substantially more stable
  // than thousands of nth(i).evaluate() protocol round trips on dense pages.
  const domRecords = await locator.evaluateAll((elements) => elements.map((element, index) => {
      const href = element.getAttribute('href');
      const allCandidates = [...document.querySelectorAll('a[href], [role="link"][href]')];
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        href,
        base_url: document.baseURI,
        text_content: (element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 1000),
        aria_label: element.getAttribute('aria-label'),
        title: element.getAttribute('title'),
        explicit_role: element.getAttribute('role'),
        tag_name: element.tagName?.toLowerCase() || null,
        document_position: allCandidates.indexOf(element),
        geometry: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        computed_style: { display: style.display, visibility: style.visibility, opacity: style.opacity },
        aria_disabled: element.getAttribute('aria-disabled') === 'true',
        disabled: element.matches('[disabled]') || !!element.closest('[aria-disabled="true"], [disabled]')
      };
    }));
  return domRecords.map((dom, index) => {
    const absoluteUrl = resolveNavigationHref(dom.href, dom.base_url);
    const included = !!absoluteUrl && !dom.disabled;
    return {
      index,
      href: dom.href,
      absolute_url: absoluteUrl,
      anchor_text: dom.text_content,
      accessible_name: dom.aria_label || dom.text_content || dom.title || '',
      role: 'link',
      // Per-link ariaSnapshot() would reintroduce one protocol call per link.
      // Role exposure itself is the construct-defining diagnostic here.
      aria_snapshot: null,
      accessibility: {
        explicit_role: dom.explicit_role,
        aria_label: dom.aria_label,
        title: dom.title,
        tag_name: dom.tag_name,
        aria_disabled: dom.aria_disabled,
        disabled: dom.disabled,
        geometry: dom.geometry,
        computed_style: dom.computed_style
      },
      document_position: dom.document_position,
      extraction_reason: included
        ? 'playwright_aria_link_with_http_href'
        : (!absoluteUrl ? 'no_usable_http_href' : 'disabled'),
      included
    };
  });
}

/**
 * Frozen HTML alone is insufficient for this construct because external CSS,
 * layout and JavaScript state are part of accessibility exposure.  Keep this
 * explicit failure to prevent accidental reuse of the invalid Step 0.5-v1
 * offline replay design.
 */
export async function extractAccessibilityFromHtml() {
  throw new Error('OFFLINE_ACCESSIBILITY_REPLAY_UNSUPPORTED: use extractAccessibilityFromPage() on the same fully loaded Playwright page as the rendered baselines.');
}
