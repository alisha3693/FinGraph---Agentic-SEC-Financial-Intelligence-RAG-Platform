import React, { useState, useEffect, useMemo } from 'react';
import {
  Send, BookOpen, ShieldAlert, Plus, X, CheckCircle,
  ArrowUpDown, ArrowUp, ArrowDown, ArrowUpRight, ArrowDownRight,
  Download, Table2, BarChart3, LineChart, Building2, FileText,
  Sun, Moon,
} from 'lucide-react';

const THEME_KEY = 'sec-intel-theme';

function getInitialTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {
    // localStorage unavailable (private mode, disabled storage) — fall through to OS setting.
  }
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const METRIC_COLORS = { "Revenue": "#1d4ed8", "Net Income": "#0a7a4c", "EPS": "#b45309", "Assets": "#4338ca", "Liabilities": "#b42318" };
const COMPARISON_PALETTE = ["#1d4ed8", "#0a7a4c", "#b45309", "#4338ca", "#b42318", "#0e7490", "#7c3aed", "#525252"];

// Matches currency amounts ("$391.04B", "$6.11") and percentages ("12.5%") so they can be
// set off from surrounding prose without touching any other text.
const FIGURE_RE = /(\$\d[\d,]*\.?\d*(?:\s?(?:B|M|K|billion|million))?|\b\d+(?:\.\d+)?%)/i;

function renderFigureLine(line) {
  return line.split(FIGURE_RE).map((part, i) =>
    i % 2 === 1
      ? <span key={i} className="figure">{part}</span>
      : <React.Fragment key={i}>{part}</React.Fragment>
  );
}

function formatCell(column, value) {
  if (value === null || value === undefined) return '—';
  if (typeof value !== 'number') return value;
  if (column.unit === '$B') return `$${value.toFixed(2)}B`;
  if (column.unit === '$') return `$${value.toFixed(2)}`;
  return value;
}

function toCsv(columns, rows) {
  const escape = (v) => {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = columns.map((c) => escape(c.label)).join(',');
  const lines = rows.map((row) => columns.map((c) => escape(row[c.key])).join(','));
  return [header, ...lines].join('\n');
}

function downloadCsv(filename, csvText) {
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

// Renders the agent's structured numeric result as a real, sortable table — separate from
// the narrative text — so the underlying data can actually be inspected, sorted, and
// exported instead of being buried inside a wall of prose.
function DataTable({ columns, rows, ticker }) {
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

// Latest-year KPI row (Revenue, Net Income, EPS, Assets, Liabilities) with a year-over-year
// delta, derived entirely from the same rows the table/chart already use.
function MetricStrip({ columns, rows }) {
  if (!columns || columns.length === 0 || !rows || rows.length === 0) return null;

  const metricCols = columns.filter((c) => c.key !== 'year');
  const latest = rows[rows.length - 1];
  const prior = rows.length > 1 ? rows[rows.length - 2] : null;

  return (
    <div className="metric-strip">
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
          <div key={col.key} className="metric">
            <div className="metric-label">{col.label} · FY{latest.year}</div>
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

// Dependency-free analytical line chart: minimal gridlines, tabular axis labels, and a
// hover crosshair with a compact tooltip — no decorative borders or chart chrome.
function TrendChart({ chartData, chartMeta }) {
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
    points[key] = chartData.map((d, idx) => ({ x: xFor(idx), y: yFor(d[key]) }));
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
            const pathD = linePoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
            const color = colorFor(key, idx);
            return (
              <g key={key}>
                <path d={pathD} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                {linePoints.map((p, i) => (
                  <circle key={i} cx={p.x} cy={p.y} r={hoverIdx === i ? 3.5 : 2.5} fill={color} style={{ stroke: 'var(--bg-surface)' }} strokeWidth="1" />
                ))}
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

function App() {
  const [prompt, setPrompt] = useState('');
  const [submittedPrompt, setSubmittedPrompt] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [currentResponse, setCurrentResponse] = useState(null);
  const [error, setError] = useState(null);

  // App states
  const [companies, setCompanies] = useState([]);
  const [selectedTicker, setSelectedTicker] = useState('');
  const [newTicker, setNewTicker] = useState('');
  const [loadingTicker, setLoadingTicker] = useState(false);
  const [tickerMessage, setTickerMessage] = useState({ text: '', isError: false });
  const [defaultFinancials, setDefaultFinancials] = useState(null);
  const [theme, setTheme] = useState(getInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // Ignore — theme just won't persist across reloads in this environment.
    }
  }, [theme]);

  const sampleQuestions = [
    "What was Apple's revenue from 2021–2025?",
    "Compare Apple and Microsoft revenue.",
    "What risks did Apple mention in its latest 10-K?",
    "What products/strategies are discussed in the filing?"
  ];

  const selectedCompany = companies.find((co) => co.ticker === selectedTicker) || null;
  const companyFactsUrl = selectedCompany ? `https://data.sec.gov/api/xbrl/companyfacts/CIK${selectedCompany.cik}.json` : null;

  // Fetch registered companies on load — DB is source of truth, no placeholder data
  const fetchCompanies = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/companies`);
      if (response.ok) {
        const data = await response.json();
        setCompanies(data);
        // Auto-scope to the first ingested company so the default table/graph has
        // something to show without requiring the user to click a chip first.
        setSelectedTicker((prev) => prev || (data.length > 0 ? data[0].ticker : ''));
      }
    } catch (err) {
      console.error("Error loading companies list:", err);
    }
  };

  useEffect(() => {
    fetchCompanies();
  }, []);

  // Default overview (all metrics: Revenue, Net Income, EPS, Assets, Liabilities) for
  // whichever ticker is scoped, shown before any question has been asked.
  useEffect(() => {
    if (!selectedTicker) {
      setDefaultFinancials(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`${API_BASE}/api/financials/${selectedTicker}`);
        if (response.ok) {
          const data = await response.json();
          if (!cancelled) setDefaultFinancials(data);
        }
      } catch (err) {
        console.error("Error loading default financials:", err);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedTicker]);

  const handleSubmit = async (e, customPrompt = null) => {
    if (e) e.preventDefault();
    const activePrompt = (customPrompt ?? prompt).trim();
    if (!activePrompt || isLoading) return;

    setIsLoading(true);
    setError(null);
    setCurrentResponse(null);
    setSubmittedPrompt(activePrompt);

    try {
      const response = await fetch(`${API_BASE}/api/query`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ prompt: activePrompt, ticker: selectedTicker || null }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to analyze query');
      }

      const data = await response.json();
      setCurrentResponse(data);
      setPrompt('');
    } catch (err) {
      console.error(err);
      setError(err.message || 'FastAPI Server connection failed. Run uvicorn inside multihop-rag.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteCompany = async (e, ticker) => {
    e.stopPropagation();
    if (!window.confirm(`Remove ${ticker} and its cached filings?`)) return;

    try {
      const response = await fetch(`${API_BASE}/api/companies/${ticker}`, {
        method: 'DELETE',
      });
      if (response.ok) {
        setCompanies((prev) => prev.filter((co) => co.ticker !== ticker));
        setSelectedTicker((prev) => (prev === ticker ? '' : prev));
      } else {
        const data = await response.json().catch(() => ({}));
        setTickerMessage({ text: data.detail || `Failed to remove ${ticker}.`, isError: true });
      }
    } catch (err) {
      setTickerMessage({ text: 'Error connecting to database loader.', isError: true });
    }
  };

  const handleLoadTicker = async (e) => {
    e.preventDefault();
    if (!newTicker.trim() || loadingTicker) return;

    setLoadingTicker(true);
    setTickerMessage({ text: '', isError: false });
    const targetTicker = newTicker.trim().toUpperCase();
    try {
      const response = await fetch(`${API_BASE}/api/load_ticker`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ticker: targetTicker }),
      });

      const data = await response.json();
      if (response.ok && data.success) {
        setTickerMessage({ text: data.message, isError: false });
        setNewTicker('');
        fetchCompanies();
        setSelectedTicker(targetTicker);
      } else {
        setTickerMessage({ text: data.message || 'Filing ingestion failed.', isError: true });
      }
    } catch (err) {
      setTickerMessage({ text: 'Error connecting to database loader.', isError: true });
    } finally {
      setLoadingTicker(false);
    }
  };

  const showDefaultChart = !currentResponse?.chart_data?.length && defaultFinancials?.chart_data?.length > 0;
  const showQueryChart = currentResponse?.chart_data?.length > 0;
  const showDefaultTable = !currentResponse && defaultFinancials?.table?.length > 0;

  return (
    <>
      <nav className="navbar">
        <div className="brand">
          <div className="brand-mark"><BarChart3 /></div>
          <div className="brand-name">FinGraph <span>Terminal</span></div>
        </div>
        <div className="navbar-status">
          <div className="status-item"><span className="status-dot"></span>EDGAR Linked</div>
          <div className="status-item"><span className="status-dot"></span>Agent Active</div>
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'dark' ? <Sun /> : <Moon />}
          </button>
        </div>
      </nav>

      <div className="layout">
        {/* Sidebar */}
        <aside className="sidebar">
          <div className="sidebar-block">
            <span className="sidebar-label">Ingest Ticker</span>
            <form onSubmit={handleLoadTicker} className="ticker-form">
              <input
                type="text"
                value={newTicker}
                onChange={(e) => setNewTicker(e.target.value)}
                placeholder="Ticker (e.g. AMZN, TSLA)"
                disabled={loadingTicker}
                className="field-input"
              />
              <button type="submit" disabled={loadingTicker || !newTicker.trim()} className="icon-btn">
                {loadingTicker ? <div className="mini-spinner"></div> : <Plus style={{ width: '16px', height: '16px' }} />}
              </button>
            </form>
            {loadingTicker && (
              <p className="status-msg pending">Downloading SEC filings and indexing them — usually 15–30s for large companies.</p>
            )}
            {tickerMessage.text && (
              <p className={`status-msg ${tickerMessage.isError ? 'err' : 'ok'}`}>
                {tickerMessage.isError ? <ShieldAlert style={{ width: '12px', height: '12px' }} /> : <CheckCircle style={{ width: '12px', height: '12px' }} />}
                {tickerMessage.text}
              </p>
            )}
          </div>

          <div className="sidebar-block">
            <span className="sidebar-label">Watchlist</span>
            {companies.length > 0 && (
              <p className="sidebar-hint">Select a company to scope your next question to it.</p>
            )}
            {companies.length === 0 ? (
              <div className="empty-state-inline">
                <p>No companies ingested yet. Add a ticker above to get started.</p>
              </div>
            ) : (
              <div className="watchlist">
                {companies.map((co) => (
                  <button
                    key={co.ticker}
                    onClick={() => setSelectedTicker((prev) => (prev === co.ticker ? '' : co.ticker))}
                    className={`watchlist-item ${selectedTicker === co.ticker ? 'active' : ''}`}
                  >
                    <div className="watchlist-details">
                      <span className="watchlist-ticker">{co.ticker}</span>
                      <span className="watchlist-name">{co.name}</span>
                    </div>
                    <span
                      role="button"
                      aria-label={`Remove ${co.ticker}`}
                      className="watchlist-remove"
                      onClick={(e) => handleDeleteCompany(e, co.ticker)}
                    >
                      <X style={{ width: '13px', height: '13px' }} />
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="sidebar-block grow">
            <span className="sidebar-label">Analysis Templates</span>
            <div className="template-list">
              {sampleQuestions.map((q, idx) => (
                <button
                  key={idx}
                  className="template-item"
                  onClick={() => handleSubmit(null, q)}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>

          <div className="sidebar-block">
            <div className="sidebar-footer">
              <BookOpen style={{ width: '14px', height: '14px' }} />
              <span>Scope: SEC 10-K &amp; XBRL Database</span>
            </div>
          </div>
        </aside>

        {/* Main workspace */}
        <main className="main">
          <div className="context-bar">
            <div className="context-bar-left">
              {selectedTicker ? (
                <>
                  <span className="context-ticker">{selectedTicker}</span>
                  <span className="context-name">{selectedCompany?.name || ''}</span>
                </>
              ) : (
                <span className="context-name">No company selected — choose one from the watchlist.</span>
              )}
            </div>
            {companyFactsUrl && (
              <a href={companyFactsUrl} target="_blank" rel="noreferrer" className="context-source">
                <FileText style={{ width: '12px', height: '12px' }} />
                <span>Company facts (SEC EDGAR)</span>
              </a>
            )}
          </div>

          {!currentResponse && defaultFinancials?.table?.length > 0 && (
            <MetricStrip columns={defaultFinancials.table_columns} rows={defaultFinancials.table} />
          )}

          <div className="workspace">
            <div className="chart-column">
              {showQueryChart ? (
                <TrendChart chartData={currentResponse.chart_data} chartMeta={currentResponse.chart_meta} />
              ) : showDefaultChart ? (
                <TrendChart chartData={defaultFinancials.chart_data} chartMeta={defaultFinancials.chart_meta} />
              ) : (
                <div className="empty-panel">
                  <LineChart />
                  <h4>No chart data yet</h4>
                  <p>Select a company from the watchlist, or ask about revenue, net income, EPS, assets, or liabilities to plot a trend.</p>
                </div>
              )}
            </div>

            <div className="content-column">
              {isLoading && (
                <div className="spinner-block">
                  <div className="spinner"></div>
                  <p>Routing query and verifying SEC SQLite records…</p>
                </div>
              )}

              {error && !isLoading && (
                <div className="error-panel">
                  <ShieldAlert />
                  <h3>Query Error</h3>
                  <p>{error}</p>
                </div>
              )}

              {!currentResponse && !isLoading && !error && (
                showDefaultTable ? (
                  <DataTable
                    columns={defaultFinancials.table_columns}
                    rows={defaultFinancials.table}
                    ticker={selectedTicker}
                  />
                ) : (
                  <div className="empty-panel">
                    <Building2 />
                    <h4>AI Financial Investigator</h4>
                    <p>Perform multi-hop reasoning, check balance sheet data directly from the SEC XBRL database, or query qualitative risk sections using the routing agent.</p>
                  </div>
                )
              )}

              {currentResponse && !isLoading && !error && (
                <div className="answer-block">
                  <div className="answer-query">Query — <span>{submittedPrompt}</span></div>

                  {currentResponse.table && currentResponse.table.length > 0 && (
                    <div className="answer-table">
                      <DataTable
                        columns={currentResponse.table_columns}
                        rows={currentResponse.table}
                        ticker={currentResponse.chart_meta?.series?.join('-vs-')}
                      />
                    </div>
                  )}

                  <div className="answer-header"><h3>Analysis</h3></div>
                  <div className="answer-body">
                    {currentResponse.answer
                      .split('\n')
                      .filter((line) => line.trim().length > 0)
                      .map((line, lIdx) => (
                        <p key={lIdx} className="answer-line">{renderFigureLine(line)}</p>
                      ))}
                  </div>

                  {currentResponse.sources && currentResponse.sources.length > 0 && (
                    <div className="citations">
                      <div className="citations-title">
                        <BookOpen />
                        Sources ({currentResponse.sources.length})
                      </div>
                      {currentResponse.sources.map((src, index) => {
                        const docSource = src.metadata?.source || `Database fact ${index + 1}`;
                        return (
                          <div key={index} className="citation">
                            <span className="citation-index">[{index + 1}]</span>
                            <div className="citation-body">
                              <div className="citation-source">{docSource}</div>
                              <div className="citation-text">{src.content}</div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="query-bar">
            <form onSubmit={handleSubmit} className="query-form">
              <input
                type="text"
                className="query-input"
                placeholder="Ask about revenue trends, comparisons, risks, or consumer facts..."
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                disabled={isLoading}
              />
              <button
                type="submit"
                className="query-submit"
                disabled={isLoading || !prompt.trim()}
              >
                <Send />
              </button>
            </form>
          </div>
        </main>
      </div>
    </>
  );
}

export default App;
