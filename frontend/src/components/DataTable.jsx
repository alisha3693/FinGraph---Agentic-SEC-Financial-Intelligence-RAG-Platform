import React, { useState, useMemo } from 'react';
import {
  ArrowUpDown, ArrowUp, ArrowDown, Download, Table2,
} from 'lucide-react';
import { formatCell, toCsv, downloadCsv } from '../lib/format';

// Renders the agent's structured numeric result as a real, sortable table — separate from
// the narrative text — so the underlying data can actually be inspected, sorted, and
// exported instead of being buried inside a wall of prose.
export function DataTable({ columns, rows, ticker }) {
  const [sort, setSort] = useState(null); // { key, dir: 'asc' | 'desc' }

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (av === bv) return 0;
      const cmp = av > bv ? 1 : -1;
      return sort.dir === 'asc' ? cmp : -cmp;
    });
    return copy;
  }, [rows, sort]);

  const toggleSort = (key) => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: 'asc' };
      if (prev.dir === 'asc') return { key, dir: 'desc' };
      return null;
    });
  };

  if (!columns || columns.length === 0 || !rows || rows.length === 0) return null;

  return (
    <div className="data-table-wrap">
      <div className="data-table-toolbar">
        <span className="data-table-title"><Table2 /> Structured Data</span>
        <button
          type="button"
          className="export-btn"
          onClick={() => downloadCsv(`${ticker || 'sec-data'}.csv`, toCsv(columns, rows))}
        >
          <Download style={{ width: '12px', height: '12px' }} /> Export CSV
        </button>
      </div>
      <div className="data-table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((col) => {
                const active = sort?.key === col.key;
                const Icon = active ? (sort.dir === 'asc' ? ArrowUp : ArrowDown) : ArrowUpDown;
                return (
                  <th key={col.key} onClick={() => toggleSort(col.key)} className={active ? 'sorted' : ''}>
                    <span>{col.label}</span>
                    <Icon style={{ width: '11px', height: '11px' }} />
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row, i) => (
              <tr key={i}>
                {columns.map((col) => (
                  <td key={col.key}>{formatCell(col, row[col.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
