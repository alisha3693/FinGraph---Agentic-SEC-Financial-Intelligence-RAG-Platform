// Data helpers for the chart views in Charts.jsx: everything here works on the backend's
// chart_meta.dataset and the response's sources, and never draws anything.

import { CITE_RE } from './format';

export const val = (ds, t, m, yr) => {
  const v = ds?.values?.[t]?.[m]?.[String(yr)];
  return typeof v === 'number' ? v : null;
};

export const hasMetric = (ds, m) => ds.companies.some((t) => ds.years.some((y) => val(ds, t, m, y) !== null));

export const commonYears = (ds, m, companies = ds.companies) =>
  ds.years.filter((y) => companies.every((t) => val(ds, t, m, y) !== null));

export const cagr = (a, b, n) => (a > 0 && b > 0 && n > 0 ? (b / a) ** (1 / n) - 1 : null);

export function fmt(v, unit) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (unit === '%') return `${(v * 100).toFixed(1)}%`;
  if (unit === '$B') return Math.abs(v) >= 100 ? `$${v.toFixed(0)}B` : `$${v.toFixed(1)}B`;
  if (unit === '$') return `$${v.toFixed(2)}`;
  if (unit === 'idx') return `${Math.round(v)}`;
  return String(v);
}

export function niceTicks(min, max, count = 4) {
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => (max - min) / s <= count) || raw;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

export function filingSources(response) {
  return (response?.sources || [])
    .map((src, i) => ({ n: i + 1, meta: src.metadata || {} }))
    .filter((s) => s.meta.filing_type && s.meta.filing_date);
}

// Theme × company counts of cited passages, from the filing sections' citation numbers.
export function themeMatrix(response) {
  const parts = response?.structured?.parts || [];
  const filings = parts.find((p) => p.key === 'filings');
  const sections = (filings?.sections || []).filter((s) => s.title);
  if (sections.length < 2) return null;
  const sources = response.sources || [];
  const companies = [];
  const rows = sections.map((sec) => {
    const counts = {};
    const seen = new Set();
    sec.points.forEach((p) => {
      for (const m of p.matchAll(CITE_RE)) {
        m[1].split(',').map((x) => parseInt(x.trim(), 10)).forEach((n) => {
          const company = sources[n - 1]?.metadata?.company;
          if (!company || seen.has(n)) return;
          seen.add(n);
          counts[company] = (counts[company] || 0) + 1;
          if (!companies.includes(company)) companies.push(company);
        });
      }
    });
    return { title: sec.title, counts };
  });
  return companies.length ? { companies, rows } : null;
}

export function availableCharts(response) {
  const meta = response?.chart_meta || {};
  const ds = meta.dataset;
  const out = [];
  if (response?.chart_data?.length) out.push('trend');
  if (ds && ds.years?.length) {
    const multi = ds.companies.length >= 2;
    if (ds.years.length >= 2) out.push('indexed', 'yoy');
    if (hasMetric(ds, 'revenue') && hasMetric(ds, 'net_income')) out.push('margin');
    if (hasMetric(ds, 'assets') && hasMetric(ds, 'liabilities')) out.push('composition');
    if (multi) out.push('ranking');
    if (multi && ds.years.length >= 2) out.push('growth');
    if (ds.companies.length >= 3 && ds.years.length >= 2) out.push('scatter');
  }
  if (themeMatrix(response)) out.push('themes');
  if (filingSources(response).length >= 2) out.push('sources');
  return out;
}
