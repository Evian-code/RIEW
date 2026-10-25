export function primaryNodeId(page) { return page.normalized_final_url; }
export function aliasMapping(page) { return { requested_url: page.requested_url, final_url: page.final_url, normalized_final_url: page.normalized_final_url, declared_canonical_url: page.declared_canonical_url, content_hash: page.content_hash, alias_type: page.requested_url === page.final_url ? 'identity' : 'redirect' }; }

export function buildPrimaryAliasMap(pages, normalize = value => value) {
  const aliases = new Map();
  for (const page of pages) {
    const primary = page.normalized_final_url;
    if (!primary) continue;
    for (const value of [page.requested_url, page.final_url, primary]) {
      if (!value) continue;
      aliases.set(value, primary);
      const normalized = normalize(value);
      if (normalized) aliases.set(normalized, primary);
    }
  }
  return aliases;
}
