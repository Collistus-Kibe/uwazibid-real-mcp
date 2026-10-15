# ⚖️ UwaziBid Real MCP — The Bid Box Challenge
> **African Agentic AI Design Challenge (Governance Track)**  
> **Zero-Mock Public Procurement Pre-Auditor built on Model Context Protocol (MCP)**

---

## 📌 Executive Summary

Public procurement in Kenya accounts for an estimated **10–13% of GDP**, yet manual Tender Evaluation Committees face severe compliance bottlenecks:
1. **Preliminary statutory violations** (tax registration under PPADA Section 55(1)(f), missing CR12 beneficial ownership disclosures, invalid tender validity periods).
2. **Debarred entities & corrupt directors** slipping through due to unlinked watchlists across development finance institutions (World Bank, AfDB).
3. **Severe price gouging & inflation** bypassing market price benchmarks (PPADA Section 54(1) & Regulation 43(4)).

**UwaziBid Real MCP** resolves these systemic failures through a **multi-server Model Context Protocol (MCP) architecture** communicating over **stdio JSON-RPC**, combined with open-weights LLMs (**Gemma 4 31B/26B**, **Gemini 2.5 Flash**, **GPT-4o Mini**) and a strict **Human-in-the-Loop (HITL) Tender Evaluation Committee sign-off gate**.

---

## 🏗️ Architecture & Component Flow

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Streamlit HITL Portal (app.py)                  │
│               - Live PDF Upload with SHA-256 Evidentiary Hash          │
│               - Real Debarment Watchlist Telemetry                     │
│               - PPRA MPI Interactive Economic Comparator               │
│               - Statutory Committee Decision Binding Sign-Off          │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       Agent Orchestrator (orchestrator.py)             │
│            - Spawns Stdio MCP Clients (Custom FastMCP + SQLite MCP)    │
│            - Multi-Key Rotation: Gemini (2.5 Flash) & Gemma Open-Weights│
│            - OpenAI Secondary Fallback (Key 1 & Key 2)                 │
│            - SHA-256 & UTC Audit Logger to SQLite                      │
└───────────────┬──────────────────────────────────────┬─────────────────┘
                │ (stdio JSON-RPC)                     │ (stdio JSON-RPC)
                ▼                                      ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│  Custom FastMCP Server               │  │  External Reference Server    │
│  (mcp_servers/uwazibid_server.py)    │  │  (mcp-server-sqlite)         │
│  1. extract_and_verify_tender_pdf    │  │  - Independent DB queries    │
│  2. screen_entity_against_sanctions  │  │  - Zero trust verification   │
│  3. benchmark_price_against_ppra_mpi │  └──────────────┬───────────────┘
│  4. cross_check_bid_box_collusion    │                 │
└──────────────────┬───────────────────┘                 │
                   │                                     │
                   ▼                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                  data/uwazibid_real.db (Zero Mock Records)             │
│  - real_sanctions_watchlist : 1,517 live World Bank & AfDB debarments   │
│  - ppip_real_tenders        : 250 live OCDS releases (tenders.go.ke)   │
│  - ppra_market_price_index  : PPRA Reference Guide Geometric Means     │
│  - mcp_audit_log            : Cryptographic SHA-256 tool invocation log │
│  - hitl_committee_decisions : Binding human statutory sign-offs        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ The 4 MCP Tools (Custom FastMCP Server)

| Tool Name | Protocol Signature | Kenya Statutory Anchor | Description |
|---|---|---|---|
| `extract_and_verify_tender_pdf` | `pdf_path: str, req_security_kes: float, req_validity: int` | PPADA 2015 Sec 55, 61, 79 | Parses binary PDFs via PyMuPDF, computes SHA-256 hash, extracts KRA PINs, CR12 Director IDs, validity days, checks Tax Compliance Certificates. |
| `screen_entity_against_live_sanctions` | `entity_or_director_name: str, threshold: float` | PPADA 2015 Sec 41 & Sec 55(1)(e) | RapidFuzz token matching against 1,500+ real World Bank & African Development Bank debarment records. |
| `benchmark_price_against_ppra_mpi` | `item_code: str, quoted_unit_price: float, currency: str, town: str` | PPADA 2015 Sec 54(1) & Reg 43(4) | Compares quotes against official PPRA Geometric Means across 5 Kenyan survey towns (Nairobi, Mombasa, Kisumu, Nakuru, Nyeri), flags quotes exceeding 30% CV corridor, and normalizes foreign currencies via live exchange rate APIs. |
| `cross_check_bid_box_collusion` | `bids_payload_json: str` | PPADA 2015 Sec 66(3)(a) | Cross-examines competing bids for overlapping beneficial ownership (shared CR12 Director IDs & KRA PINs) to prevent price-fixing rings. |

---

## 📊 Real Datasets Ingested (Zero Mock Data)

Run `python ingest_real_data.py` to populate `data/uwazibid_real.db`:
1. **World Bank Debarred Firms**: Live CSV pulled from OpenSanctions (`targets.simple.csv`).
2. **African Development Bank (AfDB) Debarred Entities**: Live CSV pulled from OpenSanctions.
3. **Kenya Public Procurement Information Portal (PPIP)**: Live OCDS JSON tender releases from `tenders.go.ke`.
4. **Official PPRA Market Price Reference Guide (2025/2026)**: ICT equipment, stationery, construction, and medical commodities computed via exact PPRA Geometric Mean formula:
   $$\text{GeoMean} = \exp\left(\frac{1}{n}\sum_{i=1}^{n}\ln(P_i)\right)$$

---

## 🔑 Multi-Key Rotation & Open-Weights Model Matrix

Configure your keys in `.env` (or copy from `.env.example`):

```bash
# Gemini & Open-Weights Gemma (Automatic key failover)
GEMINI_API_KEY_1=your_gemini_key_1
GEMINI_API_KEY_2=your_gemini_key_2
GEMINI_MODEL=gemini-2.5-flash
GEMMA_OPEN_WEIGHTS_MODEL=gemma-4-31b-it

# OpenAI Fallback
OPENAI_API_KEY_1=your_openai_key_1
OPENAI_API_KEY_2=your_openai_key_2
OPENAI_MODEL=gpt-4o-mini
```

### Rotation Order:
1. **Open-Weights Gemma (`gemma-4-31b-it`, `gemma-4-26b-a4b-it`)** via Key 1 & Key 2 (satisfies Open-Weights Challenge requirement).
2. **Google Gemini (`gemini-2.5-flash`)** via Key 1 & Key 2.
3. **OpenAI (`gpt-4o-mini`)** via Key 1 & Key 2.
4. **Deterministic Statutory Rule Engine** (guarantees offline availability).

---

## 🚀 Quickstart Guide

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Ingest Real Data
```bash
python ingest_real_data.py
```

### 3. Launch the Application
```bash
streamlit run app.py
```
Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## 🛡️ Human-in-the-Loop (HITL) Governance

Under **Section 46 of the Public Procurement and Asset Disposal Act (PPADA 2015)**, an automated AI agent **cannot legally disqualify a bidder**. 

UwaziBid strictly observes this mandate:
- **Agent Output:** Produces non-binding, evidentiary synthesis memos citing statutory clauses and SHA-256 hashes.
- **Committee Gate:** The Tender Evaluation Committee must explicitly **Approve**, **Override**, or **Hold for Clarification (Sec 81)** with a mandatory statutory justification.
- **Audit Trail:** Every action is committed to `mcp_audit_log` and `hitl_committee_decisions` tables in SQLite.
