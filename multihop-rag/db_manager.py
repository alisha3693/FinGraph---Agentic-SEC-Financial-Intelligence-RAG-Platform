import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sec_financials.db")

def init_db():
    """Initialize database schemas for companies and structured metrics."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT UNIQUE NOT NULL,
        cik TEXT NOT NULL,
        name TEXT NOT NULL
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS financials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        year INTEGER NOT NULL,
        revenue REAL,
        net_income REAL,
        eps REAL,
        assets REAL,
        liabilities REAL,
        FOREIGN KEY (company_id) REFERENCES companies (id),
        UNIQUE(company_id, year)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS migrations (
        name TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("SELECT 1 FROM migrations WHERE name = ?", ("remove_static_seed_data",))
    if cursor.fetchone() is None:
        cursor.execute("DELETE FROM financials")
        cursor.execute("DELETE FROM companies")
        cursor.execute("INSERT INTO migrations (name) VALUES (?)", ("remove_static_seed_data",))
    
    conn.commit()
    conn.close()

def save_financials(ticker, cik, name, data_by_year):
    """Save parsed financial metrics into SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("INSERT OR IGNORE INTO companies (ticker, cik, name) VALUES (?, ?, ?)", 
                   (ticker.upper(), cik, name))
    cursor.execute("UPDATE companies SET name = ?, cik = ? WHERE ticker = ?", (name, cik, ticker.upper()))
    
    cursor.execute("SELECT id FROM companies WHERE ticker = ?", (ticker.upper(),))
    co_id = cursor.fetchone()[0]
    
    for year, metrics in data_by_year.items():
        cursor.execute("""
        INSERT OR REPLACE INTO financials (company_id, year, revenue, net_income, eps, assets, liabilities)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            co_id, 
            int(year), 
            metrics.get("revenue"), 
            metrics.get("net_income"), 
            metrics.get("eps"), 
            metrics.get("assets"), 
            metrics.get("liabilities")
        ))
        
    conn.commit()
    conn.close()

def delete_company(ticker):
    """Remove a company and its cached financials from the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM companies WHERE ticker = ?", (ticker.upper(),))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return False
    co_id = row[0]
    cursor.execute("DELETE FROM financials WHERE company_id = ?", (co_id,))
    cursor.execute("DELETE FROM companies WHERE id = ?", (co_id,))
    conn.commit()
    conn.close()
    return True

def get_companies():
    """Fetch all cached companies in the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT ticker, cik, name FROM companies")
    rows = cursor.fetchall()
    conn.close()
    return [{"ticker": r[0], "cik": r[1], "name": r[2]} for r in rows]


def get_company_facts_source(ticker):
    """Return the SEC Company Facts URL used for a cached company."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT cik FROM companies WHERE ticker = ?", (ticker.upper(),))
    row = cursor.fetchone()
    conn.close()
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{row[0]}.json" if row else None

def get_financials_for_ticker(ticker):
    """Fetch all annual financials for a given ticker."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT f.year, f.revenue, f.net_income, f.eps, f.assets, f.liabilities
    FROM financials f
    JOIN companies c ON f.company_id = c.id
    WHERE c.ticker = ?
    ORDER BY f.year ASC
    """, (ticker.upper(),))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "year": r[0],
            "revenue": r[1],
            "net_income": r[2],
            "eps": r[3],
            "assets": r[4],
            "liabilities": r[5]
        } for r in rows
    ]

# Initialize db when importing
init_db()
