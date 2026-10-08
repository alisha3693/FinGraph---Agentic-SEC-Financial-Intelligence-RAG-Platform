// Number formatting, chart colours, CSV export, and the citation-marker pattern.

// Citation markers in model text: [3], [1][2], [1, 4].
export const CITE_RE = /\[(\d+(?:\s*,\s*\d+)*)\]/g;

export const METRIC_COLORS = { "Revenue": "#1d4ed8", "Net Income": "#0a7a4c", "EPS": "#b45309", "Assets": "#4338ca", "Liabilities": "#b42318" };

export const COMPARISON_PALETTE = ["#1d4ed8", "#0a7a4c", "#b45309", "#4338ca", "#b42318", "#0e7490", "#7c3aed", "#525252"];

export function formatCell(column, value) {
  if (value === null || value === undefined) return '—';
  if (typeof value !== 'number') return value;
  if (column.unit === '$B') return `$${value.toFixed(2)}B`;
  if (column.unit === '$') return `$${value.toFixed(2)}`;
  return value;
}

export function toCsv(columns, rows) {
  const escape = (v) => {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = columns.map((c) => escape(c.label)).join(',');
  const lines = rows.map((row) => columns.map((c) => escape(row[c.key])).join(','));
  return [header, ...lines].join('\n');
}

export function downloadCsv(filename, csvText) {
  const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
