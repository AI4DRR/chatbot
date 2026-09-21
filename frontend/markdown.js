/* Minimal, safe Markdown → HTML for assistant answers, plus [n] citation buttons.
 * Everything is HTML-escaped first; only the constructs the answer prompt asks the
 * model for are rendered (headings, paragraphs, bullet/numbered lists, bold, italic,
 * inline code, fenced code). Raw HTML and links are never emitted. */
(function () {
  'use strict';

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));

  // The pipeline prepends this exact line to an answer that cites nothing.
  const UNSOURCED_NOTICE = '_Not sourced from the UNDRR knowledge base._';

  function inline(text, maxCite) {
    let out = esc(text);
    out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>');
    out = out.replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, '$1<em>$2</em>');
    // [n] → citation button when n points at a source of this answer; otherwise left as text.
    out = out.replace(/\[(\d+)\]/g, (m, n) => {
      const k = Number(n);
      return k >= 1 && k <= maxCite ? `<button class="cite" data-src="${k}" type="button">${k}</button>` : m;
    });
    return out;
  }

  function render(markdown, sourceCount) {
    let text = String(markdown || '').replace(/\r\n/g, '\n');
    let notice = '';
    if (text.startsWith(UNSOURCED_NOTICE)) {
      notice = '<span class="unsourced">Not sourced from the UNDRR knowledge base</span>';
      text = text.slice(UNSOURCED_NOTICE.length).replace(/^\s+/, '');
    }
    const lines = text.split('\n');
    const html = [];
    let para = [];
    let list = null; // { tag, items }
    let code = null; // lines

    const flushPara = () => { if (para.length) { html.push(`<p>${inline(para.join(' '), sourceCount)}</p>`); para = []; } };
    const flushList = () => { if (list) { html.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i, sourceCount)}</li>`).join('')}</${list.tag}>`); list = null; } };

    for (const raw of lines) {
      const line = raw.replace(/\s+$/, '');
      if (code) {
        if (/^```/.test(line)) { html.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`); code = null; } else code.push(line);
        continue;
      }
      if (/^```/.test(line)) { flushPara(); flushList(); code = []; continue; }
      if (!line.trim()) { flushPara(); flushList(); continue; }
      const h = /^(#{1,6})\s+(.*)$/.exec(line);
      if (h) { flushPara(); flushList(); html.push(`<h4>${inline(h[2], sourceCount)}</h4>`); continue; }
      const ul = /^\s*[-*•]\s+(.*)$/.exec(line);
      const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (ul || ol) {
        flushPara();
        const tag = ul ? 'ul' : 'ol';
        if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
        list.items.push((ul || ol)[1]);
        continue;
      }
      if (list && /^\s{2,}\S/.test(raw)) { list.items[list.items.length - 1] += ' ' + line.trim(); continue; } // wrapped item
      flushList();
      para.push(line.trim());
    }
    flushPara(); flushList();
    if (code) html.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
    return notice + html.join('');
  }

  window.AI4DRR_MARKDOWN = { render, esc, UNSOURCED_NOTICE };
})();
