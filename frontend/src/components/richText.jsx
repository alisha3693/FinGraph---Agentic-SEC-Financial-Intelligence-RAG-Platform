// Answer text helpers: highlighted figures and clickable citation numbers.

import React from 'react';
import { CITE_RE } from '../lib/format';

// Matches currency amounts ("$391.04B", "$6.11") and percentages ("12.5%") so they can be
// set off from surrounding prose without touching any other text. Longer words (billion,
// million) must be listed before their single-letter abbreviations (B, M) — regex
// alternation takes the first branch that matches at a position, not the longest, so
// "B|billion" would match just the "b" of "billion" and leave "illion" outside the highlight.
// An optional leading sign is part of the figure, so "+6.4%" isn't split into "+" and "6.4%".
const FIGURE_RE = /([+\-−]?\$\d[\d,]*\.?\d*(?:\s?(?:billion|million|thousand|B|M|K))?|(?:[+\-−](?=\d)|\b)\d+(?:\.\d+)?%)/i;

export function renderFigureLine(line) {
  return line.split(FIGURE_RE).map((part, i) =>
    i % 2 === 1
      ? <span key={i} className="figure">{part}</span>
      : <React.Fragment key={i}>{part}</React.Fragment>
  );
}


function sourceLabel(src) {
  const m = src?.metadata || {};
  if (!m.filing_type) return 'SEC XBRL company facts';
  return [m.company, m.filing_type, m.filing_date && `filed ${m.filing_date}`].filter(Boolean).join(' · ');
}

export function renderRichText(text, sources, onCite) {
  const out = [];
  const re = new RegExp(CITE_RE.source, 'g');
  let last = 0;
  let key = 0;
  let match;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) out.push(<React.Fragment key={key++}>{renderFigureLine(text.slice(last, match.index))}</React.Fragment>);
    for (const n of match[1].split(',').map((x) => parseInt(x.trim(), 10))) {
      const src = sources?.[n - 1];
      out.push(src
        ? <button key={key++} type="button" className="cite" title={sourceLabel(src)} onClick={() => onCite(n)}>{n}</button>
        : <span key={key++} className="cite cite-missing">{n}</span>);
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(<React.Fragment key={key++}>{renderFigureLine(text.slice(last))}</React.Fragment>);
  return out;
}
