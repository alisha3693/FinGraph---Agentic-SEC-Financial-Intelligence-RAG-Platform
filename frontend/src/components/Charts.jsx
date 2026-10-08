import React, { useState } from 'react';
import {
  val, hasMetric, commonYears, cagr, fmt, niceTicks, filingSources, themeMatrix,
} from '../lib/chartData';

// Chart views for a query response. The backend sends every metric for every year and
// company (chart_meta.dataset) plus the plan's suggested view (chart_meta.suggested); each
// view below is derived from that data here, so switching views needs no new request.
// "trend" is the original line chart, rendered by the caller (App's TrendChart).

const CHART_LABELS = {
  trend: 'Trend',
  indexed: 'Growth index',
  yoy: 'YoY change',
  margin: 'Margin',
  composition: 'Balance sheet',
  ranking: 'Ranking',
  growth: 'Growth rate',
  scatter: 'Size vs growth',
  themes: 'Themes',
  sources: 'Sources',
};

const W = 460;
const H = 240;
const PAD = { top: 14, right: 16, bottom: 28, left: 50 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

// ------------------------------------------------------------------ data helpers

// ------------------------------------------------------------------ SVG pieces

function YAxis({ ticks, yFor, unit }) {
  return ticks.map((t) => (
    <g key={t}>
      <line x1={PAD.left} y1={yFor(t)} x2={W - PAD.right} y2={yFor(t)}
        style={{ stroke: t === 0 ? 'var(--border-strong)' : 'var(--border)' }} strokeWidth="1" />
      <text x={PAD.left - 8} y={yFor(t) + 3} style={{ fill: 'var(--text-muted)' }} fontSize="9" textAnchor="end">
        {fmt(t, unit)}
      </text>
    </g>
  ));
}

function Legend({ items }) {
  return (
    <div className="chart-legend">
      {items.map((it) => (
        <div key={it.name} className="legend-item">
          <span className="legend-dot" style={{ backgroundColor: it.color }}></span>
          <span>{it.name}</span>
        </div>
      ))}
    </div>
  );
}

function Tooltip({ xPct, title, rows }) {
  return (
    <div className="chart-tooltip" style={{ left: `${xPct}%` }}>
      <div className="tooltip-year">{title}</div>
      {rows.map((r) => (
        <div key={r.name} className="tooltip-row">
          <span className="legend-dot" style={{ backgroundColor: r.color }}></span>
          <span>{r.name}: {r.text}</span>
        </div>
      ))}
    </div>
  );
}

// Multi-series line over labelled x positions. A null value breaks the line.
function LineView({ labels, series, unit, reference }) {
  const [hover, setHover] = useState(null);
  const all = series.flatMap((s) => s.values).filter((v) => v !== null);
  if (all.length === 0) return <p className="chart-empty">No values to plot.</p>;
  const ticks = niceTicks(Math.min(...all, reference ?? Infinity), Math.max(...all, reference ?? -Infinity));
  const lo = ticks[0];
  const hi = ticks[ticks.length - 1];
  const xFor = (i) => PAD.left + (i * PLOT_W) / (labels.length - 1 || 1);
  const yFor = (v) => PAD.top + PLOT_H - ((v - lo) * PLOT_H) / (hi - lo || 1);

  return (
    <>
      <div className="chart-frame">
        <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" preserveAspectRatio="none">
          <YAxis ticks={ticks} yFor={yFor} unit={unit} />
          {reference !== undefined && (
            <line x1={PAD.left} y1={yFor(reference)} x2={W - PAD.right} y2={yFor(reference)}
              style={{ stroke: 'var(--text-muted)' }} strokeWidth="1" strokeDasharray="3 3" />
          )}
          {labels.map((l, i) => (
            <text key={l} x={xFor(i)} y={H - PAD.bottom + 15} style={{ fill: 'var(--text-muted)' }} fontSize="10" textAnchor="middle">{l}</text>
          ))}
          {hover !== null && (
            <line x1={xFor(hover)} y1={PAD.top} x2={xFor(hover)} y2={H - PAD.bottom}
              style={{ stroke: 'var(--border-strong)' }} strokeWidth="1" strokeDasharray="2 3" />
          )}
          {series.map((s) => {
            const d = s.values
              .map((v, i) => (v === null ? null : `${i === 0 || s.values[i - 1] === null ? 'M' : 'L'} ${xFor(i)} ${yFor(v)}`))
              .filter(Boolean)
              .join(' ');
            return (
              <g key={s.name}>
                <path d={d} fill="none" stroke={s.color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                {s.values.map((v, i) => (v === null ? null : (
                  <circle key={i} cx={xFor(i)} cy={yFor(v)} r={hover === i ? 3.5 : 2.5} fill={s.color}
                    style={{ stroke: 'var(--bg-surface)' }} strokeWidth="1" />
                )))}
              </g>
            );
          })}
          {labels.map((l, i) => {
            const start = i === 0 ? PAD.left : (xFor(i - 1) + xFor(i)) / 2;
            const end = i === labels.length - 1 ? W - PAD.right : (xFor(i) + xFor(i + 1)) / 2;
            return (
              <rect key={l} x={start} y={PAD.top} width={Math.max(end - start, 0)} height={PLOT_H} fill="transparent"
                onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
            );
          })}
        </svg>
        {hover !== null && (
          <Tooltip xPct={(xFor(hover) / W) * 100} title={labels[hover]}
            rows={series.map((s) => ({ name: s.name, color: s.color, text: fmt(s.values[hover], unit) }))} />
        )}
      </div>
      <Legend items={series} />
    </>
  );
}

// Grouped vertical bars; negative values hang below the zero line.
function BarsView({ labels, series, unit, stacked = false }) {
  const [hover, setHover] = useState(null);
  const sums = labels.map((_, i) => series.reduce((acc, s) => acc + Math.max(s.values[i] ?? 0, 0), 0));
  const all = stacked ? sums : series.flatMap((s) => s.values).filter((v) => v !== null);
  if (all.length === 0) return <p className="chart-empty">No values to plot.</p>;
  const ticks = niceTicks(Math.min(0, ...all), Math.max(0, ...all));
  const lo = ticks[0];
  const hi = ticks[ticks.length - 1];
  const yFor = (v) => PAD.top + PLOT_H - ((v - lo) * PLOT_H) / (hi - lo || 1);
  const groupW = PLOT_W / labels.length;
  const inner = groupW * 0.72;
  const barW = stacked ? inner : inner / series.length;

  return (
    <>
      <div className="chart-frame">
        <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" preserveAspectRatio="none">
          <YAxis ticks={ticks} yFor={yFor} unit={unit} />
          {labels.map((l, i) => {
            const gx = PAD.left + i * groupW + (groupW - inner) / 2;
            let base = 0;
            return (
              <g key={l} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={PAD.left + i * groupW} y={PAD.top} width={groupW} height={PLOT_H}
                  fill={hover === i ? 'var(--bg-hover)' : 'transparent'} />
                {series.map((s, si) => {
                  const v = s.values[i];
                  if (v === null) return null;
                  if (stacked) {
                    const h = Math.max(v, 0);
                    const rect = <rect key={s.name} x={gx} y={yFor(base + h)} width={barW} height={yFor(base) - yFor(base + h)} fill={s.color} />;
                    base += h;
                    return rect;
                  }
                  const top = yFor(Math.max(v, 0));
                  return <rect key={s.name} x={gx + si * barW + 1} y={top} width={Math.max(barW - 2, 1)}
                    height={Math.abs(yFor(v) - yFor(0))} fill={s.color} rx="1" />;
                })}
                <text x={PAD.left + (i + 0.5) * groupW} y={H - PAD.bottom + 15} style={{ fill: 'var(--text-muted)' }}
                  fontSize="10" textAnchor="middle">{l}</text>
              </g>
            );
          })}
        </svg>
        {hover !== null && (
          <Tooltip xPct={((PAD.left + (hover + 0.5) * groupW) / W) * 100} title={labels[hover]}
            rows={series.map((s) => ({ name: s.name, color: s.color, text: fmt(s.values[hover], unit) }))} />
        )}
      </div>
      <Legend items={series} />
    </>
  );
}

// Horizontal bars, sorted largest first, with the value printed at the end of each bar.
function HBarsView({ items, unit }) {
  const sorted = [...items].filter((it) => it.value !== null).sort((a, b) => b.value - a.value);
  if (sorted.length === 0) return <p className="chart-empty">No values to plot.</p>;
  const left = 70;
  const right = 64;
  const lo = Math.min(0, ...sorted.map((it) => it.value));
  const hi = Math.max(0, ...sorted.map((it) => it.value));
  const xFor = (v) => left + ((v - lo) * (W - left - right)) / (hi - lo || 1);
  const rowH = PLOT_H / sorted.length;
  return (
    <div className="chart-frame">
      <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" preserveAspectRatio="none">
        <line x1={xFor(0)} y1={PAD.top} x2={xFor(0)} y2={PAD.top + PLOT_H} style={{ stroke: 'var(--border-strong)' }} strokeWidth="1" />
        {sorted.map((it, i) => {
          const y = PAD.top + i * rowH + rowH * 0.2;
          const h = rowH * 0.6;
          const x0 = xFor(Math.min(it.value, 0));
          const x1 = xFor(Math.max(it.value, 0));
          return (
            <g key={it.name}>
              <text x={left - 8} y={y + h / 2 + 3} style={{ fill: 'var(--text-secondary)' }} fontSize="10" textAnchor="end">{it.name}</text>
              <rect x={x0} y={y} width={Math.max(x1 - x0, 1)} height={h} fill={it.color} opacity={i === 0 ? 1 : 0.55} rx="1" />
              <text x={x1 + 6} y={y + h / 2 + 3} style={{ fill: 'var(--text-primary)' }} fontSize="10" fontWeight={i === 0 ? 600 : 400}>
                {fmt(it.value, unit)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function ScatterView({ points, xUnit, yUnit, xLabel, yLabel }) {
  if (points.length === 0) return <p className="chart-empty">No values to plot.</p>;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xt = niceTicks(Math.min(0, ...xs), Math.max(...xs));
  const yt = niceTicks(Math.min(0, ...ys), Math.max(...ys));
  const xFor = (v) => PAD.left + ((v - xt[0]) * PLOT_W) / (xt[xt.length - 1] - xt[0] || 1);
  const yFor = (v) => PAD.top + PLOT_H - ((v - yt[0]) * PLOT_H) / (yt[yt.length - 1] - yt[0] || 1);
  return (
    <>
      <div className="chart-frame">
        <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" preserveAspectRatio="none">
          <YAxis ticks={yt} yFor={yFor} unit={yUnit} />
          {xt.map((t) => (
            <text key={t} x={xFor(t)} y={H - PAD.bottom + 15} style={{ fill: 'var(--text-muted)' }} fontSize="9" textAnchor="middle">{fmt(t, xUnit)}</text>
          ))}
          {points.map((p) => (
            <g key={p.name}>
              <circle cx={xFor(p.x)} cy={yFor(p.y)} r="4.5" fill={p.color} style={{ stroke: 'var(--bg-surface)' }} strokeWidth="1.5">
                <title>{`${p.name}: ${fmt(p.x, xUnit)}, ${fmt(p.y, yUnit)}`}</title>
              </circle>
              <text x={xFor(p.x) + 7} y={yFor(p.y) + 3} style={{ fill: 'var(--text-secondary)' }} fontSize="10">{p.name}</text>
            </g>
          ))}
        </svg>
      </div>
      <div className="chart-axis-note">x: {xLabel} · y: {yLabel}</div>
    </>
  );
}

function ThemeGrid({ matrix }) {
  const max = Math.max(1, ...matrix.rows.flatMap((r) => Object.values(r.counts)));
  return (
    <div className="theme-grid-wrap">
      <table className="theme-grid">
        <thead>
          <tr>
            <th>Theme</th>
            {matrix.companies.map((c) => <th key={c}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {matrix.rows.map((r) => (
            <tr key={r.title}>
              <td>{r.title}</td>
              {matrix.companies.map((c) => {
                const n = r.counts[c] || 0;
                return (
                  <td key={c} className={n ? '' : 'is-empty'}
                    style={n ? { background: `color-mix(in srgb, var(--accent) ${Math.round(12 + (n / max) * 38)}%, transparent)` } : undefined}>
                    {n || '—'}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const FORM_COLORS = { '10-K': '#1d4ed8', '10-Q': '#0a7a4c', '8-K': '#b45309' };

function SourcesTimeline({ items }) {
  const companies = [...new Set(items.map((s) => s.meta.company))];
  const times = items.map((s) => Date.parse(s.meta.filing_date));
  let lo = Math.min(...times);
  let hi = Math.max(...times);
  if (lo === hi) {
    lo -= 864e5 * 30;
    hi += 864e5 * 30;
  }
  const left = 60;
  const xFor = (t) => left + ((t - lo) * (W - left - PAD.right)) / (hi - lo);
  const rowH = PLOT_H / companies.length;
  const yFor = (c) => PAD.top + (companies.indexOf(c) + 0.5) * rowH;
  const forms = [...new Set(items.map((s) => s.meta.filing_type))];
  // Several citations from one filing share a dot; the label lists their numbers.
  const dots = {};
  items.forEach((s) => {
    const key = `${s.meta.company}|${s.meta.filing_date}|${s.meta.filing_type}`;
    (dots[key] ||= { ...s, ns: [] }).ns.push(s.n);
  });
  const yearTicks = [];
  for (let y = new Date(lo).getFullYear(); y <= new Date(hi).getFullYear() + 1; y += 1) {
    const t = Date.parse(`${y}-01-01`);
    if (t >= lo && t <= hi) yearTicks.push({ y, t });
  }
  return (
    <>
      <div className="chart-frame">
        <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" preserveAspectRatio="none">
          {companies.map((c) => (
            <g key={c}>
              <line x1={left} y1={yFor(c)} x2={W - PAD.right} y2={yFor(c)} style={{ stroke: 'var(--border)' }} strokeWidth="1" />
              <text x={left - 8} y={yFor(c) + 3} style={{ fill: 'var(--text-secondary)' }} fontSize="10" textAnchor="end">{c}</text>
            </g>
          ))}
          {yearTicks.map(({ y, t }) => (
            <g key={y}>
              <line x1={xFor(t)} y1={PAD.top} x2={xFor(t)} y2={PAD.top + PLOT_H} style={{ stroke: 'var(--border)' }} strokeDasharray="2 3" />
              <text x={xFor(t)} y={H - PAD.bottom + 15} style={{ fill: 'var(--text-muted)' }} fontSize="10" textAnchor="middle">{y}</text>
            </g>
          ))}
          {/* Axis ends as year-month, so a span of a few months is still readable. */}
          {[lo, hi].map((t, i) => (
            <text key={t} x={xFor(t)} y={H - 2} style={{ fill: 'var(--text-muted)' }} fontSize="9"
              textAnchor={i === 0 ? 'start' : 'end'}>
              {new Date(t).toISOString().slice(0, 7)}
            </text>
          ))}
          {Object.values(dots).map((d) => {
            const x = xFor(Date.parse(d.meta.filing_date));
            const y = yFor(d.meta.company);
            return (
              <g key={`${d.meta.company}${d.meta.filing_date}${d.meta.filing_type}`}>
                <circle cx={x} cy={y} r="5" fill={FORM_COLORS[d.meta.filing_type] || '#525252'} style={{ stroke: 'var(--bg-surface)' }} strokeWidth="1.5">
                  <title>{`${d.meta.company} ${d.meta.filing_type}, filed ${d.meta.filing_date}`}</title>
                </circle>
                <text x={x} y={y - 9} style={{ fill: 'var(--text-muted)' }} fontSize="9" textAnchor="middle">[{d.ns.join(',')}]</text>
              </g>
            );
          })}
        </svg>
      </div>
      <Legend items={forms.map((f) => ({ name: f, color: FORM_COLORS[f] || '#525252' }))} />
    </>
  );
}

// ------------------------------------------------------------------ panel

const TITLES = {
  indexed: ['Growth index', 'Each line starts at 100 in its first year with a positive value, so growth compares across sizes.'],
  yoy: ['Year-over-year change', 'Bars below zero are declines.'],
  margin: ['Net margin', 'Net income as a share of revenue.'],
  composition: ['Liabilities and equity', 'Stacked to total assets; equity = assets − liabilities.'],
  ranking: ['Ranking', 'Latest year every company reports.'],
  growth: ['Compound annual growth', 'Over the years every company reports.'],
  scatter: ['Size vs growth', 'Latest common year against growth over the common window.'],
  themes: ['Cited passages by theme', 'How many filing passages each theme cites per company. "—" means none were cited.'],
  sources: ['Filings cited', 'Each dot is a filing; the numbers are the citations drawn from it.'],
};

export function ChartPanel({ response, charts, renderTrend, palette, metricColors }) {
  const meta = response.chart_meta || {};
  const ds = meta.dataset;
  const suggested = charts.includes(meta.suggested) ? meta.suggested : charts[0];
  const [chosen, setChosen] = useState(null);
  const active = chosen && charts.includes(chosen) ? chosen : suggested;

  const metricOptions = ds ? Object.keys(ds.metrics).filter((m) => hasMetric(ds, m)) : [];
  const defaultMetric = (meta.metric_keys || []).find((m) => metricOptions.includes(m)) || metricOptions[0];
  const [metricChoice, setMetricChoice] = useState(null);
  const metric = metricOptions.includes(metricChoice) ? metricChoice : defaultMetric;

  const multi = ds && ds.companies.length >= 2;
  const companyColor = (t) => palette[ds.companies.indexOf(t) % palette.length];
  // Single company: one series per selected metric. Comparison: one series per company.
  const singleMetrics = (meta.metric_keys || []).filter((m) => metricOptions.includes(m));
  const usesMetricPicker = ds && ((multi && ['indexed', 'yoy'].includes(active)) || ['ranking', 'growth', 'scatter'].includes(active));

  const seriesFor = (fn) => (multi
    ? ds.companies.map((t) => ({ name: t, color: companyColor(t), values: fn(t, metric) }))
    : (singleMetrics.length ? singleMetrics : [metric]).map((m) => ({
      name: ds.metrics[m].label, color: metricColors[ds.metrics[m].label] || palette[0], values: fn(ds.companies[0], m),
    })));

  let body = null;
  let title = TITLES[active];
  if (active === 'trend') {
    body = renderTrend();
    title = null;
  } else if (active === 'indexed') {
    const series = seriesFor((t, m) => {
      const raw = ds.years.map((y) => val(ds, t, m, y));
      const base = raw.find((v) => v !== null && v > 0);
      return raw.map((v) => (v === null || !base ? null : (v / base) * 100));
    });
    body = <LineView labels={ds.years.map(String)} series={series} unit="idx" reference={100} />;
  } else if (active === 'yoy') {
    const years = ds.years.slice(1);
    const series = seriesFor((t, m) => years.map((y) => {
      const a = val(ds, t, m, y - 1);
      const b = val(ds, t, m, y);
      return a !== null && b !== null && a > 0 ? b / a - 1 : null;
    }));
    body = <BarsView labels={years.map(String)} series={series.slice(0, 6)} unit="%" />;
  } else if (active === 'margin') {
    const series = ds.companies.map((t, i) => ({
      name: multi ? t : 'Net margin',
      color: multi ? companyColor(t) : palette[i],
      values: ds.years.map((y) => {
        const r = val(ds, t, 'revenue', y);
        const n = val(ds, t, 'net_income', y);
        return r && n !== null ? n / r : null;
      }),
    }));
    body = <LineView labels={ds.years.map(String)} series={series} unit="%" />;
  } else if (active === 'composition') {
    const stacks = (getVals) => [
      { name: 'Liabilities', color: metricColors.Liabilities, values: getVals((a, l) => l) },
      { name: 'Equity', color: metricColors.Assets, values: getVals((a, l) => a - l) },
    ];
    if (multi) {
      const yrs = commonYears(ds, 'assets').filter((y) => commonYears(ds, 'liabilities').includes(y));
      const yr = yrs[yrs.length - 1];
      body = yr ? (
        <BarsView stacked labels={ds.companies} unit="$B"
          series={stacks((f) => ds.companies.map((t) => f(val(ds, t, 'assets', yr), val(ds, t, 'liabilities', yr))))} />
      ) : <p className="chart-empty">No year with assets and liabilities for every company.</p>;
      title = [`Liabilities and equity, ${yr ?? ''}`, TITLES.composition[1]];
    } else {
      const t = ds.companies[0];
      body = (
        <BarsView stacked labels={ds.years.map(String)} unit="$B"
          series={stacks((f) => ds.years.map((y) => {
            const a = val(ds, t, 'assets', y);
            const l = val(ds, t, 'liabilities', y);
            return a === null || l === null ? null : f(a, l);
          }))} />
      );
    }
  } else if (active === 'ranking') {
    const yrs = commonYears(ds, metric);
    const yr = yrs[yrs.length - 1];
    body = yr ? (
      <HBarsView unit={ds.metrics[metric].unit}
        items={ds.companies.map((t) => ({ name: t, value: val(ds, t, metric, yr), color: companyColor(t) }))} />
    ) : <p className="chart-empty">No year where every company reports {ds.metrics[metric].label}.</p>;
    title = [`${ds.metrics[metric].label} in ${yr ?? ''}, largest first`, TITLES.ranking[1]];
  } else if (active === 'growth' || active === 'scatter') {
    const yrs = commonYears(ds, metric);
    const first = yrs[0];
    const last = yrs[yrs.length - 1];
    const growth = (t) => (yrs.length >= 2 ? cagr(val(ds, t, metric, first), val(ds, t, metric, last), last - first) : null);
    const label = ds.metrics[metric].label;
    if (yrs.length < 2) {
      body = <p className="chart-empty">Fewer than two years where every company reports {label}.</p>;
    } else if (active === 'growth') {
      body = <HBarsView unit="%" items={ds.companies.map((t) => ({ name: t, value: growth(t), color: companyColor(t) }))} />;
      title = [`${label} CAGR ${first}–${last}`, 'Fastest first. Companies with a non-positive start or end value are left out.'];
    } else {
      const points = ds.companies
        .map((t) => ({ name: t, x: val(ds, t, metric, last), y: growth(t), color: companyColor(t) }))
        .filter((p) => p.x !== null && p.y !== null);
      body = <ScatterView points={points} xUnit={ds.metrics[metric].unit} yUnit="%"
        xLabel={`${label} ${last}`} yLabel={`${label} CAGR ${first}–${last}`} />;
    }
  } else if (active === 'themes') {
    body = <ThemeGrid matrix={themeMatrix(response)} />;
  } else if (active === 'sources') {
    body = <SourcesTimeline items={filingSources(response)} />;
  }

  return (
    <div className="chart-panel-inner">
      {charts.length > 1 && (
        <div className="chart-switcher" role="tablist" aria-label="Chart type">
          {charts.map((c) => (
            <button key={c} type="button" role="tab" aria-selected={c === active}
              className={`chart-tab${c === active ? ' is-active' : ''}`} onClick={() => setChosen(c)}>
              {CHART_LABELS[c]}
              {c === suggested && <span className="chart-tab-pick" title="Suggested for this question"></span>}
            </button>
          ))}
        </div>
      )}
      {title && (
        <div className="panel-title">
          <div>
            <h3>{title[0]}</h3>
            <div className="sub">{title[1]}</div>
          </div>
          {usesMetricPicker && metricOptions.length > 1 && (
            <select className="chart-metric" value={metric} onChange={(e) => setMetricChoice(e.target.value)} aria-label="Metric">
              {metricOptions.map((m) => <option key={m} value={m}>{ds.metrics[m].label}</option>)}
            </select>
          )}
        </div>
      )}
      {body}
    </div>
  );
}
