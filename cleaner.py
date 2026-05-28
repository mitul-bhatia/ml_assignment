"""
cleaner.py — Data normalization, duplicate detection, anomaly flagging,
and summary insight calculation. Produces final CSV and JSON outputs.
"""

import sqlite3
import pandas as pd
import json
import re
import os
import logging
from typing import Dict, Any

# Ensure output directory exists
os.makedirs("output", exist_ok=True)

log = logging.getLogger("cleaner")

def normalize_vendor_name(name: Any) -> str:
    """
    Normalizes vendor names to detect repeat winners and duplicates.
    Example: 'VISHAL ENTERPRISES PVT. LTD.' -> 'VISHAL ENTERPRISES PVT LTD'
    """
    if pd.isna(name) or name is None:
        return ""
    
    # Cast to string and convert to uppercase and strip outer whitespace
    name_str = str(name).strip()
    if not name_str:
        return ""
        
    n = name_str.upper()
    
    # 2. Replace multiple spaces with a single space
    n = re.sub(r"\s+", " ", n)
    
    # 3. Clean common company designations and punctuation
    # PVT. LTD. -> PVT LTD, CO. -> CO, etc.
    n = re.sub(r"\bPVT\.?\s*LTD\.?\b", "PVT LTD", n)
    n = re.sub(r"\bLIMITED\b", "LTD", n)
    n = re.sub(r"\bLTD\.?\b", "LTD", n)
    n = re.sub(r"\bCO\b\s*(\bAND\b|&)?\s*\bLTD\b", "CO LTD", n)
    n = re.sub(r"\bPVT\b", "PVT", n)
    n = re.sub(r"\bLLP\.?\b", "LLP", n)
    n = re.sub(r"\bINC\.?\b", "INC", n)
    n = re.sub(r"\bCORP\.?\b", "CORP", n)
    
    # 4. Remove trailing punctuation like dots or commas
    n = re.sub(r"[.,\-()]+$", "", n)
    n = re.sub(r"^[.,\-()]+", "", n)
    
    return n.strip()

def parse_price(val: Any) -> float:
    """Parses money values to float. Example: '₹12,34,567.89' -> 1234567.89"""
    if pd.isna(val) or val is None:
        return 0.0
    s = str(val).strip()
    
    # 1. Ignore if it looks like a date (e.g. '16-11-2025' or '16/11/2025') or a time ('20:24:59')
    if re.search(r"\d{2,4}[-/]\d{2}[-/]\d{2,4}", s) or ":" in s:
        return 0.0
        
    # 2. Ignore non-numeric strings
    if any(k in s.upper() for k in ["MSE", "REGISTERED", "QUALIFIED", "DISQUALIFIED", "N/A", "OFFLINE"]):
        return 0.0

    # 3. Strip everything except digits and decimal point
    s_clean = re.sub(r"[^\d.]", "", s)
    try:
        val_float = float(s_clean) if s_clean else 0.0
        # If it's unreasonably large (e.g., > 100 billion, likely an parsed ID or date without dashes)
        if val_float > 1e11:
            return 0.0
        return val_float
    except ValueError:
        return 0.0

def process_data() -> Dict[str, Any]:
    """
    Reads SQLite data, applies normalization, flags anomalies,
    detects duplicates, and outputs final results.
    """
    DB_FILE = "gem_bids.db"
    if not os.path.exists(DB_FILE):
        return {"error": "Database file gem_bids.db does not exist."}
        
    conn = sqlite3.connect(DB_FILE)
    
    # Load bids
    df_bids = pd.read_sql_query("SELECT * FROM bids", conn)
    # Load vendors
    df_vendors = pd.read_sql_query("SELECT * FROM vendors", conn)
    conn.close()
    
    if df_bids.empty:
        return {"error": "No bid records found in database."}

    # Prepare datasets
    flat_rows = []
    nested_bids = []
    
    # Track statistics
    total_bids = 0
    bids_over_3_bidders = 0
    price_gaps = []
    winner_frequencies = {}

    for _, bid_row in df_bids.iterrows():
        bid_id = bid_row["bid_id"]
        total_bids += 1
        
        # Get vendors for this bid
        bid_vendors = df_vendors[df_vendors["bid_id"] == bid_id].copy()
        
        # Fallback if evaluation detail table wasn't populated
        if bid_vendors.empty and bid_row["raw_eval_json"]:
            try:
                raw_v = json.loads(bid_row["raw_eval_json"])
                rows = []
                for idx, v in enumerate(raw_v):
                    rows.append({
                        "bid_id": bid_id,
                        "vendor_name": v.get("name"),
                        "vendor_rank": v.get("rank") or f"L{idx+1}",
                        "vendor_price": v.get("price"),
                        "status_flag": v.get("status", "qualified"),
                        "remarks": v.get("remarks")
                    })
                bid_vendors = pd.DataFrame(rows)
            except Exception:
                pass
                
        # Normalize and process vendors
        processed_vendors = []
        seen_vendors = set()
        
        # Sort vendors by rank or price if rank is L1, L2 etc.
        def get_rank_num(rank_str):
            if not rank_str:
                return 999
            m = re.search(r"\d+", str(rank_str))
            return int(m.group(0)) if m else 999
            
        if not bid_vendors.empty:
            # Add rank sorting priority
            bid_vendors["rank_num"] = bid_vendors["vendor_rank"].apply(get_rank_num)
            bid_vendors = bid_vendors.sort_values(by="rank_num")

        # Winner Details
        winner_name = normalize_vendor_name(bid_row["winner_name"])
        winner_price = parse_price(bid_row["winner_price"])
        bid_value_num = parse_price(bid_row["bid_value"])
        
        # Verify L1 vs L2 pricing gap
        l1_price = None
        l2_price = None
        
        for _, v_row in bid_vendors.iterrows():
            v_name_raw = v_row["vendor_name"] or ""
            v_name_norm = normalize_vendor_name(v_name_raw)
            v_rank = v_row["vendor_rank"] or ""
            v_price_raw = v_row["vendor_price"] or ""
            v_price_num = parse_price(v_price_raw)
            v_price = f"{v_price_num:.2f}" if v_price_num > 0 else ""
            v_status = v_row["status_flag"] or "qualified"
            v_remarks = v_row["remarks"] or ""
            
            # ── Duplicate Detection ─────────────────────────────────────────
            is_duplicate = False
            if v_name_norm in seen_vendors:
                is_duplicate = True
                v_remarks += " [DUPLICATE_VENDOR]"
                log.warning(f"Duplicate vendor {v_name_norm} detected in bid {bid_id}")
            else:
                seen_vendors.add(v_name_norm)
                
            # ── Track L1 and L2 for Gap Analysis ─────────────────────────────
            v_price_num = parse_price(v_price)
            if v_status.lower() in ["qualified", "l1", "l2", "active"]:
                if l1_price is None:
                    l1_price = v_price_num
                elif l2_price is None and v_price_num > l1_price:
                    l2_price = v_price_num
            
            # Anomaly Flag Check
            # If vendor name matches winner name, check if price matches winner price
            # Or check if this vendor has "WINNER_NOT_LOWEST" from scraper
            v_status_flag = v_status
            if "WINNER_NOT_LOWEST" in v_remarks:
                v_status_flag = "anomaly"
                
            processed_vendors.append({
                "vendor_name": v_name_norm,
                "vendor_rank": v_rank,
                "vendor_price": v_price,
                "status_flag": v_status_flag,
                "remarks": v_remarks
            })
            
        # ── Winner Price Anomaly Check ──────────────────────────────────────
        # Improve winner detection if missing
        if not winner_name:
            l1_candidates = [pv for pv in processed_vendors if (pv.get("vendor_rank") or "").upper() == "L1"]
            if l1_candidates:
                winner_name = l1_candidates[0]["vendor_name"]
                winner_price = parse_price(l1_candidates[0]["vendor_price"])
            else:
                price_candidates = [pv for pv in processed_vendors if pv.get("vendor_price")]
                if price_candidates:
                    lowest = min(price_candidates, key=lambda pv: parse_price(pv["vendor_price"]))
                    winner_name = lowest["vendor_name"]
                    winner_price = parse_price(lowest["vendor_price"])

        # Verify if winner is truly lowest price
        lowest_qualified_price = None
        for pv in processed_vendors:
            if pv["status_flag"] == "qualified" and pv["vendor_price"]:
                p = parse_price(pv["vendor_price"])
                if lowest_qualified_price is None or (p > 0 and p < lowest_qualified_price):
                    lowest_qualified_price = p
                    
        # Apply winner_not_lowest flag
        is_anomaly = False
        anomaly_remarks = ""
        if winner_price and lowest_qualified_price and winner_price > lowest_qualified_price + 0.01:
            is_anomaly = True
            anomaly_remarks = f"Winner price {winner_price} is higher than lowest qualified quote {lowest_qualified_price}"
            log.warning(f"Anomaly in {bid_id}: winner price {winner_price} > min {lowest_qualified_price}")
            
        # Calculate gaps
        if l1_price and l2_price and l1_price > 0:
            gap = l2_price - l1_price
            price_gaps.append({
                "bid_id": bid_id,
                "l1": l1_price,
                "l2": l2_price,
                "gap_abs": gap,
                "gap_pct": (gap / l1_price) * 100
            })
            
        # Repeat winner tracking
        if winner_name:
            winner_frequencies[winner_name] = winner_frequencies.get(winner_name, 0) + 1
            
        # Bidders Count Check
        num_bidders_raw = bid_row["num_bidders"]
        if pd.isna(num_bidders_raw) or num_bidders_raw is None:
            num_bidders_raw = len(processed_vendors)
        num_bidders = int(num_bidders_raw or 0)
        if num_bidders and num_bidders > 3:
            bids_over_3_bidders += 1
            
        # Compile nested representation
        nested_bid = {
            "bid_id": bid_id,
            "ra_number": bid_row["ra_number"],
            "category": bid_row["category"],
            "buyer": bid_row["buyer"],
            "quantity": bid_row["quantity"],
            "bid_value": f"{bid_value_num:.2f}" if bid_value_num > 0 else "",
            "start_date": bid_row["start_date"],
            "award_date": bid_row["award_date"],
            "bid_url": bid_row["bid_url"],
            "winner_name": winner_name,
            "winner_price": f"{winner_price:.2f}" if winner_price > 0 else "",
            "num_bidders": num_bidders,
            "is_anomaly": is_anomaly,
            "anomaly_remarks": anomaly_remarks,
            "vendors": processed_vendors
        }
        nested_bids.append(nested_bid)
        
        # Compile flat rows for CSV export
        if not processed_vendors:
            # If no vendors, export bid with null vendor fields
            flat_rows.append({
                "bid_id": bid_id,
                "category": bid_row["category"],
                "buyer": bid_row["buyer"],
                "quantity": bid_row["quantity"],
                "bid_value": f"{bid_value_num:.2f}" if bid_value_num > 0 else "",
                "award_date": bid_row["award_date"],
                "winner_name": winner_name,
                "winner_price": f"{winner_price:.2f}" if winner_price > 0 else "",
                "num_bidders": num_bidders,
                "vendor_name": "",
                "vendor_rank": "",
                "vendor_price": "",
                "status_flag": "no_data_scraped",
                "remarks": "No evaluation details scraped"
            })
        else:
            for pv in processed_vendors:
                flat_rows.append({
                    "bid_id": bid_id,
                    "category": bid_row["category"],
                    "buyer": bid_row["buyer"],
                    "quantity": bid_row["quantity"],
                    "bid_value": f"{bid_value_num:.2f}" if bid_value_num > 0 else "",
                    "award_date": bid_row["award_date"],
                    "winner_name": winner_name,
                    "winner_price": f"{winner_price:.2f}" if winner_price > 0 else "",
                    "num_bidders": num_bidders,
                    "vendor_name": pv["vendor_name"],
                    "vendor_rank": pv["vendor_rank"],
                    "vendor_price": pv["vendor_price"],
                    "status_flag": pv["status_flag"],
                    "remarks": pv["remarks"]
                })

    # Save to final outputs
    df_flat = pd.DataFrame(flat_rows)
    df_flat.to_csv("output/bids_final.csv", index=False)
    
    with open("output/bids_final.json", "w", encoding="utf-8") as f:
        json.dump(nested_bids, f, indent=2, ensure_ascii=False)

    # Dynamically inject JSON into dashboard.html to bypass CORS when loaded locally
    try:
        dash_path = "output/dashboard.html"
        if os.path.exists(dash_path):
            with open(dash_path, "r", encoding="utf-8") as f:
                dash_html = f.read()
            
            pattern = r"/\*BIDS_DATA_START\*/.*?/\*BIDS_DATA_END\*/"
            replacement = f"/*BIDS_DATA_START*/{json.dumps(nested_bids, ensure_ascii=False)}/*BIDS_DATA_END*/"
            new_html = re.sub(pattern, replacement, dash_html, flags=re.DOTALL)
            
            with open(dash_path, "w", encoding="utf-8") as f:
                f.write(new_html)
    except Exception as e:
        log.warning(f"Could not inject embedded data into dashboard.html: {e}")

    # ─── Summarize Insights ──────────────────────────────────────────────────
    pct_over_3_bidders = (bids_over_3_bidders / total_bids) * 100 if total_bids > 0 else 0
    
    df_gaps = pd.DataFrame(price_gaps)
    avg_gap_pct = df_gaps["gap_pct"].mean() if not df_gaps.empty else 0.0
    avg_gap_abs = df_gaps["gap_abs"].mean() if not df_gaps.empty else 0.0
    
    repeat_winners = {k: v for k, v in winner_frequencies.items() if v > 1}
    sorted_repeats = sorted(repeat_winners.items(), key=lambda x: x[1], reverse=True)
    
    insights = {
        "total_bids": total_bids,
        "pct_over_3_bidders": pct_over_3_bidders,
        "avg_gap_abs": avg_gap_abs,
        "avg_gap_pct": avg_gap_pct,
        "sorted_repeats": sorted_repeats
    }
    
    print_report(insights)
    
    return insights

def print_report(insights: Dict[str, Any]):
    """Prints a beautiful markdown analysis report in the terminal."""
    print("=" * 60)
    print("                 GEMEDGE SCRAPER SUMMARY REPORT")
    print("=" * 60)
    print(f"Total Bids Extracted:               {insights['total_bids']}")
    print(f"Bids with >3 Bidders:               {insights['pct_over_3_bidders']:.2f}%")
    print(f"Average L1-L2 Pricing Gap (Abs):     ₹{insights['avg_gap_abs']:,.2f}")
    print(f"Average L1-L2 Pricing Gap (%):       {insights['avg_gap_pct']:.2f}%")
    print("-" * 60)
    print("Repeat Winners Distribution:")
    if insights["sorted_repeats"]:
        for name, count in insights["sorted_repeats"][:5]:
            print(f"  - {name:<35} | {count} Wins")
    else:
        print("  No repeat winners detected (all winners unique so far).")
    print("=" * 60)
    print("Outputs successfully saved to:")
    print("  - Flat CSV:  output/bids_final.csv")
    print("  - JSON:      output/bids_final.json")
    print("=" * 60)

if __name__ == "__main__":
    process_data()
