"""
db.py — Persistence layer using SQLite.
Stores listing data and drilldown (winner and vendor evaluation tables).
"""

import sqlite3
import logging
from datetime import datetime

DB_FILE = "gem_bids.db"
log = logging.getLogger("db")

def init_db():
    """Initializes the SQLite database schema."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create bids table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bids (
            bid_id TEXT PRIMARY KEY,
            ra_number TEXT,
            category TEXT,
            buyer TEXT,
            quantity TEXT,
            bid_value TEXT,
            start_date TEXT,
            award_date TEXT,
            bid_url TEXT,
            winner_name TEXT,
            winner_price TEXT,
            num_bidders INTEGER,
            raw_eval_json TEXT,
            scrape_status TEXT DEFAULT 'listing',
            error_msg TEXT,
            scraped_at TEXT
        )
    """)
    
    # Create vendors table for full evaluation table breakdown
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            bid_id TEXT,
            vendor_name TEXT,
            vendor_rank TEXT,
            vendor_price TEXT,
            status_flag TEXT,
            remarks TEXT,
            PRIMARY KEY (bid_id, vendor_name),
            FOREIGN KEY (bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE
        )
    """)
    
    conn.commit()
    conn.close()
    log.info("Database initialized successfully.")

def upsert_bid(bid: dict):
    """Inserts or updates a bid record in the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Normalize inputs
    bid_id = bid.get("bid_id")
    if not bid_id:
        conn.close()
        return
        
    scraped_at = bid.get("scraped_at") or datetime.now().isoformat()
    
    # Prepare data for insertion
    fields = [
        "bid_id", "ra_number", "category", "buyer", "quantity", "bid_value",
        "start_date", "award_date", "bid_url", "winner_name", "winner_price",
        "num_bidders", "raw_eval_json", "scrape_status", "error_msg", "scraped_at"
    ]
    
    # Using INSERT OR REPLACE is safe and highly compatible
    placeholders = ", ".join(["?"] * len(fields))
    values = [
        bid.get("bid_id"),
        bid.get("ra_number"),
        bid.get("category"),
        bid.get("buyer"),
        bid.get("quantity"),
        bid.get("bid_value"),
        bid.get("start_date"),
        bid.get("award_date"),
        bid.get("bid_url"),
        bid.get("winner_name"),
        bid.get("winner_price"),
        bid.get("num_bidders"),
        bid.get("raw_eval_json"),
        bid.get("scrape_status", "listing"),
        bid.get("error_msg"),
        scraped_at
    ]
    
    cursor.execute(f"""
        INSERT OR REPLACE INTO bids ({", ".join(fields)})
        VALUES ({placeholders})
    """, values)
    
    conn.commit()
    conn.close()

def insert_vendors(bid_id: str, vendors: list[dict]):
    """Inserts or replaces all vendors related to a specific bid."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Delete old vendor records for this bid first to avoid duplicates
    cursor.execute("DELETE FROM vendors WHERE bid_id = ?", (bid_id,))
    
    for v in vendors:
        name = v.get("name")
        if not name:
            continue
            
        cursor.execute("""
            INSERT OR REPLACE INTO vendors (bid_id, vendor_name, vendor_rank, vendor_price, status_flag, remarks)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            bid_id,
            name,
            v.get("rank"),
            v.get("price"),
            v.get("status", "qualified"),
            v.get("remarks")
        ))
        
    conn.commit()
    conn.close()

def get_pending_bids() -> list[dict]:
    """Retrieves all bids that have been listed but not drilled down (or resulted in errors)."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM bids 
        WHERE scrape_status = 'listing' OR scrape_status = 'error'
    """)
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

def get_all_bids() -> list[dict]:
    """Retrieves all bids in the database."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM bids")
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


# Run schema initialization when executed directly
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
