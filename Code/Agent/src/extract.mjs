export const EXTRACT_PAGE_SCRIPT = () => {
  const region = node => { for (let e=node; e; e=e.parentElement) { const t=e.tagName?.toLowerCase(), r=e.getAttribute?.('role')?.toLowerCase(), c=`${e.id||''} ${e.className||''}`.toLowerCase(); if (t==='header'||r==='banner') return 'header'; if (t==='nav'||r==='navigation') return 'nav'; if (t==='main'||r==='main'||t==='article'||/\b(main|content|article)\b/.test(c)) return t==='article'?'article':'main'; if(t==='aside'||r==='complementary')return'aside'; if(t==='footer'||r==='contentinfo')return'footer'; } return 'unknown'; };
  const path = node => { const a=[]; for(let e=node;e&&a.length<8;e=e.parentElement)a.unshift(`${e.tagName.toLowerCase()}${e.id?`#${e.id}`:''}`); return a.join(' > '); };
  const meaningful = node => (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 1000);
  const details = (anchor, index) => {
    const style = getComputedStyle(anchor);
    const rect = anchor.getBoundingClientRect();
    const href = anchor.getAttribute('href');
    const usable = !!href && !/^(javascript:|mailto:|tel:|data:|#)/i.test(href.trim());
    const visible = !!(rect.width && rect.height && style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || 1) > 0);
    const followable = visible && usable && !anchor.closest('[aria-disabled="true"], [disabled]');
    return { index, href, absolute_url: usable ? new URL(href, document.baseURI).href : null, anchor_text: meaningful(anchor), rel: anchor.getAttribute('rel'), title: anchor.getAttribute('title'), visible, followable, semantic_region: region(anchor), ancestor_path: path(anchor), dom_depth: path(anchor).split(' > ').length, document_position: index };
  };
  const jsonLd = [...document.querySelectorAll('script[type="application/ld+json"]')].map((node, index) => ({ index, raw: node.textContent, parse_error: (() => { try { JSON.parse(node.textContent); return null; } catch (error) { return String(error); } })() }));
  const meta = Object.fromEntries([...document.querySelectorAll('meta[property],meta[name]')].map(node => [node.getAttribute('property') || node.getAttribute('name'), node.getAttribute('content')]).filter(([, value]) => value));
  const urls = node => [...node.querySelectorAll?.('a[href]')||[]].map(a=>a.href); return { base_url: document.baseURI, anchors: [...document.querySelectorAll('a')].map(details), metadata: { title: document.title, canonical: document.querySelector('link[rel~="canonical"]')?.href || null, alternates: [...document.querySelectorAll('link[rel~="alternate"]')].map(node => ({ href: node.href, type: node.type || null, hreflang: node.hreflang || null })), breadcrumbs: [...document.querySelectorAll('[itemtype*="BreadcrumbList"], nav[aria-label*="breadcrumb" i], .breadcrumb, [class*="breadcrumb" i]')].map(node => ({ text: meaningful(node), urls: urls(node) })), open_graph: Object.fromEntries(Object.entries(meta).filter(([key]) => key.toLowerCase().startsWith('og:'))), meta, json_ld: jsonLd } };
};

export const EXTRACT_RAW_SCRIPT = html => {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const base = doc.querySelector('base[href]')?.getAttribute('href');
  const responseUrl = location.href; const baseUrl = base ? new URL(base, responseUrl).href : responseUrl;
  const region = node => { for (let e=node;e;e=e.parentElement) { const t=e.tagName?.toLowerCase(), r=e.getAttribute?.('role')?.toLowerCase(), c=`${e.id||''} ${e.className||''}`.toLowerCase(); if(t==='header'||r==='banner')return'header'; if(t==='nav'||r==='navigation')return'nav'; if(t==='main'||r==='main'||t==='article'||/\b(main|content|article)\b/.test(c))return t==='article'?'article':'main'; if(t==='aside'||r==='complementary')return'aside'; if(t==='footer'||r==='contentinfo')return'footer'; } return'unknown'; };
  const path = node => {const a=[];for(let e=node;e&&a.length<8;e=e.parentElement)a.unshift(`${e.tagName.toLowerCase()}${e.id?`#${e.id}`:''}`);return a.join(' > ');};
  const anchors = [...doc.querySelectorAll('a')].map((anchor, index) => {
    const href = anchor.getAttribute('href');
    const usable = !!href && !/^(javascript:|mailto:|tel:|data:|#)/i.test(href.trim());
    return { index, href, absolute_url: usable ? new URL(href, baseUrl).href : null, anchor_text: (anchor.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 1000), rel: anchor.getAttribute('rel'), title: anchor.getAttribute('title'), visible: null, followable: usable, semantic_region: region(anchor), ancestor_path:path(anchor), dom_depth:path(anchor).split(' > ').length, document_position:index };
  });
  const jsonLd = [...doc.querySelectorAll('script[type="application/ld+json"]')].map((node, index) => ({ index, raw: node.textContent, parse_error: (() => { try { JSON.parse(node.textContent); return null; } catch (error) { return String(error); } })() }));
  const metas = [...doc.querySelectorAll('meta[property],meta[name]')].map(node => [node.getAttribute('property') || node.getAttribute('name'), node.getAttribute('content')]).filter(([, value]) => value);
  const resolved = href => href ? new URL(href, baseUrl).href : null; return { base_url: baseUrl, anchors, metadata: { title: doc.title, canonical: resolved(doc.querySelector('link[rel~="canonical"]')?.getAttribute('href')), alternates: [...doc.querySelectorAll('link[rel~="alternate"]')].map(node => ({ href: resolved(node.getAttribute('href')), type: node.type || null, hreflang: node.hreflang || null })), breadcrumbs: [...doc.querySelectorAll('[itemtype*="BreadcrumbList"], nav[aria-label*="breadcrumb" i], .breadcrumb, [class*="breadcrumb" i]')].map(node => ({text:(node.textContent||'').replace(/\s+/g,' ').trim(),urls:[...node.querySelectorAll('a[href]')].map(a=>resolved(a.getAttribute('href')))})), open_graph: Object.fromEntries(metas.filter(([key]) => key.toLowerCase().startsWith('og:')).map(([k,v])=>[k,k.toLowerCase()==='og:url'?resolved(v):v])), meta: Object.fromEntries(metas), json_ld: jsonLd } };
};
