# app.py
import os
import uuid
import asyncio
import sqlite3
import pymupdf as fitz  # PyMuPDF
import pandas as pd
import streamlit as st
from agent.orchestrator import execute_real_mcp_pipeline, log_audit_event

st.set_page_config(page_title="UwaziBid Real MCP | The Bid Box Challenge", layout="wide")
DB_PATH = "data/uwazibid_real.db"
os.makedirs("data/uploaded_pdfs", exist_ok=True)

if not os.path.exists(DB_PATH):
    st.error("⚠️ Database not found! Please run `python ingest_real_data.py` first to download the live World Bank, AfDB, and PPRA datasets.")
    st.stop()

def create_sample_real_pdf(filename: str, kra_pin: str, director_id: str, validity_days: int) -> str:
    """Uses PyMuPDF to write a real binary PDF tender submission on disk for instant live testing."""
    path = os.path.join("data/uploaded_pdfs", filename)
    doc = fitz.open()
    page = doc.new_page()
    content = (
        f"REPUBLIC OF KENYA - STANDARD TENDER DOCUMENT\n"
        f"FORM OF TENDER & MANDATORY STATUTORY DECLARATIONS\n\n"
        f"1. Tax Compliance Certificate Status: Valid\n"
        f"   KRA PIN: {kra_pin}\n\n"
        f"2. CR12 Register of Directors & Beneficial Ownership:\n"
        f"   Lead Director National ID: ID-{director_id}\n\n"
        f"3. Tender Security & Validity Period:\n"
        f"   Bid Bond Amount: KES 650,000.00\n"
        f"   This tender shall remain valid for {validity_days} days from the date of tender opening.\n"
    )
    page.insert_text((50, 72), content, fontsize=11)
    doc.save(path)
    doc.close()
    return path

st.title("⚖️ UwaziBid MCP — Live Public Procurement Audit Engine")
st.caption("Zero-Mock Data Edition | Live World Bank & AfDB Sanctions + Kenya PPIP OCDS + Official PPRA Market Price Reference Guide")

# Sidebar: Live Database Telemetry
conn = sqlite3.connect(DB_PATH)
sanctions_count = conn.execute("SELECT COUNT(*) FROM real_sanctions_watchlist").fetchone()[0]
ppip_count = conn.execute("SELECT COUNT(*) FROM ppip_real_tenders").fetchone()[0]
ppra_items_df = pd.read_sql_query("SELECT item_code, item_description, geometric_mean_kes FROM ppra_market_price_index", conn)
sample_debarred_df = pd.read_sql_query("SELECT name, countries, dataset_source FROM real_sanctions_watchlist WHERE countries LIKE '%KE%' OR countries LIKE '%NG%' OR countries LIKE '%ZA%' LIMIT 15", conn)
conn.close()

with st.sidebar:
    st.header("📡 Live Ingested Data Telemetry")
    st.metric("Real World Bank & AfDB Debarred Entities", f"{sanctions_count:,}")
    st.metric("Kenya PPIP OCDS Tender Releases", f"{ppip_count:,}")
    st.metric("PPRA Market Price Index Items", len(ppra_items_df))
    st.divider()
    st.markdown("**🔍 Sample Real African Debarred Entities in DB (Click to test):**")
    st.dataframe(sample_debarred_df, width="stretch", hide_index=True)
    evaluator_officer = st.text_input("👤 Committee Officer", value="Collistus Kibe (Chair, Evaluation Committee)")

st.subheader("1️⃣ Submit Real Tender PDF & Financial Quote to MCP Pipeline")

col_left, col_right = st.columns(2)
with col_left:
    uploaded_pdf = st.file_uploader("📄 Upload a Real Tender PDF Document (Parsed live via PyMuPDF)", type=["pdf"])
    bidder_name = st.text_input("🏢 Bidding Company Name", value="Ngong Hills ICT Solutions Ltd")
    director_or_entity_to_screen = st.text_input(
        "🔎 Company or Director Name to Screen against World Bank / AfDB Sanctions",
        value="Westair ACS JV (PTY) LTD",
        help="Try typing any real debarred entity from the sidebar table (e.g., 'Westair ACS JV (PTY) LTD' or 'United Aviation Services Limited') or a clean name."
    )

with col_right:
    item_options = {f"{r['item_code']} — {r['item_description']} (PPRA GeoMean: KES {r['geometric_mean_kes']:,.0f})": r["item_code"] for _, r in ppra_items_df.iterrows()}
    selected_item_label = st.selectbox("📦 Select PPRA Market Price Reference Item", list(item_options.keys()))
    selected_item_code = item_options[selected_item_label]

    c_curr, c_price, c_town = st.columns(3)
    currency = c_curr.selectbox("Currency", ["KES", "USD", "EUR"])
    quoted_price = c_price.number_input("Quoted Unit Price", min_value=1.0, value=140000.0, step=1000.0)
    survey_town = c_town.selectbox("Procuring Entity Town", ["Nairobi", "Mombasa", "Kisumu", "Nakuru", "Nyeri"])

if st.button("🚀 Execute Real MCP Server Pipeline (stdio JSON-RPC)", type="primary"):
    session_id = str(uuid.uuid4())[:8]
    if uploaded_pdf is not None:
        pdf_path = os.path.join("data/uploaded_pdfs", uploaded_pdf.name)
        with open(pdf_path, "wb") as f:
            f.write(uploaded_pdf.getbuffer())
    else:
        # Generates an actual binary PDF on disk if the user didn't drag-and-drop one yet
        pdf_path = create_sample_real_pdf("Submitted_Bid_Form.pdf", "P051882910K", "30119284", 180)

    with st.spinner("Spawning Custom FastMCP Server + External SQLite MCP Server over stdio..."):
        result = asyncio.run(
            execute_real_mcp_pipeline(
                session_id=session_id,
                pdf_path=pdf_path,
                bidder_name=bidder_name,
                director_name_to_screen=director_or_entity_to_screen,
                item_code=selected_item_code,
                quoted_unit_price=quoted_price,
                currency=currency,
                town=survey_town
            )
        )
        st.session_state["last_mcp_run"] = result

if "last_mcp_run" in st.session_state:
    res = st.session_state["last_mcp_run"]
    st.divider()
    st.subheader("2️⃣ MCP Tool Outputs & Open-Weights LLM Brief")
    st.info(res["ai_executive_memo"])

    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown("#### 📄 Tool 1: PyMuPDF Statutory Audit")
        st.json(res["pdf_report"])
    with m2:
        st.markdown("#### 🚨 Tool 2: Live World Bank/AfDB Watchlist")
        st.json(res["sanction_report"])
    with m3:
        st.markdown("#### 📊 Tool 3: PPRA Market Price Index")
        st.json(res["price_report"])

    st.divider()
    st.subheader("3️⃣ Mandatory Human-in-the-Loop Committee Sign-Off (PPADA Sec 46)")
    with st.form("hitl_real_form"):
        st.markdown(f"**Agent Non-Binding Recommendation:** `{res['ai_recommendation_short']}`")
        human_action = st.radio(
            "Select Binding Committee Decision:",
            [
                "APPROVE Agent Recommendation",
                "OVERRIDE — Qualify Bidder (Attach Statutory Justification)",
                "OVERRIDE — Disqualify Bidder",
                "HOLD — Request Written Clarification under PPADA Sec 81"
            ]
        )
        notes = st.text_area(
            "Mandatory Statutory Justification (Recorded in Immutable Audit Log):",
            value="Reviewed PyMuPDF SHA-256 extraction, World Bank/AfDB watchlist screening, and PPRA Geometric Mean benchmark."
        )
        if st.form_submit_button("✍️ Commit Human Decision to SQLite Audit Trail"):
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                """
                INSERT INTO hitl_committee_decisions
                (timestamp, tender_ref, document_filename, document_sha256, extracted_kra_pin, ai_recommendation, human_decision, committee_officer, statutory_justification)
                VALUES (datetime('now'), 'LIVE-TENDER-EVAL', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    res["pdf_report"].get("filename", ""),
                    res["pdf_report"].get("document_sha256", ""),
                    ",".join(res["pdf_report"].get("extracted_kra_pins", [])),
                    res["ai_recommendation_short"],
                    human_action,
                    evaluator_officer,
                    notes
                )
            )
            conn.commit()
            conn.close()
            st.success("✅ Human-in-the-Loop decision committed to SQLite!")

st.divider()
st.subheader("4️⃣ Live Protocol Audit Trail (`mcp_audit_log` & `hitl_committee_decisions`)")
conn = sqlite3.connect(DB_PATH)
c_log1, c_log2 = st.columns(2)
with c_log1:
    st.markdown("**MCP Server & External Tool Invocations**")
    st.dataframe(pd.read_sql_query("SELECT id, timestamp, mcp_server_name, tool_name, output_summary FROM mcp_audit_log ORDER BY id DESC LIMIT 15", conn), width="stretch")
with c_log2:
    st.markdown("**Human Committee Sign-Offs**")
    st.dataframe(pd.read_sql_query("SELECT id, timestamp, document_filename, ai_recommendation, human_decision, committee_officer FROM hitl_committee_decisions ORDER BY id DESC LIMIT 15", conn), width="stretch")
conn.close()