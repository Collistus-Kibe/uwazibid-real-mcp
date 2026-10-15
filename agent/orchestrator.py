# agent/orchestrator.py
import os
import sys
import json
import sqlite3
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
from openai import OpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from agent.audit_logger import log_audit_event

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "uwazibid_real.db")
CUSTOM_SERVER_SCRIPT = os.path.join(BASE_DIR, "mcp_servers", "uwazibid_server.py")


async def execute_real_mcp_pipeline(
    session_id: str,
    pdf_path: str,
    bidder_name: str,
    director_name_to_screen: str,
    item_code: str,
    quoted_unit_price: float,
    currency: str = "KES",
    town: str = "Nairobi"
) -> dict:
    """
    Connects over real MCP stdio transport to:
      1) Custom Server: uwazibid_server.py (FastMCP)
      2) External Pre-Existing Server: mcp-server-sqlite
    Executes the tools, logs every step to SQLite audit trail,
    and synthesizes the recommendation with multi-key Gemini / Gemma / OpenAI LLMs.
    """
    custom_server_params = StdioServerParameters(
        command=sys.executable,
        args=[CUSTOM_SERVER_SCRIPT]
    )

    # 1. Connect to YOUR Custom MCP Server via real JSON-RPC stdio
    async with stdio_client(custom_server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as custom_session:
            await custom_session.initialize()

            # Discover tools dynamically via MCP protocol
            tools_list = await custom_session.list_tools()
            discovered_tool_names = [t.name for t in tools_list.tools]

            # Call Tool 1: Real PDF Extraction & Statutory Check
            pdf_res_raw = await custom_session.call_tool(
                "extract_and_verify_tender_pdf",
                arguments={"pdf_path": pdf_path}
            )
            pdf_report = json.loads(pdf_res_raw.content[0].text)
            log_audit_event(
                session_id, "MCP_TOOL_CALL", "uwazibid-custom-mcp", "extract_and_verify_tender_pdf",
                {"pdf_path": os.path.basename(pdf_path)},
                f"SHA256: {pdf_report.get('document_sha256', '')[:16]}... | Compliant: {pdf_report.get('preliminary_compliant')}",
                pdf_report.get("statutory_citations", [])
            )

            # Call Tool 2: Live World Bank / AfDB Sanctions Screening
            sanction_res_raw = await custom_session.call_tool(
                "screen_entity_against_live_sanctions",
                arguments={"entity_or_director_name": director_name_to_screen}
            )
            sanction_report = json.loads(sanction_res_raw.content[0].text)
            log_audit_event(
                session_id, "MCP_TOOL_CALL", "uwazibid-custom-mcp", "screen_entity_against_live_sanctions",
                {"entity_or_director_name": director_name_to_screen},
                f"Scanned {sanction_report.get('total_watchlist_records_scanned')} real records | Flagged: {sanction_report.get('is_flagged')}",
                ["World Bank Debarred Firms (OpenSanctions)", "AfDB Sanctions List", "PPADA Sec 41"]
            )

            # Call Tool 3: PPRA Market Price Index Benchmark
            price_res_raw = await custom_session.call_tool(
                "benchmark_price_against_ppra_mpi",
                arguments={
                    "item_code": item_code,
                    "quoted_unit_price": quoted_unit_price,
                    "currency": currency,
                    "town": town
                }
            )
            price_report = json.loads(price_res_raw.content[0].text)
            log_audit_event(
                session_id, "MCP_TOOL_CALL", "uwazibid-custom-mcp", "benchmark_price_against_ppra_mpi",
                {"item_code": item_code, "quoted_unit_price": quoted_unit_price, "currency": currency},
                f"Normalized KES {price_report.get('normalized_quoted_kes')} vs PPRA GeoMean KES {price_report.get('ppra_national_geometric_mean_kes')} ({price_report.get('deviation_from_geometric_mean_pct')}%)",
                [price_report.get("citation", "PPRA MPI"), price_report.get("fx_source", "")]
            )

    # 2. Connect to EXTERNAL Pre-Existing MCP Server (mcp-server-sqlite)
    external_sqlite_output = []
    try:
        ext_params = StdioServerParameters(
            command="mcp-server-sqlite",
            args=["--db-path", DB_PATH]
        )
        async with stdio_client(ext_params) as (ext_r, ext_w):
            async with ClientSession(ext_r, ext_w) as ext_session:
                await ext_session.initialize()
                sql_res = await ext_session.call_tool(
                    "read_query",
                    arguments={"query": "SELECT COUNT(*) as total_sanctions FROM real_sanctions_watchlist"}
                )
                external_sqlite_output = [c.text for c in sql_res.content]
                log_audit_event(
                    session_id, "EXTERNAL_MCP_TOOL_CALL", "mcp-server-sqlite (External)", "read_query",
                    {"query": "SELECT COUNT(*) FROM real_sanctions_watchlist"},
                    str(external_sqlite_output),
                    ["Official External MCP Server: mcp-server-sqlite"]
                )
    except Exception as ext_err:
        # Fallback check if external binary is not reachable
        conn = sqlite3.connect(DB_PATH)
        cnt = conn.execute("SELECT COUNT(*) FROM real_sanctions_watchlist").fetchone()[0]
        conn.close()
        external_sqlite_output = [f"Verified {cnt:,} live watchlist records directly in SQLite"]

    # 3. Synthesize with LLM supporting Gemini/Gemma rotation & OpenAI fallback
    ai_memo, ai_short_rec = synthesize_with_llm(
        session_id, bidder_name, pdf_report, sanction_report, price_report
    )

    return {
        "discovered_mcp_tools": discovered_tool_names,
        "external_mcp_verification": external_sqlite_output,
        "pdf_report": pdf_report,
        "sanction_report": sanction_report,
        "price_report": price_report,
        "ai_recommendation_short": ai_short_rec,
        "ai_executive_memo": ai_memo
    }


def synthesize_with_llm(session_id: str, bidder_name: str, pdf_rep: dict, sanc_rep: dict, price_rep: dict) -> tuple[str, str]:
    """
    Synthesizes audit findings for the Tender Evaluation Committee.
    Rotates across:
      1. Gemini API Keys (GEMINI_API_KEY_1, GEMINI_API_KEY_2) with `gemini-2.5-flash` or open-weights `gemma-3-27b-it`
      2. OpenAI API Keys (OPENAI_API_KEY_1, OPENAI_API_KEY_2) with `gpt-4o-mini`
      3. Groq API Key (Llama 3.3) or local Ollama
      4. Deterministic Statutory Rule Engine (Fallback)
    """
    # Deterministic statutory baseline
    if not pdf_rep.get("preliminary_compliant", False):
        short_rec = "RECOMMEND DISQUALIFICATION (Preliminary Statutory Non-Compliance - PPADA Sec 55/79)"
    elif sanc_rep.get("is_flagged", False):
        short_rec = "RECOMMEND DISQUALIFICATION (Debarred Entity / Director Hit - PPADA Sec 41)"
    elif price_rep.get("exceeds_ppra_30pct_cv_corridor", False):
        short_rec = "RECOMMEND FINANCIAL REJECTION (Price Exceeds PPRA 30% CV Corridor - PPADA Sec 54)"
    else:
        short_rec = "RECOMMEND QUALIFICATION (Compliant & Within PPRA Market Price Corridor)"

    evidence_bundle = {
        "bidder_name": bidder_name,
        "pdf_audit": pdf_rep,
        "live_sanctions_screening": sanc_rep,
        "ppra_price_benchmark": price_rep,
        "recommended_status": short_rec
    }

    sys_msg = (
        "You are UwaziBid, an open-weights public procurement audit agent built for Kenyan public institutions under the PPADA 2015. "
        "You NEVER make a final decision. You present sourced, verifiable evidence from the MCP tools so the human Tender Evaluation Committee can decide."
    )
    user_msg = (
        f"Write a structured 4-bullet evaluation brief for the Tender Evaluation Committee regarding bidder '{bidder_name}'. "
        f"Cite the exact SHA-256 hash, KRA PIN findings, World Bank/AfDB watchlist scan count, and PPRA Geometric Mean price comparison:\n\n"
        f"{json.dumps(evidence_bundle, indent=2)}"
    )

    # Build candidates list: (provider_type, base_url, api_key, model_name, display_tag)
    candidates = []

    gemini_keys = [
        k for k in [os.getenv("GEMINI_API_KEY_1", "").strip(), os.getenv("GEMINI_API_KEY_2", "").strip()]
        if k
    ]
    # Check if legacy GEMINI_API_KEY is present
    if not gemini_keys and os.getenv("GEMINI_API_KEY", "").strip():
        gemini_keys.append(os.getenv("GEMINI_API_KEY", "").strip())

    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    gemma_model = os.getenv("GEMMA_OPEN_WEIGHTS_MODEL", "gemma-4-31b-it").strip()

    for idx, key in enumerate(gemini_keys, start=1):
        # 1. Open-Weights Gemma via Google OpenAI-compatible endpoint
        candidates.append((
            "gemini_gemma",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            key,
            gemma_model,
            f"Open-Weights Gemma (Key #{idx}: {gemma_model})"
        ))
        # Also try gemma-4-26b-a4b-it as secondary open-weights model
        candidates.append((
            "gemini_gemma_alt",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            key,
            "gemma-4-26b-a4b-it",
            f"Open-Weights Gemma (Key #{idx}: gemma-4-26b-a4b-it)"
        ))
        # 2. Gemini 2.5 Flash via Google OpenAI-compatible endpoint
        candidates.append((
            "gemini_flash",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            key,
            gemini_model,
            f"Google Gemini (Key #{idx}: {gemini_model})"
        ))

    # OpenAI Keys
    openai_keys = [
        k for k in [os.getenv("OPENAI_API_KEY_1", "").strip(), os.getenv("OPENAI_API_KEY_2", "").strip()]
        if k
    ]
    if not openai_keys and os.getenv("OPENAI_API_KEY", "").strip():
        openai_keys.append(os.getenv("OPENAI_API_KEY", "").strip())

    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    for idx, key in enumerate(openai_keys, start=1):
        candidates.append((
            "openai",
            "https://api.openai.com/v1",
            key,
            openai_model,
            f"OpenAI (Key #{idx}: {openai_model})"
        ))

    # Groq fallback
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        groq_model = os.getenv("OPEN_WEIGHTS_MODEL", "llama-3.3-70b-versatile").strip()
        candidates.append((
            "groq",
            "https://api.groq.com/openai/v1",
            groq_key,
            groq_model,
            f"Open-Weights Groq ({groq_model})"
        ))

    # Local Ollama fallback
    if os.getenv("LLM_PROVIDER", "").lower() == "ollama":
        ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip()
        candidates.append((
            "ollama",
            "http://localhost:11434/v1",
            "ollama",
            ollama_model,
            f"Local Ollama ({ollama_model})"
        ))

    # Attempt execution across candidate chain
    for kind, base_url, api_key, model, tag in candidates:
        try:
            client = OpenAI(base_url=base_url, api_key=api_key)
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_msg}
                ],
                temperature=0.1,
                max_tokens=600
            )
            memo_body = completion.choices[0].message.content or ""
            # Strip internal <thought> tags if returned by reasoning models
            import re
            memo_body = re.sub(r"<thought>.*?</thought>", "", memo_body, flags=re.DOTALL).strip()
            memo = f"**[Synthesized by {tag}]**\n\n{memo_body}"
            log_audit_event(
                session_id, "LLM_EVIDENCE_SYNTHESIS", model, "chat.completions",
                {"bidder": bidder_name, "provider_tag": tag},
                memo[:300], ["PPADA 2015", "PPRA MPI", tag]
            )
            return memo, short_rec
        except Exception as err:
            print(f"[Orchestrator LLM Rotation] Warning: Call failed with {tag}: {err}")
            continue

    # Deterministic fallback when no API keys are provided or all endpoints are unreachable
    fallback = (
        f"**[Deterministic Statutory Synthesis — `{short_rec}`]**\n\n"
        f"- **Document Chain-of-Custody:** Parsed `{pdf_rep.get('filename')}` (`SHA-256: {pdf_rep.get('document_sha256', '')[:20]}...`, {pdf_rep.get('page_count')} pages). Extracted KRA PINs: `{pdf_rep.get('extracted_kra_pins')}`.\n"
        f"- **Debarment & Sanctions Screening:** Cross-checked `{sanc_rep.get('queried_name')}` against **{sanc_rep.get('total_watchlist_records_scanned', 0):,} real World Bank & AfDB debarred entities**. Flagged hits: `{len(sanc_rep.get('matches', []))}`.\n"
        f"- **PPRA Market Price Index Benchmark:** Quoted `{price_rep.get('quoted_original')}` (KES `{price_rep.get('normalized_quoted_kes', 0):,.2f}`) vs PPRA National Geometric Mean of **KES `{price_rep.get('ppra_national_geometric_mean_kes', 0):,.2f}`** (`{price_rep.get('deviation_from_geometric_mean_pct', 0)}%` deviation).\n"
        f"- **Human-in-the-Loop Mandate:** Committee Officer must review and sign off under PPADA Section 46 below."
    )
    log_audit_event(
        session_id, "DETERMINISTIC_SYNTHESIS_FALLBACK", "rules-engine", "statutory_synthesizer",
        {"bidder": bidder_name}, fallback[:300], ["PPADA 2015 Sec 46", "PPRA MPI"]
    )
    return fallback, short_rec