// The answer panel: structured answers, with the original bullet list as fallback.

import React, { useState, useEffect, useRef } from 'react';
import {
  BookOpen, ArrowUpRight, ArrowDownRight, Maximize2, Minimize2,
} from 'lucide-react';
import { renderFigureLine, renderRichText } from './richText';
import { DataTable } from './DataTable';

function KpiGrid({ tiles }) {
  return (
    <div className="kpi-grid">
      {tiles.map((t) => (
        <div key={t.label} className="kpi">
          <div className="metric-label">{t.label}</div>
          <div className="metric-value">{t.value}</div>
          {t.detail && (
            <div className={`kpi-detail ${t.neutral ? 'flat' : t.trend}`}>
              {t.trend === 'up' && <ArrowUpRight />}
              {t.trend === 'down' && <ArrowDownRight />}
              {t.detail}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Scoreboard({ board }) {
  return (
    <div className="scoreboard-wrap">
      <table className="scoreboard">
        <thead>
          <tr>
            <th>Metric</th>
            {board.companies.map((c) => <th key={c}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {board.rows.map((row) => (
            <tr key={row.label}>
              <td>{row.label}</td>
              {board.companies.map((c) => (
                <td key={c} className={row.top === c ? 'is-top' : ''}>{row.values[c] ?? '—'}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="scoreboard-note">Highlighted: highest value in each row. Computed from SEC XBRL data.</div>
    </div>
  );
}

function SourceList({ sources, highlighted }) {
  const [expanded, setExpanded] = useState({});
  const groups = [
    { title: 'Data', items: [] },
    { title: 'Filings', items: [] },
  ];
  sources.forEach((src, i) => groups[src.metadata?.filing_type ? 1 : 0].items.push({ src, n: i + 1 }));

  return (
    <div className="citations">
      <div className="citations-title"><BookOpen />Sources ({sources.length})</div>
      {groups.filter((g) => g.items.length > 0).map((group) => (
        <div key={group.title} className="source-group">
          <div className="source-group-title">{group.title}</div>
          {group.items.map(({ src, n }) => {
            const m = src.metadata || {};
            const url = /^https?:\/\//i.test(m.source || '') ? m.source : null;
            const isOpen = expanded[n];
            const long = (src.content || '').length > 240;
            return (
              <div key={n} id={`source-${n}`} className={`source${highlighted === n ? ' is-highlighted' : ''}`}>
                <div className="source-head">
                  <span className="citation-index">[{n}]</span>
                  <span className="source-meta">
                    {m.filing_type ? (
                      <>
                        <strong>{m.company}</strong>
                        <span>{m.filing_type}</span>
                        {m.fiscal_year && <span>FY{m.fiscal_year}</span>}
                        {m.filing_date && <span>filed {m.filing_date}</span>}
                        {m.section && m.section !== 'Filing text' && <span>{m.section}</span>}
                      </>
                    ) : (
                      <span>{src.content}</span>
                    )}
                  </span>
                  {url && (
                    <a className="source-link" href={url} target="_blank" rel="noreferrer">
                      {m.filing_type ? 'Filing' : 'Data'}<ArrowUpRight />
                    </a>
                  )}
                </div>
                {m.filing_type && (
                  <>
                    <div className={`source-excerpt${isOpen ? ' is-open' : ''}`}>{src.content}</div>
                    {long && (
                      <button type="button" className="source-toggle" onClick={() => setExpanded((e) => ({ ...e, [n]: !isOpen }))}>
                        {isOpen ? 'Show less' : 'Show more'}
                      </button>
                    )}
                  </>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function PointList({ points, rich }) {
  return (
    <ul className="answer-list">
      {points.map((p, i) => <li key={i} className="answer-line">{rich(p)}</li>)}
    </ul>
  );
}

// Query line above an answer, with the toggle that hides the chart column so the answer
// gets the full workspace width.
function AnswerQuery({ prompt, focused, onToggleFocus }) {
  return (
    <div className="answer-query">
      <div className="answer-query-text">Query — <span>{prompt}</span></div>
      {onToggleFocus && (
        <button type="button" className="answer-focus-toggle" onClick={onToggleFocus}
          aria-pressed={focused} title={focused ? 'Show the chart again' : 'Hide the chart and widen the response'}>
          {focused ? <Minimize2 /> : <Maximize2 />}
          {focused ? 'Show chart' : 'Expand'}
        </button>
      )}
    </div>
  );
}

export function LegacyAnswer({ response, prompt, focused, onToggleFocus }) {
  return (
    <div className="answer-block">
      <AnswerQuery prompt={prompt} focused={focused} onToggleFocus={onToggleFocus} />

      {response.table && response.table.length > 0 && (
        <div className="answer-table">
          <DataTable
            columns={response.table_columns}
            rows={response.table}
            ticker={response.chart_meta?.series?.join('-vs-')}
          />
        </div>
      )}

      <div className="answer-header"><h3>Analysis</h3></div>
      <div className="answer-body">
        <ul className="answer-list">
          {response.answer
            .split('\n')
            .map((line) => line.replace(/^\s*[-•*]\s*/, '').trim())
            .filter((line) => line.length > 0)
            .map((line, lIdx) => (
              <li key={lIdx} className="answer-line">{renderFigureLine(line)}</li>
            ))}
        </ul>
      </div>

      {response.sources && response.sources.length > 0 && (
        <div className="citations">
          <div className="citations-title">
            <BookOpen />
            Sources ({response.sources.length})
          </div>
          {response.sources.map((src, index) => {
            const docSource = src.metadata?.source || `Database fact ${index + 1}`;
            const isLink = /^https?:\/\//i.test(docSource);
            return (
              <div key={index} className="citation">
                <span className="citation-index">[{index + 1}]</span>
                <div className="citation-body">
                  <div className="citation-source">
                    {isLink ? (
                      <a href={docSource} target="_blank" rel="noreferrer">{docSource}</a>
                    ) : (
                      docSource
                    )}
                  </div>
                  <div className="citation-text">{src.content}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const PART_TITLES = { figures: 'Figures', filings: 'From the filings' };

export function AnswerView({ response, prompt, focused, onToggleFocus }) {
  const [highlighted, setHighlighted] = useState(null);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const s = response.structured;
  if (!s || !Array.isArray(s.parts) || s.parts.length === 0) {
    return <LegacyAnswer response={response} prompt={prompt} focused={focused} onToggleFocus={onToggleFocus} />;
  }

  const sources = response.sources || [];
  const onCite = (n) => {
    setHighlighted(n);
    document.getElementById(`source-${n}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setHighlighted(null), 1800);
  };
  const rich = (text) => renderRichText(text, sources, onCite);
  const isMixed = s.parts.length > 1;
  const hasTable = response.table && response.table.length > 0;
  // In a single-part answer the summary line usually restates the verdict; show one, not both.
  const summary = s.verdict && !isMixed ? null : s.summary;

  return (
    <div className="answer-block">
      <AnswerQuery prompt={prompt} focused={focused} onToggleFocus={onToggleFocus} />

      {(summary || s.verdict) && (
        <div className="answer-lead">
          {summary && <p className="answer-summary">{rich(summary)}</p>}
          {s.verdict && (
            <div className="answer-verdict">
              <span className="answer-verdict-label">Verdict</span>
              <p>{rich(s.verdict)}</p>
            </div>
          )}
        </div>
      )}

      {s.parts.map((part) => (
        <section key={part.key} className="answer-part">
          <div className="answer-header"><h3>{isMixed ? PART_TITLES[part.key] : 'Analysis'}</h3></div>
          <div className="answer-body">
            {isMixed && part.summary && <p className="part-summary">{rich(part.summary)}</p>}
            {part.kpis?.length > 0 && <KpiGrid tiles={part.kpis} />}
            {part.scoreboard && <Scoreboard board={part.scoreboard} />}
            {(part.sections || []).map((sec, i) => (
              <div key={i} className="answer-section">
                {sec.title && <h4 className="answer-section-title">{sec.title}</h4>}
                <PointList points={sec.points} rich={rich} />
              </div>
            ))}
            {part.key === 'figures' && hasTable && (
              <details className="answer-data">
                <summary>Data table · {response.table.length} rows</summary>
                <DataTable
                  columns={response.table_columns}
                  rows={response.table}
                  ticker={response.chart_meta?.series?.join('-vs-')}
                />
              </details>
            )}
          </div>
        </section>
      ))}

      {s.connections?.length > 0 && (
        <section className="answer-part">
          <div className="answer-header"><h3>How they connect</h3></div>
          <div className="answer-body"><PointList points={s.connections} rich={rich} /></div>
        </section>
      )}

      {sources.length > 0 && <SourceList sources={sources} highlighted={highlighted} />}
    </div>
  );
}
