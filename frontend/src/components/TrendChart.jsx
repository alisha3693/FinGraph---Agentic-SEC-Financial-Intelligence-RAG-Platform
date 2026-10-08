import React, { useState } from 'react';
import { METRIC_COLORS, COMPARISON_PALETTE } from '../lib/format';

// Dependency-free analytical line chart: minimal gridlines, tabular axis labels, and a
// hover crosshair with a compact tooltip — no decorative borders or chart chrome.
export function TrendChart({ chartData, chartMeta }) {
  const [hoverIdx, setHoverIdx] = useState(null);

  if (!chartData || chartData.length === 0) return null;

  const isComparison = chartMeta?.type === 'comparison';
  const series = chartMeta?.series || [];
  const metricLabels = chartMeta?.metrics || [];

  const years = chartData.map((d) => d.name);
  const width = 460;
  const height = 240;
  const padding = { top: 14, right: 14, bottom: 26, left: 42 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  let maxVal = 0;
  chartData.forEach((d) => {
    Object.keys(d).forEach((k) => {
      if (k !== 'name' && typeof d[k] === 'number' && d[k] > maxVal) maxVal = d[k];
    });
  });
  maxVal = maxVal ? maxVal * 1.15 : 100;

  const keys = Object.keys(chartData[0]).filter((k) => k !== 'name');
  const xFor = (idx) => padding.left + (idx * plotW) / (chartData.length - 1 || 1);
  const yFor = (val) => padding.top + plotH - ((val || 0) * plotH) / maxVal;

  const points = {};
  keys.forEach((key) => {
    // A missing value (null) is a gap, not zero: no point is drawn, and the line breaks there.
    points[key] = chartData.map((d, idx) => ({ x: xFor(idx), y: typeof d[key] === 'number' ? yFor(d[key]) : null }));
  });

  // Comparison mode has ticker-specific keys that vary per query, so colors are assigned
  // by position from a shared palette instead of guessing at specific ticker strings.
  const colorFor = (key, idx) =>
    (!isComparison && METRIC_COLORS[key]) || COMPARISON_PALETTE[idx % COMPARISON_PALETTE.length];

  const metricsText = metricLabels.length > 0 ? metricLabels.join(', ') : 'Financial Trends';
  const headerText = isComparison
    ? `${series.join(' vs ')} — ${metricsText}`
    : `${series[0] ? series[0] + ' ' : ''}${metricsText} Trend`;

  // Hover zones centered on each point (not raw equal slices), so the crosshair snaps to
  // whichever year the cursor is actually closest to.
  const hoverZones = years.map((_, idx) => {
    const x = xFor(idx);
    const start = idx === 0 ? padding.left : (xFor(idx - 1) + x) / 2;
    const end = idx === years.length - 1 ? width - padding.right : (x + xFor(idx + 1)) / 2;
    return { start, width: end - start };
  });

  return (
    <div className="chart-panel-inner">
      <div className="panel-title">
        <div>
          <h3>{headerText}</h3>
        </div>
      </div>

      <div className="chart-frame">
        <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg" preserveAspectRatio="none">
          {[0, 0.25, 0.5, 0.75, 1].map((ratio, idx) => {
            const y = padding.top + ratio * plotH;
            const val = (maxVal * (1 - ratio)).toFixed(0);
            return (
              <g key={idx}>
                <line x1={padding.left} y1={y} x2={width - padding.right} y2={y} style={{ stroke: 'var(--border)' }} strokeWidth="1" />
                <text x={padding.left - 8} y={y + 3} style={{ fill: 'var(--text-muted)' }} fontSize="9" textAnchor="end">{val}</text>
              </g>
            );
          })}

          <line
            x1={padding.left} y1={height - padding.bottom}
            x2={width - padding.right} y2={height - padding.bottom}
            style={{ stroke: 'var(--border-strong)' }} strokeWidth="1"
          />

          {years.map((yr, idx) => (
            <text key={idx} x={xFor(idx)} y={height - padding.bottom + 15} style={{ fill: 'var(--text-muted)' }} fontSize="10" textAnchor="middle">{yr}</text>
          ))}

          {hoverIdx !== null && (
            <line
              x1={xFor(hoverIdx)} y1={padding.top} x2={xFor(hoverIdx)} y2={height - padding.bottom}
              style={{ stroke: 'var(--border-strong)' }} strokeWidth="1" strokeDasharray="2 3"
            />
          )}

          {keys.map((key, idx) => {
            const linePoints = points[key];
            // Start a new segment after each gap, so a missing year breaks the line.
            const pathD = linePoints
              .map((p, i) => (p.y === null ? null : `${i === 0 || linePoints[i - 1].y === null ? 'M' : 'L'} ${p.x} ${p.y}`))
              .filter(Boolean)
              .join(' ');
            const color = colorFor(key, idx);
            return (
              <g key={key}>
                <path d={pathD} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                {linePoints.map((p, i) => (p.y === null ? null : (
                  <circle key={i} cx={p.x} cy={p.y} r={hoverIdx === i ? 3.5 : 2.5} fill={color} style={{ stroke: 'var(--bg-surface)' }} strokeWidth="1" />
                )))}
              </g>
            );
          })}

          {hoverZones.map((zone, idx) => (
            <rect
              key={idx}
              x={zone.start}
              y={padding.top}
              width={Math.max(zone.width, 0)}
              height={plotH}
              fill="transparent"
              onMouseEnter={() => setHoverIdx(idx)}
              onMouseLeave={() => setHoverIdx(null)}
            />
          ))}
        </svg>

        {hoverIdx !== null && (
          <div className="chart-tooltip" style={{ left: `${(xFor(hoverIdx) / width) * 100}%` }}>
            <div className="tooltip-year">{years[hoverIdx]}</div>
            {keys.map((key, idx) => {
              const val = chartData[hoverIdx][key];
              return (
                <div key={key} className="tooltip-row">
                  <span className="legend-dot" style={{ backgroundColor: colorFor(key, idx) }}></span>
                  <span>{key}: {typeof val === 'number' ? val.toFixed(2) : (val ?? '—')}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="chart-legend">
        {keys.map((key, idx) => (
          <div key={key} className="legend-item">
            <span className="legend-dot" style={{ backgroundColor: colorFor(key, idx) }}></span>
            <span>{key}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Answer view
// The backend sends `structured` (summary, verdict, titled sections, figure tiles) next to
// the plain `answer` text. Responses without it render exactly as before (LegacyAnswer).
