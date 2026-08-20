import os
import logging
from typing import Dict, Any, List
from typing_extensions import TypedDict
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langgraph.graph import StateGraph, END
import requests

from db_manager import get_company_facts_source, get_financials_for_ticker

logger = logging.getLogger(__name__)


def _llm() -> ChatGroq:
    return ChatGroq(model=os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b"), temperature=0)

# State definition
class AgentState(TypedDict):
    query: str
    ticker: str
    route: str
    answer: str
    sources: List[Dict[str, Any]]
    chart_data: List[Dict[str, Any]]

# Router definition using LLM structured output
def router_node(state: AgentState) -> Dict[str, Any]:
    query = state["query"].lower()
    
    # Simple keyword rule router for speed and reliability, falling back to LLM if needed
    route = "filing_rag"
    
    if any(k in query for k in ["compare", "comparison", "versus", " vs "]):
        route = "comparison"
    elif any(k in query for k in ["revenue", "net income", "profit", "eps", "earnings", "assets", "liabilities", "balance sheet", "income statement", "financials"]):
        route = "financial_analysis"
    elif any(k in query for k in ["device", "gen z", "consumer", "phone preference", "brand ownership", "market share"]):
        route = "external"
        
    # Extract ticker from query if possible
    ticker = "AAPL"
    if "microsoft" in query or "msft" in query:
        ticker = "MSFT"
    elif "google" in query or "goog" in query:
        ticker = "GOOGL"
    elif "tesla" in query or "tsla" in query:
        ticker = "TSLA"
        
    return {"route": route, "ticker": ticker}

def financial_analysis_node(state: AgentState) -> Dict[str, Any]:
    ticker = state.get("ticker", "AAPL").upper()
    query = state["query"]
    
    # Query database
    financials = get_financials_for_ticker(ticker)
    
    if not financials:
        return {
            "answer": f"I currently do not have cached financials for {ticker}. Please load the ticker using the dashboard sidebar first.",
            "chart_data": [],
            "sources": []
        }
        
    # Build chart data format: [{ "name": "2021", "Revenue": 365.8, "Net Income": 94.6 }]
    chart_data = []
    markdown_table = "| Fiscal Year | Revenue | Net Income | EPS | Assets | Liabilities |\n|---|---|---|---|---|---|\n"
    
    for row in financials:
        rev_b = row["revenue"] / 1e9 if row["revenue"] else 0
        ni_b = row["net_income"] / 1e9 if row["net_income"] else 0
        assets_b = row["assets"] / 1e9 if row["assets"] else 0
        liab_b = row["liabilities"] / 1e9 if row["liabilities"] else 0
        
        chart_data.append({
            "name": str(row["year"]),
            "Revenue": round(rev_b, 2),
            "Net Income": round(ni_b, 2),
            "EPS": round(row["eps"], 2) if row["eps"] else 0
        })
        
        markdown_table += f"| {row['year']} | ${rev_b:.2f}B | ${ni_b:.2f}B | ${row['eps'] or 0:.2f} | ${assets_b:.2f}B | ${liab_b:.2f}B |\n"

    # Use LLM to formulate narrative using only the retrieved database numbers (preventing math hallucinations)
    llm = _llm()
    prompt = ChatPromptTemplate.from_template(
        "You are an expert financial analyst. Formulate a short executive summary describing the revenue, net income, "
        "and EPS trends for {ticker} using ONLY the following structured facts:\n\n{table}\n\n"
        "Question: {question}\n\nInclude the source as SQLite SEC EDGAR Facts DB."
    )
    
    chain = prompt | llm | StrOutputParser()
    narrative = chain.invoke({"ticker": ticker, "table": markdown_table, "question": query})
    
    answer = f"### Structured Financial Data for {ticker}\n\n{markdown_table}\n\n### Trend Analysis\n{narrative}"
    
    return {
        "answer": answer,
        "chart_data": chart_data,
        "sources": [{"content": f"Structured annual financial facts for {ticker}.", "metadata": {"source": get_company_facts_source(ticker)}}]
    }

def filing_rag_node(state: AgentState) -> Dict[str, Any]:
    ticker = state.get("ticker", "AAPL").upper()
    query = state["query"]
    
    # Load Chroma
    current_dir = os.path.dirname(os.path.abspath(__file__))
    db_dir = os.path.join(current_dir, "chroma_db")
    if not os.path.isdir(db_dir):
        return {"answer": f"No SEC filing index exists for {ticker}. Load the ticker first.", "sources": [], "chart_data": []}
    embeddings = HuggingFaceEmbeddings(model_name=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    db = Chroma(persist_directory=db_dir, embedding_function=embeddings)
    
    # Retrieve documents relevant to ticker
    retriever = db.as_retriever(search_kwargs={
        "k": 4,
        "filter": {"$and": [{"company": ticker}, {"filing_type": {"$in": ["10-K", "10-Q", "8-K"]}}]},
    })
    docs = retriever.invoke(query)
        
    context = "\n\n".join(f"[{doc.metadata.get('source', 'Unknown')}]: {doc.page_content}" for doc in docs)
    
    if not docs:
        return {"answer": f"No indexed SEC filing evidence is available for {ticker}.", "sources": [], "chart_data": []}
    llm = _llm()
    prompt = ChatPromptTemplate.from_template(
        "You are an assistant for question-answering tasks analyzing SEC EDGAR filings.\n"
        "Use the following pieces of retrieved filing context to answer the question.\n"
        "If you don't know the answer, say that you don't know.\n\n"
        "Context:\n{context}\n\n"
        "Question: {question}\n\n"
        "Answer (be comprehensive and ground everything in source documents. Cite the files/items):"
    )
    
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"context": context, "question": query})
    
    sources = [
        {
            "content": doc.page_content,
            "metadata": doc.metadata
        }
        for doc in docs
    ]
    
    return {
        "answer": answer,
        "sources": sources,
        "chart_data": []
    }

def comparison_node(state: AgentState) -> Dict[str, Any]:
    query = state["query"]
    
    # Fetch AAPL and MSFT financials to perform comparisons
    aapl_financials = get_financials_for_ticker("AAPL")
    msft_financials = get_financials_for_ticker("MSFT")
    
    if not aapl_financials or not msft_financials:
        return {
            "answer": "Comparing Apple and Microsoft requires both tickers to be loaded. Make sure AAPL and MSFT are cached.",
            "chart_data": [],
            "sources": []
        }
        
    # Pivot datasets by year
    years = sorted(list(set([r["year"] for r in aapl_financials] + [r["year"] for r in msft_financials])))
    
    aapl_by_yr = {r["year"]: r for r in aapl_financials}
    msft_by_yr = {r["year"]: r for r in msft_financials}
    
    chart_data = []
    markdown_table = "| Year | Apple Revenue | Microsoft Revenue | Apple Net Income | Microsoft Net Income |\n|---|---|---|---|---|\n"
    
    for yr in years:
        a_rev = aapl_by_yr.get(yr, {}).get("revenue", 0) or 0
        m_rev = msft_by_yr.get(yr, {}).get("revenue", 0) or 0
        a_ni = aapl_by_yr.get(yr, {}).get("net_income", 0) or 0
        m_ni = msft_by_yr.get(yr, {}).get("net_income", 0) or 0
        
        chart_data.append({
            "name": str(yr),
            "AAPL Revenue": round(a_rev / 1e9, 2),
            "MSFT Revenue": round(m_rev / 1e9, 2),
            "AAPL Net Income": round(a_ni / 1e9, 2),
            "MSFT Net Income": round(m_ni / 1e9, 2)
        })
        
        markdown_table += f"| {yr} | ${a_rev/1e9:.2f}B | ${m_rev/1e9:.2f}B | ${a_ni/1e9:.2f}B | ${m_ni/1e9:.2f}B |\n"
        
    llm = _llm()
    prompt = ChatPromptTemplate.from_template(
        "Compare the annual revenues, net incomes, and growth rates of Apple (AAPL) and Microsoft (MSFT) "
        "based ONLY on this data table:\n{table}\n\nProvide a concise analysis of who is growing faster, "
        "revenue differences, and profit margins. Cite 'SQLite SEC database' as the source."
    )
    
    chain = prompt | llm | StrOutputParser()
    analysis = chain.invoke({"table": markdown_table})
    
    answer = f"### Apple vs Microsoft - Comparative Data\n\n{markdown_table}\n\n### Analytical Summary\n{analysis}"
    
    return {
        "answer": answer,
        "chart_data": chart_data,
        "sources": [
            {"content": "Structured Apple (AAPL) annual SEC facts.", "metadata": {"source": get_company_facts_source("AAPL")}},
            {"content": "Structured Microsoft (MSFT) annual SEC facts.", "metadata": {"source": get_company_facts_source("MSFT")}}
        ]
    }

def external_queries_node(state: AgentState) -> Dict[str, Any]:
    query = state["query"]
    source_url = os.getenv("EXTERNAL_DATA_URL")
    if not source_url:
        return {
            "answer": "This question requires an external consumer-data provider. Configure EXTERNAL_DATA_URL; SEC filings do not contain that evidence.",
            "sources": [],
            "chart_data": [],
        }
    try:
        response = requests.get(source_url, params={"q": query}, timeout=20)
        response.raise_for_status()
        consumer_facts = response.text[:12000]
    except requests.RequestException:
        logger.exception("External data request failed")
        return {"answer": "The configured external consumer-data provider is unavailable.", "sources": [], "chart_data": []}

    llm = _llm()
    prompt = ChatPromptTemplate.from_template(
        "You are a consumer research analyst. Answer the user question utilizing the following consumer data:\n\n"
        "{facts}\n\nQuestion: {question}\n\nFormat clearly, do not hallucinate, and cite the configured external source."
    )
    
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"facts": consumer_facts, "question": query})
    
    return {
        "answer": answer,
        "sources": [{"content": consumer_facts, "metadata": {"source": source_url}}],
        "chart_data": []
    }

# Build LangGraph workflow
def run_agent(query_str: str) -> Dict[str, Any]:
    load_dotenv()
    
    builder = StateGraph(AgentState)
    
    builder.add_node("router", router_node)
    builder.add_node("financial_analysis", financial_analysis_node)
    builder.add_node("filing_rag", filing_rag_node)
    builder.add_node("comparison", comparison_node)
    builder.add_node("external", external_queries_node)
    
    builder.set_entry_point("router")
    
    # Define conditional routing from router node
    def route_decision(state: AgentState) -> str:
        return state["route"]
        
    builder.add_conditional_edges(
        "router",
        route_decision,
        {
            "financial_analysis": "financial_analysis",
            "filing_rag": "filing_rag",
            "comparison": "comparison",
            "external": "external"
        }
    )
    
    builder.add_edge("financial_analysis", END)
    builder.add_edge("filing_rag", END)
    builder.add_edge("comparison", END)
    builder.add_edge("external", END)
    
    graph = builder.compile()
    
    initial_state = {
        "query": query_str,
        "ticker": "AAPL",
        "route": "",
        "answer": "",
        "sources": [],
        "chart_data": []
    }
    
    result = graph.invoke(initial_state)
    return result
