import React, { useState, useEffect, useRef } from 'react';
import {
  Send, BookOpen, ShieldAlert, Plus, X, CheckCircle, BarChart3, LineChart, Building2, FileText, Sun, Moon,
} from 'lucide-react';
import { THEME_KEY, PROMPT_HISTORY_KEY, PROMPT_HISTORY_LIMIT, getInitialTheme, getInitialPromptHistory } from './lib/storage';
import { SPLIT, SIDEBAR, useDivider } from './hooks/useDivider';
import { METRIC_COLORS, COMPARISON_PALETTE } from './lib/format';
import { DataTable } from './components/DataTable';
import { MetricStrip } from './components/MetricStrip';
import { TrendChart } from './components/TrendChart';
import { AnswerView } from './components/AnswerView';
import { ChartPanel } from './components/Charts';
import { availableCharts } from './lib/chartData';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  const [prompt, setPrompt] = useState('');
  const [submittedPrompt, setSubmittedPrompt] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [currentResponse, setCurrentResponse] = useState(null);

  // Chart/response split: the chart column's share of the workspace width, dragged with the
  // divider (or arrow keys) and remembered. Focus mode hides the chart so the response gets
  // the full width.
  const [focusResponse, setFocusResponse] = useState(false);
  const workspaceRef = useRef(null);
  const layoutRef = useRef(null);
  const split = useDivider(SPLIT, (e) => {
    const rect = workspaceRef.current.getBoundingClientRect();
    return ((e.clientX - rect.left) / rect.width) * 100;
  });
  const sidebar = useDivider(SIDEBAR, (e) => e.clientX - layoutRef.current.getBoundingClientRect().left);
  const [error, setError] = useState(null);

  // App states
  const [companies, setCompanies] = useState([]);
  // The company/companies currently "in focus" — resolved entirely from the query text
  // (auto-loading a company from SEC EDGAR if it isn't ingested yet) or from the sidebar's
  // manual ingest form. One entry for a single-company query, two for a comparison.
  const [activeCompanies, setActiveCompanies] = useState([]);
  const [newTicker, setNewTicker] = useState('');
  const [loadingTicker, setLoadingTicker] = useState(false);
  const [tickerMessage, setTickerMessage] = useState({ text: '', isError: false });
  const [defaultFinancials, setDefaultFinancials] = useState(null);
  const [theme, setTheme] = useState(getInitialTheme);
  const [promptHistory, setPromptHistory] = useState(getInitialPromptHistory);
  const promptInputRef = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // Ignore — theme just won't persist across reloads in this environment.
    }
  }, [theme]);

  // Records a submitted prompt at the front of the cached history (most recent first),
  // moving an existing duplicate to the front instead of listing it twice, capped to the
  // 5 most recent. Persisted to localStorage so it survives a reload.
  const recordPromptHistory = (submitted) => {
    setPromptHistory((prev) => {
      const next = [submitted, ...prev.filter((p) => p !== submitted)].slice(0, PROMPT_HISTORY_LIMIT);
      try {
        localStorage.setItem(PROMPT_HISTORY_KEY, JSON.stringify(next));
      } catch {
        // Ignore — history just won't persist across reloads in this environment.
      }
      return next;
    });
  };

  // Single-company focus only — comparisons (two active companies) don't have one "company
  // facts" link or a default-overview fetch of their own; the query response already carries
  // both companies' data.
  const activeTicker = activeCompanies.length === 1 ? activeCompanies[0].ticker : '';
  const activeCompanyFull = activeTicker ? companies.find((co) => co.ticker === activeTicker) : null;
  const companyFactsUrl = activeCompanyFull ? `https://data.sec.gov/api/xbrl/companyfacts/CIK${activeCompanyFull.cik}.json` : null;

  // Fetch registered companies on load — DB is source of truth, no placeholder data.
  // Company selection stays query-driven; the sidebar is no longer used to scope answers.
  // Returns the fetched list so callers can use it immediately, before the `companies`
  // state update from this call has actually re-rendered.
  const fetchCompanies = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/companies`);
      if (response.ok) {
        const data = await response.json();
        setCompanies(data);
        return data;
      }
    } catch (err) {
      console.error("Error loading companies list:", err);
    }
    return null;
  };

  useEffect(() => {
    fetchCompanies();
  }, []);

  // Full-metric overview (Revenue, Net Income, EPS, Assets, Liabilities) for whichever
  // single company the latest query (or manual ingest) resolved — not a sidebar selection.
  useEffect(() => {
    if (!activeTicker) {
      setDefaultFinancials(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`${API_BASE}/api/financials/${activeTicker}`);
        if (response.ok) {
          const data = await response.json();
          if (!cancelled) setDefaultFinancials(data);
        }
      } catch (err) {
        console.error("Error loading default financials:", err);
      }
    })();
    return () => { cancelled = true; };
  }, [activeTicker]);

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
        body: JSON.stringify({ prompt: activePrompt }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to analyze query');
      }

      const data = await response.json();
      setCurrentResponse(data);
      setPrompt('');
      recordPromptHistory(activePrompt);

      // The backend resolves (and auto-loads, if new) the company/companies this query was
      // about — use that to drive the heading and default overview instead of any sidebar
      // selection. A companyless/ambiguous response leaves the previous focus in place
      // rather than blanking the heading out.
      if (data.resolved_companies && data.resolved_companies.length > 0) {
        setActiveCompanies(data.resolved_companies);
        fetchCompanies(); // refresh the watchlist in case a new ticker was just auto-loaded
      }
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
        setActiveCompanies((prev) => prev.filter((co) => co.ticker !== ticker));
      } else {
        const data = await response.json().catch(() => ({}));
        setTickerMessage({ text: data.detail || `Failed to remove ${ticker}.`, isError: true });
      }
    } catch {
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
        const list = await fetchCompanies();
        const loaded = list?.find((co) => co.ticker === targetTicker);
        setActiveCompanies([{ ticker: targetTicker, name: loaded?.name || targetTicker }]);
      } else {
        setTickerMessage({ text: data.message || 'Filing ingestion failed.', isError: true });
      }
    } catch {
      setTickerMessage({ text: 'Error connecting to database loader.', isError: true });
    } finally {
      setLoadingTicker(false);
    }
  };

  // Chart views the current response's data supports (empty for a response without data,
  // which keeps the previous behaviour: the default chart, or the empty panel).
  const queryCharts = currentResponse ? availableCharts(currentResponse) : [];
  const showDefaultChart = !queryCharts.length && defaultFinancials?.chart_data?.length > 0;
  const showQueryChart = queryCharts.length > 0;
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

      <div
        ref={layoutRef}
        className={`layout${sidebar.dragging ? ' is-resizing' : ''}`}
        style={{ '--sidebar-w': `${sidebar.value}px` }}
      >
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
              <p className="sidebar-hint">Company is resolved from the prompt text, not click selection.</p>
            )}
            {companies.length === 0 ? (
              <div className="empty-state-inline">
                <p>No companies ingested yet. Add a ticker above to get started.</p>
              </div>
            ) : (
              <div className="watchlist">
                {companies.map((co) => (
                  <div key={co.ticker} className="watchlist-item"> 
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
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="sidebar-block grow">
            <span className="sidebar-label">Recent Prompts</span>
            {promptHistory.length === 0 ? (
              <div className="empty-state-inline">
                <p>Your last {PROMPT_HISTORY_LIMIT} questions will appear here — ask something to get started.</p>
              </div>
            ) : (
              <div className="history-list">
                {promptHistory.map((q, idx) => (
                  <button
                    key={idx}
                    className="history-item"
                    onClick={() => {
                      setPrompt(q);
                      promptInputRef.current?.focus();
                    }}
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="sidebar-block">
            <div className="sidebar-footer">
              <BookOpen style={{ width: '14px', height: '14px' }} />
              <span>Scope: SEC 10-K &amp; XBRL Database</span>
            </div>
          </div>
        </aside>

        {/* Zero-width grid column; the handle overhangs the sidebar's edge (see .sidebar-divider). */}
        <div className="sidebar-divider" aria-label="Resize sidebar" {...sidebar.handlers} />

        {/* Main workspace */}
        <main className="main">
          <div className="context-bar">
            <div className="context-bar-left">
              {activeCompanies.length === 1 ? (
                <>
                  <span className="context-ticker">{activeCompanies[0].ticker}</span>
                  <span className="context-name">{activeCompanies[0].name}</span>
                </>
              ) : activeCompanies.length >= 2 ? (
                <span className="context-name">{activeCompanies.map((co) => co.name).join(' vs ')}</span>
              ) : (
                <span className="context-name">Company is resolved from the prompt text.</span>
              )}
            </div>
            {companyFactsUrl && (
              <a href={companyFactsUrl} target="_blank" rel="noreferrer" className="context-source">
                <FileText style={{ width: '12px', height: '12px' }} />
                <span>Company facts (SEC EDGAR)</span>
              </a>
            )}
          </div>

          {activeTicker && defaultFinancials?.table?.length > 0 && (
            <MetricStrip columns={defaultFinancials.table_columns} rows={defaultFinancials.table} />
          )}

          <div
            ref={workspaceRef}
            className={`workspace${focusResponse && currentResponse && !isLoading ? ' is-focus' : ''}${split.dragging ? ' is-resizing' : ''}`}
            style={{ '--chart-w': `${split.value}%` }}
          >
            <div className="chart-column">
              {showQueryChart ? (
                <ChartPanel
                  key={submittedPrompt}
                  response={currentResponse}
                  charts={queryCharts}
                  palette={COMPARISON_PALETTE}
                  metricColors={METRIC_COLORS}
                  renderTrend={() => <TrendChart chartData={currentResponse.chart_data} chartMeta={currentResponse.chart_meta} />}
                />
              ) : showDefaultChart ? (
                <TrendChart chartData={defaultFinancials.chart_data} chartMeta={defaultFinancials.chart_meta} />
              ) : (
                <div className="empty-panel">
                  <LineChart />
                  <h4>No chart data yet</h4>
                  <p>Ask about a company's revenue, net income, EPS, assets, or liabilities to plot a trend — any SEC-filed company works, not just ones already loaded.</p>
                </div>
              )}
            </div>

            <div className="workspace-divider" aria-label="Resize chart and response" {...split.handlers} />

            <div className="content-column">
              {isLoading && (
                <div className="spinner-block">
                  <div className="spinner"></div>
                  <p>Routing query and verifying SEC SQLite records… (a company mentioned for the first time is loaded from SEC EDGAR automatically — this can take up to ~30s)</p>
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
                    ticker={activeTicker}
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
                <AnswerView
                  key={submittedPrompt}
                  response={currentResponse}
                  prompt={submittedPrompt}
                  focused={focusResponse}
                  onToggleFocus={() => setFocusResponse((f) => !f)}
                />
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
                ref={promptInputRef}
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
