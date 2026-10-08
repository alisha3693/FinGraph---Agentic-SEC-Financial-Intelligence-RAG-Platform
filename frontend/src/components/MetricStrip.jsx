import React from 'react';
import {
  ArrowUpRight, ArrowDownRight,
} from 'lucide-react';
import { formatCell } from '../lib/format';

// Latest-year KPI row (Revenue, Net Income, EPS, Assets, Liabilities) with a year-over-year
// delta, derived entirely from the same rows the table/chart already use.
export function MetricStrip({ columns, rows }) {
  if (!columns || columns.length === 0 || !rows || rows.length === 0) return null;

  const metricCols = columns.filter((c) => c.key !== 'year');
  const latest = rows[rows.length - 1];
  const prior = rows.length > 1 ? rows[rows.length - 2] : null;

  return (
    <div className="metric-strip">
      {/* Every figure is from the same year, so it's shown once instead of in each label. */}
      <div className="metric metric-year" title="Fiscal year of these figures">
        <span className="metric-value">FY{latest.year}</span>
      </div>
      {metricCols.map((col) => {
        const latestVal = latest[col.key];
        const priorVal = prior ? prior[col.key] : null;
        let deltaPct = null;
        if (typeof latestVal === 'number' && typeof priorVal === 'number' && priorVal !== 0) {
          deltaPct = ((latestVal - priorVal) / Math.abs(priorVal)) * 100;
        }
        const dir = deltaPct === null ? 'flat' : deltaPct > 0.05 ? 'up' : deltaPct < -0.05 ? 'down' : 'flat';
        const DeltaIcon = dir === 'up' ? ArrowUpRight : dir === 'down' ? ArrowDownRight : null;

        return (
          <div key={col.key} className="metric"
            title={prior ? `FY${latest.year} vs FY${prior.year}` : `FY${latest.year}`}>
            <div className="metric-label">{col.label}</div>
            <div className="metric-value-row">
              <span className="metric-value">{formatCell(col, latestVal)}</span>
              {deltaPct !== null && (
                <span className={`metric-delta ${dir}`}>
                  {DeltaIcon && <DeltaIcon />}
                  {Math.abs(deltaPct).toFixed(1)}%
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
