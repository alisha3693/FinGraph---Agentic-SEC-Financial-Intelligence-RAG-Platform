import React, { useState, useEffect } from 'react';
import { Send, Bot, User, Sparkles, BookOpen, ShieldAlert, Plus, TrendingUp, DollarSign, Activity, FileText, CheckCircle, Database } from 'lucide-react';

function App() {
  const [prompt, setPrompt] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [currentResponse, setCurrentResponse] = useState(null);
  const [error, setError] = useState(null);
  
  // App states
  const [companies, setCompanies] = useState([
    { ticker: "AAPL", name: "Apple Inc.", cik: "0000320193" },
    { ticker: "MSFT", name: "Microsoft Corporation", cik: "0000789019" }
  ]);
  const [selectedTicker, setSelectedTicker] = useState("AAPL");
  const [newTicker, setNewTicker] = useState('');
  const [loadingTicker, setLoadingTicker] = useState(false);
  const [tickerMessage, setTickerMessage] = useState({ text: '', isError: false });

  const sampleQuestions = [
    "What was Apple's revenue from 2021–2025?",
    "Compare Apple and Microsoft revenue.",
    "What risks did Apple mention in its latest 10-K?",
    "What products/strategies are discussed in the filing?"
  ];

  // Fetch registered companies on load
  const fetchCompanies = async () => {
    try {
      const response = await fetch('http://localhost:8000/api/companies');
      if (response.ok) {
        const data = await response.json();
        if (data.length > 0) {
          setCompanies(data);
        }
      }
    } catch (err) {
      console.error("Error loading companies list:", err);
    }
  };

  useEffect(() => {
    fetchCompanies();
  }, []);

  const handleSubmit = async (e, customPrompt = null) => {
    if (e) e.preventDefault();
    const activePrompt = customPrompt || prompt;
    if (!activePrompt.trim() || isLoading) return;

    setIsLoading(true);
    setError(null);
    setCurrentResponse(null);

    try {
      const response = await fetch('http://localhost:8000/api/query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ prompt: activePrompt.trim() }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to analyze query');
      }

      const data = await response.json();
      setCurrentResponse(data);
      if (customPrompt) {
        setPrompt('');
      }
    } catch (err) {
      console.error(err);
      setError(err.message || 'FastAPI Server connection failed. Run uvicorn inside multihop-rag.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleLoadTicker = async (e) => {
    e.preventDefault();
    if (!newTicker.trim() || loadingTicker) return;

    setLoadingTicker(true);
    setTickerMessage({ text: '', isError: false });
    const targetTicker = newTicker.trim().toUpperCase();

    try {
      const response = await fetch('http://localhost:8000/api/load_ticker', {
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

  // Custom high-quality SVG Chart Component for dependency-free rendering
  const renderSVGChart = (chartData) => {
    if (!chartData || chartData.length === 0) return null;

    // Detect if this is a comparison chart or a single company trend
    const isComparison = Object.keys(chartData[0]).some(k => k.includes("AAPL") || k.includes("MSFT"));
    
    const years = chartData.map(d => d.name);
    const width = 450;
    const height = 230;
    const padding = 40;

    // Calculate dynamic range
    let maxVal = 0;
    chartData.forEach(d => {
      Object.keys(d).forEach(k => {
        if (k !== 'name' && typeof d[k] === 'number') {
          if (d[k] > maxVal) maxVal = d[k];
        }
      });
    });
    
    maxVal = maxVal ? maxVal * 1.15 : 100; // Give 15% headroom

    const points = {};
    const keys = Object.keys(chartData[0]).filter(k => k !== 'name');

    keys.forEach(key => {
      points[key] = chartData.map((d, index) => {
        const x = padding + (index * (width - 2 * padding)) / (chartData.length - 1 || 1);
        const y = height - padding - (d[key] * (height - 2 * padding)) / maxVal;
        return { x, y, val: d[key] };
      });
    });

    const colors = {
      "Revenue": "#66fcf1",
      "Net Income": "#9d4edd",
      "EPS": "#f72585",
      "AAPL Revenue": "#66fcf1",
      "MSFT Revenue": "#4ea8de",
      "AAPL Net Income": "#9d4edd",
      "MSFT Net Income": "#f72585"
    };

    return (
      <div className="telemetry-card chart-container">
        <div className="chart-header">
          <TrendingUp className="chart-icon" />
          <span>{isComparison ? "Comparative Financial Performance ($ Billions)" : "Financial Telemetry Trends ($ Billions / EPS)"}</span>
        </div>
        
        <svg viewBox={`0 0 ${width} ${height}`} className="svg-canvas">
          {/* Background Grid Lines */}
          {[0, 0.25, 0.5, 0.75, 1].map((ratio, idx) => {
            const y = padding + ratio * (height - 2 * padding);
            const val = (maxVal * (1 - ratio)).toFixed(0);
            return (
              <g key={idx}>
                <line x1={padding} y1={y} x2={width - padding} y2={y} stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3" />
                <text x={padding - 8} y={y + 4} fill="#8b9bb4" fontSize="10" textAnchor="end">{val}</text>
              </g>
            );
          })}

          {/* X Axis Labels */}
          {years.map((yr, idx) => {
            const x = padding + (idx * (width - 2 * padding)) / (chartData.length - 1 || 1);
            return (
              <text key={idx} x={x} y={height - 12} fill="#8b9bb4" fontSize="11" textAnchor="middle">{yr}</text>
            );
          })}

          {/* Line drawings */}
          {keys.map(key => {
            const linePoints = points[key];
            const pathD = linePoints.map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
            return (
              <g key={key}>
                <path d={pathD} fill="none" stroke={colors[key] || "#fff"} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" style={{ transition: 'all 0.5s ease' }} />
                {linePoints.map((p, idx) => (
                  <circle key={idx} cx={p.x} cy={p.y} r="5" fill="#1f2833" stroke={colors[key] || "#fff"} strokeWidth="2.5" />
                ))}
              </g>
            );
          })}
        </svg>

        {/* Legend */}
        <div className="chart-legend">
          {keys.map(key => (
            <div key={key} className="legend-item">
              <span className="legend-dot" style={{ backgroundColor: colors[key] || "#fff" }}></span>
              <span>{key}</span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', width: '100%' }}>
      {/* Header */}
      <header className="app-header">
        <div className="logo-container">
          <Sparkles className="logo-glow-icon" />
          <span className="logo-text">SEC.Intelligence</span>
        </div>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
          <div className="status-badge sec-active">
            <Database style={{ width: '14px', height: '14px' }} />
            <span>SEC EDGAR Live Linked</span>
          </div>
          <div className="status-badge">
            <div className="status-dot"></div>
            <span>LangGraph API Active</span>
          </div>
        </div>
      </header>

      {/* Main Layout Grid */}
      <div className="app-container">
        
        {/* Sidebar */}
        <aside className="sidebar">
          {/* Ticker Loader Form */}
          <div className="sidebar-section">
            <h3>Ingest Ticker</h3>
            <form onSubmit={handleLoadTicker} className="loader-form">
              <input
                type="text"
                value={newTicker}
                onChange={(e) => setNewTicker(e.target.value)}
                placeholder="Ticker (e.g. AMZN, TSLA)"
                disabled={loadingTicker}
                className="ticker-input"
              />
              <button type="submit" disabled={loadingTicker || !newTicker.trim()} className="plus-btn">
                {loadingTicker ? <div className="mini-spinner"></div> : <Plus style={{ width: '18px', height: '18px' }} />}
              </button>
            </form>
            {tickerMessage.text && (
              <p className={`ticker-msg ${tickerMessage.isError ? 'err' : 'ok'}`}>
                {tickerMessage.isError ? <ShieldAlert style={{ width: '12px', height: '12px' }} /> : <CheckCircle style={{ width: '12px', height: '12px' }} />}
                {tickerMessage.text}
              </p>
            )}
          </div>

          {/* Watchlist */}
          <div className="sidebar-section">
            <h3>Active Companies</h3>
            <div className="company-list">
              {companies.map((co) => (
                <button
                  key={co.ticker}
                  onClick={() => setSelectedTicker(co.ticker)}
                  className={`company-chip ${selectedTicker === co.ticker ? 'active' : ''}`}
                >
                  <div className="chip-details">
                    <span className="chip-ticker">{co.ticker}</span>
                    <span className="chip-name">{co.name}</span>
                  </div>
                </button>
              ))}
            </div>
          </div>
          
          {/* Suggested Queries */}
          <div className="sidebar-section" style={{ flexGrow: 1 }}>
            <h3>Analysis Templates</h3>
            <div className="recent-queries-list">
              {sampleQuestions.map((q, idx) => (
                <button 
                  key={idx} 
                  className="recent-query-item"
                  onClick={() => {
                    setPrompt(q);
                    handleSubmit(null, q);
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
          
          <div className="sidebar-footer">
            <BookOpen style={{ width: '16px', height: '16px', color: 'var(--accent-cyan)' }} />
            <span>Scope: SEC 10-K & XBRL Database</span>
          </div>
        </aside>

        {/* Main Double Dashboard Layout */}
        <div className="dashboard-grid">
          
          {/* Left Panel: Chat Interface */}
          <main className="chat-window">
            {!currentResponse && !isLoading && !error && (
              <div className="welcome-screen">
                <Bot className="welcome-icon" />
                <h2>AI Financial Investigator</h2>
                <p>
                  Perform multi-hop reasoning, check balance sheet data directly from SEC XBRL database, or query qualitative risk sections using our routing agent.
                </p>
              </div>
            )}

            {isLoading && (
              <div className="spinner-container" style={{ flexGrow: 1 }}>
                <div className="spinner"></div>
                <p style={{ color: 'var(--text-muted)' }}>Routing query and verifying SEC SQLite records...</p>
              </div>
            )}

            {error && (
              <div className="welcome-screen" style={{ color: 'var(--text-muted)' }}>
                <ShieldAlert style={{ width: '50px', height: '50px', color: '#ff5555', marginBottom: '1rem' }} />
                <h3 style={{ color: 'var(--text-bright)' }}>Query Error</h3>
                <p style={{ fontSize: '0.95rem' }}>{error}</p>
              </div>
            )}

            {currentResponse && !isLoading && !error && (
              <div className="response-container">
                {/* User Prompt */}
                <div className="bubble user">
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600, fontSize: '0.85rem', color: 'var(--accent-cyan)', marginBottom: '0.4rem' }}>
                    <User style={{ width: '14px', height: '14px' }} /> User Request
                  </div>
                  <p>{prompt || "Analysis Query"}</p>
                </div>

                {/* AI RAG Response */}
                <div className="bubble ai">
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600, fontSize: '0.85rem', color: 'var(--accent-purple)', marginBottom: '0.4rem' }}>
                    <Bot style={{ width: '14px', height: '14px' }} /> SEC Intelligence Agent
                  </div>
                  
                  {/* Process Markdown-like formatting inside answer */}
                  <div className="answer-content">
                    {currentResponse.answer.split('\n').map((line, lIdx) => {
                      if (line.startsWith('###')) {
                        return <h3 key={lIdx} style={{ color: 'var(--accent-cyan)', margin: '1.2rem 0 0.6rem 0', borderBottom: '1px solid var(--border-light)', paddingBottom: '0.4rem' }}>{line.replace('###', '')}</h3>;
                      }
                      if (line.startsWith('|')) {
                        return <div key={lIdx} style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', padding: '0.2rem 0', color: 'var(--text-primary)' }}>{line}</div>;
                      }
                      return <p key={lIdx} style={{ margin: '0.4rem 0', lineHeight: 1.6 }}>{line}</p>;
                    })}
                  </div>

                  {/* Retrieved Sources Section */}
                  {currentResponse.sources && currentResponse.sources.length > 0 && (
                    <div className="sources-section">
                      <div className="sources-title">
                        <BookOpen style={{ width: '16px', height: '16px' }} />
                        Citations & Verified Context ({currentResponse.sources.length})
                      </div>
                      <div className="sources-grid">
                        {currentResponse.sources.map((src, index) => {
                          const docSource = src.metadata?.source || `Database Fact ${index + 1}`;
                          return (
                            <div key={index} className="source-card">
                              <div className="source-header">{docSource}</div>
                              <div className="source-body">{src.content}</div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Input Form */}
            <form onSubmit={handleSubmit} className="input-form">
              <input
                type="text"
                className="prompt-input"
                placeholder="Ask about revenue trends, comparisons, risks, or consumer facts..."
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                disabled={isLoading}
              />
              <button 
                type="submit" 
                className="submit-btn" 
                disabled={isLoading || !prompt.trim()}
              >
                <Send style={{ width: '20px', height: '20px' }} />
              </button>
            </form>
          </main>

          {/* Right Panel: Financial Metrics & Active Telemetry Dashboard */}
          <aside className="telemetry-panel">
            {/* Render chart if analytical query returned metrics */}
            {currentResponse && currentResponse.chart_data && currentResponse.chart_data.length > 0 ? (
              renderSVGChart(currentResponse.chart_data)
            ) : (
              // Default Telemetry Panel when no chart is rendering
              <div className="telemetry-default">
                <div className="telemetry-default-title">
                  <Activity style={{ animation: 'pulse 2s infinite', color: 'var(--accent-cyan)', width: '32px', height: '32px' }} />
                  <h2>Active Analyzer Panel</h2>
                  <p>Submit questions about metrics (e.g. Apple's revenue trends or comparisons) to unlock visual charts here.</p>
                </div>
                
                {/* Dummy stats for selected company to look clean */}
                <div style={{ marginTop: '2rem', display: 'flex', flexDirection: 'column', gap: '1rem', width: '100%' }}>
                  <div className="telemetry-stat-card">
                    <DollarSign style={{ color: 'var(--accent-cyan)' }} />
                    <div>
                      <h4>Balance Sheet Analysis</h4>
                      <p>Full liability-to-asset models populated from CIK filings.</p>
                    </div>
                  </div>
                  <div className="telemetry-stat-card">
                    <FileText style={{ color: 'var(--accent-purple)' }} />
                    <div>
                      <h4>Audit-Ready Auditing</h4>
                      <p>Form 10-K & 10-Q textual validation pipeline.</p>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </aside>
          
        </div>
      </div>
    </div>
  );
}

export default App;
