# agent/audit_logger.py
import os
import json
import sqlite3
import hashlib
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "uwazibid_real.db")


def ensure_audit_tables():
    """Ensures the audit and HITL tables exist even if called standalone."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS mcp_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        session_id TEXT,
        event_type TEXT,
        mcp_server_name TEXT,
        tool_name TEXT,
        input_payload TEXT,
        output_summary TEXT,
        source_citations TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS hitl_committee_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        tender_ref TEXT,
        document_filename TEXT,
        document_sha256 TEXT,
        extracted_kra_pin TEXT,
        ai_recommendation TEXT,
        human_decision TEXT,
        committee_officer TEXT,
        statutory_justification TEXT
    )
    """)
    conn.commit()
    conn.close()


def log_audit_event(
    session_id: str,
    event_type: str,
    server_name: str,
    tool_name: str,
    input_data: dict,
    output_summary: str,
    citations: list[str]
) -> int:
    """
    Logs an immutable protocol-level MCP tool call, external server query,
    or open-weights LLM synthesis step into SQLite.
    """
    ensure_audit_tables()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    cur.execute(
        """
        INSERT INTO mcp_audit_log
        (timestamp, session_id, event_type, mcp_server_name, tool_name, input_payload, output_summary, source_citations)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_utc,
            session_id,
            event_type,
            server_name,
            tool_name,
            json.dumps(input_data, ensure_ascii=False),
            str(output_summary)[:600],
            json.dumps(citations, ensure_ascii=False)
        )
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def record_hitl_committee_decision(
    session_id: str,
    tender_ref: str,
    document_filename: str,
    document_sha256: str,
    extracted_kra_pins: list[str],
    ai_recommendation: str,
    human_decision: str,
    committee_officer: str,
    statutory_justification: str
) -> int:
    """
    Records the mandatory Human-in-the-Loop (HITL) Tender Evaluation Committee
    decision in both `hitl_committee_decisions` and the master `mcp_audit_log`.
    """
    ensure_audit_tables()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    kra_str = ", ".join(extracted_kra_pins) if extracted_kra_pins else "NONE_DETECTED"

    cur.execute(
        """
        INSERT INTO hitl_committee_decisions
        (timestamp, tender_ref, document_filename, document_sha256, extracted_kra_pin,
         ai_recommendation, human_decision, committee_officer, statutory_justification)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_utc,
            tender_ref,
            document_filename,
            document_sha256,
            kra_str,
            ai_recommendation,
            human_decision,
            committee_officer,
            statutory_justification
        )
    )
    decision_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Mirror the human decision into the master MCP audit log for complete traceability
    log_audit_event(
        session_id=session_id,
        event_type="HITL_COMMITTEE_SIGN_OFF",
        server_name="human-evaluation-committee",
        tool_name="record_hitl_committee_decision",
        input_data={
            "tender_ref": tender_ref,
            "document_filename": document_filename,
            "document_sha256": document_sha256,
            "ai_recommendation": ai_recommendation
        },
        output_summary=f"Officer: {committee_officer} | Decision: {human_decision} | Note: {statutory_justification}",
        citations=["Kenya PPADA 2015 Sec 46 (Evaluation Committee Responsibility)"]
    )
    return decision_id