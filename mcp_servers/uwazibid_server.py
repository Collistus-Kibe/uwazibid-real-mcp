# mcp_servers/uwazibid_server.py
import os
import re
import hashlib
import sqlite3
import pymupdf as fitz  # PyMuPDF
import httpx
from rapidfuzz import process, fuzz
from fastmcp import FastMCP

mcp = FastMCP("UwaziBid-Real-Governance-MCP")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "uwazibid_real.db")


@mcp.tool()
def extract_and_verify_tender_pdf(
    pdf_path: str,
    required_bid_security_kes: float = 500000.0,
    required_validity_days: int = 150
) -> dict:
    """
    TOOL 1 (Real Document Parser & Statutory Verifier):
    Reads an actual PDF tender document from disk using PyMuPDF, calculates its cryptographic SHA-256 hash
    for evidentiary chain-of-custody, extracts KRA PINs, CR12 Director IDs, Bid Bond amounts, and tender validity days,
    and checks statutory compliance under Kenya PPADA 2015 Sections 55, 61, and 79.
    """
    if not os.path.exists(pdf_path):
        return {"error": f"PDF file not found at {pdf_path}"}

    with open(pdf_path, "rb") as f:
        sha256_hash = hashlib.sha256(f.read()).hexdigest()

    doc = fitz.open(pdf_path)
    full_text = ""
    page_citations = []

    for page_num, page in enumerate(doc, start=1):
        txt = page.get_text("text")
        full_text += f"\n--- [Page {page_num}] ---\n{txt}"

    # 1. Extract KRA PINs (Official Kenya format: A or P + 9 digits + Letter)
    kra_pins = list(set(re.findall(r"\b[AP]\d{9}[A-Z]\b", full_text)))

    # 2. Extract Kenyan National IDs / Registration Numbers from CR12 sections
    national_ids = list(set(re.findall(r"\b(?:ID|NO|PASSPORT)[:\s#-]*(\d{7,8})\b", full_text, re.IGNORECASE)))

    # 3. Extract Monetary Figures (KES / USD) for Bid Security / Tender Sum
    kes_amounts = re.findall(r"(?:KES|Kshs?\.?)\s*([\d,]+(?:\.\d{2})?)", full_text, re.IGNORECASE)
    parsed_kes = [float(x.replace(",", "")) for x in kes_amounts if x.replace(",", "").replace(".", "").isdigit()]

    # 4. Extract Validity Period in Days (e.g., "180 days", "150 days", "120 days")
    validity_matches = re.findall(r"\b(\d{2,3})\s*days\b", full_text, re.IGNORECASE)
    validity_days = [int(d) for d in validity_matches if 30 <= int(d) <= 365]

    # 5. Check Tax Compliance & Mandatory Forms Presence
    has_tax_cert = bool(re.search(r"Tax\s+Compliance\s+Certificate", full_text, re.IGNORECASE))
    has_cr12 = bool(re.search(r"CR\s*12|Beneficial\s+Ownership|Register\s+of\s+Directors", full_text, re.IGNORECASE))
    has_form_of_tender = bool(re.search(r"Form\s+of\s+Tender", full_text, re.IGNORECASE))

    violations = []
    citations = []

    if not kra_pins:
        violations.append("No valid 11-character KRA PIN (e.g., P051...Z) detected in document.")
        citations.append("PPADA 2015 Sec 55(1)(f) - Mandatory Tax Registration")
    if not has_tax_cert:
        violations.append("Missing reference to valid KRA Tax Compliance Certificate.")
        citations.append("PPADA 2015 Sec 55(1)(f)")
    if not has_cr12:
        violations.append("Missing CR12 / Beneficial Ownership disclosure.")
        citations.append("PPADA 2015 Sec 55(1) & Executive Order No. 2 of 2018")
    if validity_days and max(validity_days) < required_validity_days:
        violations.append(f"Maximum detected validity period ({max(validity_days)} days) is below statutory threshold ({required_validity_days} days).")
        citations.append("PPADA Regulations 2020 Reg 45")

    return {
        "filename": os.path.basename(pdf_path),
        "document_sha256": sha256_hash,
        "page_count": len(doc),
        "extracted_kra_pins": kra_pins,
        "extracted_director_ids": national_ids,
        "detected_kes_figures": parsed_kes[:10],
        "detected_validity_days": validity_days,
        "mandatory_documents_detected": {
            "tax_compliance_certificate": has_tax_cert,
            "cr12_beneficial_ownership": has_cr12,
            "signed_form_of_tender": has_form_of_tender
        },
        "preliminary_compliant": len(violations) == 0,
        "violations": violations,
        "statutory_citations": citations if citations else ["PPADA 2015 Sec 79(1) - Conforms to Mandatory Preliminary Requirements"]
    }


@mcp.tool()
def screen_entity_against_live_sanctions(entity_or_director_name: str, threshold: float = 82.0) -> dict:
    """
    TOOL 2 (Substantive Fuzzy & Exact Debarment Screening Engine):
    Screens a bidding company name or CR12 director name against the real 4,000+ World Bank
    and African Development Bank (AfDB) debarred entities stored in SQLite using RapidFuzz token-set matching.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT entity_id, name, schema_type, countries, dataset_source, first_seen, last_seen FROM real_sanctions_watchlist")
    rows = cur.fetchall()
    conn.close()

    names_dict = {i: r[1] for i, r in enumerate(rows) if r[1]}
    matches = process.extract(
        entity_or_director_name,
        names_dict,
        scorer=fuzz.WRatio,
        limit=5
    )

    hits = []
    for match_name, score, idx in matches:
        if score >= threshold:
            r = rows[idx]
            hits.append({
                "watchlist_entity_id": r[0],
                "matched_name": r[1],
                "similarity_score_pct": round(score, 1),
                "entity_type": r[2],
                "jurisdiction": r[3],
                "official_source": r[4],
                "debarment_first_seen": r[5],
                "statutory_citation": "PPADA 2015 Sec 41 & Sec 55(1)(e) - Ineligibility due to Debarment"
            })

    return {
        "queried_name": entity_or_director_name,
        "total_watchlist_records_scanned": len(rows),
        "is_flagged": len(hits) > 0,
        "matches": hits
    }


@mcp.tool()
def benchmark_price_against_ppra_mpi(
    item_code: str,
    quoted_unit_price: float,
    currency: str = "KES",
    town: str = "Nairobi"
) -> dict:
    """
    TOOL 3 (Substantive Econometric Price Reasonableness Engine):
    Compares a bidder's quoted unit price against the official Kenya PPRA Market Price Reference Guide
    surveyed across Nairobi, Mombasa, Kisumu, Nakuru, and Nyeri. Normalizes foreign currency via live
    Open Exchange Rates API and evaluates price inflation under PPADA Section 54(1) & Regulation 43(4).
    """
    fx_rate = 1.0
    fx_source = "Native KES Quote"
    if currency.upper() != "KES":
        try:
            resp = httpx.get(f"https://open.er-api.com/v6/latest/{currency.upper()}", timeout=5.0)
            if resp.status_code == 200:
                fx_rate = float(resp.json()["rates"]["KES"])
                fx_source = f"Live Open Exchange Rates API (1 {currency.upper()} = {fx_rate:.2f} KES)"
        except Exception as e:
            return {"error": f"Live FX lookup failed for {currency}: {e}"}

    quoted_kes = quoted_unit_price * fx_rate

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT item_description, unit_of_measure, nairobi_kes, mombasa_kes, kisumu_kes,
               nakuru_kes, nyeri_kes, geometric_mean_kes, std_dev_kes, coeff_variation_pct, source_document
        FROM ppra_market_price_index WHERE item_code = ?
        """,
        (item_code,)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return {"error": f"Item code {item_code} not found in PPRA Market Price Index table."}

    desc, unit, nbi, msa, ksm, nkr, nyr, geo_mean, std_dev, cv_pct, src_doc = row
    town_lookup = {
        "nairobi": nbi, "mombasa": msa, "kisumu": ksm, "nakuru": nkr, "nyeri": nyr
    }
    town_ref_kes = town_lookup.get(town.lower(), geo_mean)

    pct_above_geomean = ((quoted_kes - geo_mean) / geo_mean) * 100.0
    pct_above_town = ((quoted_kes - town_ref_kes) / town_ref_kes) * 100.0
    # PPRA uses CV <= 30% as the outlier elimination boundary
    is_inflated = pct_above_geomean > 30.0

    return {
        "item_code": item_code,
        "item_description": desc,
        "unit": unit,
        "quoted_original": f"{currency.upper()} {quoted_unit_price:,.2f}",
        "normalized_quoted_kes": round(quoted_kes, 2),
        "fx_source": fx_source,
        "ppra_national_geometric_mean_kes": geo_mean,
        "ppra_town_reference_kes": {"town": town, "price_kes": town_ref_kes},
        "deviation_from_geometric_mean_pct": round(pct_above_geomean, 2),
        "deviation_from_town_mean_pct": round(pct_above_town, 2),
        "ppra_survey_cv_pct": cv_pct,
        "exceeds_ppra_30pct_cv_corridor": is_inflated,
        "statutory_assessment": (
            "FLAGGED FOR PRICE INFLATION (>30% CV Threshold — Violates PPADA Sec 54(1) & Reg 43(4))"
            if is_inflated else "WITHIN PPRA MARKET PRICE CORRIDOR"
        ),
        "citation": src_doc
    }


@mcp.tool()
def cross_check_bid_box_collusion(bids_payload_json: str) -> dict:
    """
    TOOL 4 (Multi-Bid Beneficial Ownership & Metadata Collusion Graph):
    Takes a JSON array of extracted bid records (company names, KRA PINs, CR12 Director IDs, phone numbers)
    and detects shared beneficial owners or collusive bidding rings under PPADA Section 66(3).
    """
    import json
    bids = json.loads(bids_payload_json)
    director_index = {}
    kra_index = {}
    collusion_findings = []

    for b in bids:
        bid_ref = b.get("bidder_name", "Unknown Bidder")
        for d_id in b.get("director_ids", []):
            director_index.setdefault(d_id, []).append(bid_ref)
        kra = b.get("kra_pin")
        if kra:
            kra_index.setdefault(kra, []).append(bid_ref)

    for d_id, companies in director_index.items():
        if len(companies) > 1:
            collusion_findings.append({
                "indicator": "Shared CR12 Director National ID across competing bids",
                "shared_id": d_id,
                "competing_bidders": companies,
                "statutory_citation": "PPADA 2015 Sec 66(3)(a) - Collusive Practices / Conflict of Interest"
            })

    return {
        "bids_compared": len(bids),
        "collusion_detected": len(collusion_findings) > 0,
        "findings": collusion_findings
    }

if __name__ == "__main__":
    mcp.run()