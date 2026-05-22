"""
scraper_drilldown.py
Step 3: For each bid, open "View Bid Result" → extract winner, L1 price, and bidder count.
Step 4: Navigate to "Evaluation Details" → extract and merge full vendor rankings table.
"""

import asyncio
import re
import json
import logging
from typing import Optional
from playwright.async_api import Page

from config import *
from db import upsert_bid, insert_vendors

log = logging.getLogger("drilldown")


# ─── Page Navigation ──────────────────────────────────────────────────────────

async def open_bid_result(page: Page, bid: dict) -> Optional[str]:
    """Navigate directly to the award result page using direct/candidate URLs."""
    bid_id = bid["bid_id"]
    
    # 1. Try the direct result URL captured during listing
    if bid.get("bid_url"):
        try:
            log.info(f"Navigating to direct result URL: {bid['bid_url']}")
            await page.goto(bid["bid_url"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            await page.wait_for_timeout(2000)
            return bid["bid_url"]
        except Exception as e:
            log.warning(f"Could not open via direct URL for {bid_id}: {e}")

    # 2. Candidate Fallbacks construction
    bid_slug = bid_id.replace("/", "%2F")
    candidate_urls = [
        f"https://bidplus.gem.gov.in/bidding/bid/getBidResultView/{bid_slug}",
        f"https://bidplus.gem.gov.in/bidding/bid/getSinglePacketResultView/{bid_slug}",
    ]

    for url in candidate_urls:
        try:
            log.info(f"Trying candidate URL: {url}")
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if resp and resp.status < 400:
                return url
        except Exception:
            continue

    return None


# ─── Remarks Extraction ───────────────────────────────────────────────────────

async def fetch_disqualification_reason(page: Page, bp_id: str) -> str:
    """
    Fetch deep technical disqualification comments directly from the session.
    Fires an async browser-evaluated fetch request to bypass external AJAX session limits.
    """
    try:
        log.info(f"Fetching AJAX disqualification remarks for bp_id={bp_id}...")
        reason = await page.evaluate(f"""
            async () => {{
                try {{
                    const response = await fetch('/bidding/buyer/getReason/{bp_id}');
                    if (response.ok) {{
                        return await response.text();
                    }}
                }} catch (e) {{}}
                return '';
            }}
        """)
        if reason:
            # Strip tags and normalize whitespaces
            reason_clean = re.sub(r"<[^>]*>", " ", reason)
            reason_clean = re.sub(r"\s+", " ", reason_clean).strip()
            log.info(f"Remarks parsed: '{reason_clean[:50]}...'")
            return reason_clean
    except Exception as e:
        log.warning(f"AJAX comments fetch failed for bp_id={bp_id}: {e}")
    return ""


# ─── Vendor Name Normalization ────────────────────────────────────────────────

def clean_seller_name(name: str) -> str:
    """
    Strip noisy corporate metadata from seller names to facilitate relational mapping.
    Example: 'VISHAL ENTERPRISES( MSE Social Category:General )' -> 'VISHAL ENTERPRISES'
    """
    if not name:
        return ""
    n = name.replace("\xa0", " ").strip().upper()
    n = re.sub(r"\(.*?\)", "", n)       # remove popover descriptions
    n = re.sub(r"\bUNDER\s+PMA\b", "", n)  # remove PMA designation
    n = re.sub(r"\s+", " ", n)          # remove duplicate spacing
    n = re.sub(r"[^\w\s&.,-]", "", n)   # clean special punctuation characters
    return n.strip()


# ─── Fallback Card Result Parser ──────────────────────────────────────────────

async def extract_bid_result_fallback(page: Page) -> dict:
    """Text-based regex fallback parser if table layouts are corrupted."""
    result = {
        "winner_name": "",
        "winner_price": "",
        "num_bidders": None,
    }
    try:
        page_text = await page.inner_text("body")
        
        m_win = re.search(r"(?:Winner|Awarded to)\s*:?\s*([A-Za-z0-9 &.,()/-]+)", page_text, re.I)
        if m_win:
            result["winner_name"] = clean_seller_name(m_win.group(1))

        m_price = re.search(r"(?:L1 Price|Winner Price|Award Amount)\s*:?\s*₹?([\d,. ]+)", page_text, re.I)
        if m_price:
            result["winner_price"] = m_price.group(1).strip()

        m_bidders = re.search(r"(?:Total Bidders|Participants|No\. of Bidders)\s*:?\s*(\d+)", page_text, re.I)
        if m_bidders:
            result["num_bidders"] = int(m_bidders.group(1))
    except Exception as e:
        log.error(f"Fallback card text parser failed: {e}")
    return result


# ─── Page Table Layout Dynamic Parser ─────────────────────────────────────────

async def parse_bid_result_page(page: Page) -> dict:
    """
    Dynamic layouts parser. Detects Single-Packet vs. Double-Packet results,
    extracts qualification statuses/remarks, merges prices, and maps L1 winner details.
    """
    tables = await page.query_selector_all("table")
    log.info(f"Parsing page tables... found {len(tables)} tables")

    vendors_dict = {}
    parsed_tables = []
    
    for idx, table in enumerate(tables):
        headers = [((await h.inner_text()).strip().lower()) for h in await table.query_selector_all("th")]
        parsed_tables.append((table, headers))

    is_single_packet = False
    technical_table = None
    financial_table = None

    # Classify tables based on headers
    for table, headers in parsed_tables:
        header_str = " ".join(headers)
        if "status" in header_str and "rank" in header_str:
            is_single_packet = True
            financial_table = table
            break
        elif "status" in header_str:
            technical_table = table
        elif "rank" in header_str or "price" in header_str or "total price" in header_str:
            financial_table = table

    if len(tables) == 1 and not is_single_packet:
        is_single_packet = True
        financial_table = tables[0]

    # ── Option A: Single-Packet Layout (Combined table) ─────────────────────
    if is_single_packet and financial_table:
        log.info("Parsing Single-Packet table layout...")
        headers = [((await h.inner_text()).strip().lower()) for h in await financial_table.query_selector_all("th")]
        rows = await financial_table.query_selector_all("tbody tr")
        
        name_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ["seller", "name", "vendor", "bidder"])), 1)
        price_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ["price", "total", "amount"]) and "item" not in h), None)
        rank_idx = next((i for i, h in enumerate(headers) if "rank" in h), None)
        status_idx = next((i for i, h in enumerate(headers) if "status" in h), None)

        for row in rows:
            cells = await row.query_selector_all("td")
            if len(cells) < 2:
                continue
            cell_texts = [((await c.inner_text()).strip()) for c in cells]
            
            clean_name = clean_seller_name(cell_texts[name_idx] if name_idx < len(cell_texts) else "")
            if not clean_name:
                continue

            price_str = cell_texts[price_idx] if price_idx is not None and price_idx < len(cell_texts) else ""
            rank_str = cell_texts[rank_idx] if rank_idx is not None and rank_idx < len(cell_texts) else ""
            status_str = cell_texts[status_idx] if status_idx is not None and status_idx < len(cell_texts) else "Qualified"

            status_flag = "disqualified" if any(k in status_str.lower() for k in ["disqual", "reject", "fail"]) else "qualified"

            remarks = ""
            view_reason_btn = await row.query_selector("a.view_reason, .view_reason, [data-bp_id]")
            if view_reason_btn:
                bp_id = await view_reason_btn.get_attribute("data-bp_id")
                if bp_id:
                    remarks = await fetch_disqualification_reason(page, bp_id)

            vendors_dict[clean_name] = {
                "name": clean_name,
                "rank": rank_str,
                "price": price_str.replace("`", "").strip(),
                "status": status_flag,
                "remarks": remarks
            }

    # ── Option B: Double-Packet Layout (Split tables) ──────────────────────
    else:
        log.info("Parsing Double-Packet table layouts...")
        # 1. Parse Technical Table (Qualifications)
        if technical_table:
            headers = [((await h.inner_text()).strip().lower()) for h in await technical_table.query_selector_all("th")]
            rows = await technical_table.query_selector_all("tbody tr")
            name_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ["seller", "name", "vendor", "bidder"])), 1)
            status_idx = next((i for i, h in enumerate(headers) if "status" in h), 4)

            for row in rows:
                cells = await row.query_selector_all("td")
                if len(cells) < 2:
                    continue
                cell_texts = [((await c.inner_text()).strip()) for c in cells]

                clean_name = clean_seller_name(cell_texts[name_idx] if name_idx < len(cell_texts) else "")
                if not clean_name:
                    continue

                status_str = cell_texts[status_idx] if status_idx < len(cell_texts) else "Qualified"
                status_flag = "disqualified" if any(k in status_str.lower() for k in ["disqual", "reject", "fail"]) else "qualified"

                remarks = ""
                view_reason_btn = await row.query_selector("a.view_reason, .view_reason, [data-bp_id]")
                if view_reason_btn:
                    bp_id = await view_reason_btn.get_attribute("data-bp_id")
                    if bp_id:
                        remarks = await fetch_disqualification_reason(page, bp_id)

                vendors_dict[clean_name] = {
                    "name": clean_name,
                    "rank": "",
                    "price": "",
                    "status": status_flag,
                    "remarks": remarks
                }

        # 2. Parse Financial Table (Prices and Rankings)
        if financial_table:
            headers = [((await h.inner_text()).strip().lower()) for h in await financial_table.query_selector_all("th")]
            rows = await financial_table.query_selector_all("tbody tr")
            name_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ["seller", "name", "vendor", "bidder"])), 1)
            price_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ["price", "total", "amount"]) and "item" not in h), None)
            rank_idx = next((i for i, h in enumerate(headers) if "rank" in h), None)

            for row in rows:
                cells = await row.query_selector_all("td")
                if len(cells) < 2:
                    continue
                cell_texts = [((await c.inner_text()).strip()) for c in cells]

                clean_name = clean_seller_name(cell_texts[name_idx] if name_idx < len(cell_texts) else "")
                if not clean_name:
                    continue

                price_str = cell_texts[price_idx] if price_idx is not None and price_idx < len(cell_texts) else ""
                rank_str = cell_texts[rank_idx] if rank_idx is not None and rank_idx < len(cell_texts) else ""

                if clean_name in vendors_dict:
                    vendors_dict[clean_name]["price"] = price_str.replace("`", "").strip()
                    vendors_dict[clean_name]["rank"] = rank_str
                else:
                    vendors_dict[clean_name] = {
                        "name": clean_name,
                        "rank": rank_str,
                        "price": price_str.replace("`", "").strip(),
                        "status": "qualified",
                        "remarks": ""
                    }

    # ── Identify winner details ───────────────────────────────────────────
    winner_name = ""
    winner_price = ""
    num_bidders = len(vendors_dict)

    for v in vendors_dict.values():
        if v["rank"] == "L1" or "L1" in v["rank"]:
            winner_name = v["name"]
            winner_price = v["price"]
            break

    # Fallback to text parsing if no L1 was mapped in tables
    if not winner_name:
        fallback = await extract_bid_result_fallback(page)
        winner_name = fallback["winner_name"]
        winner_price = fallback["winner_price"]
        if fallback["num_bidders"]:
            num_bidders = fallback["num_bidders"]

    return {
        "winner_name": winner_name,
        "winner_price": winner_price,
        "num_bidders": num_bidders,
        "vendors": list(vendors_dict.values())
    }


# ─── Anomaly Flagging ─────────────────────────────────────────────────────────

def flag_anomalies(bid: dict, vendors: list[dict]) -> list[dict]:
    """Flag bidding pricing anomalies where winner price exceeds the lowest qualified quote."""
    winner_name = (bid.get("winner_name") or "").lower().strip()
    winner_price = bid.get("winner_price") or ""

    def parse_price(p):
        try:
            return float(re.sub(r"[^\d.]", "", p))
        except Exception:
            return float("inf")

    qualified = [v for v in vendors if v["status"] == "qualified"]
    if not qualified or not winner_price:
        return vendors

    prices = [parse_price(v["price"]) for v in qualified if v["price"]]
    min_price = min(prices) if prices else None
    winner_p = parse_price(winner_price)

    for v in vendors:
        v_name = v["name"].lower().strip()
        if winner_name and winner_name in v_name:
            if min_price and abs(winner_p - min_price) > 0.01 and winner_p > min_price:
                v["status"] = "anomaly"
                v["remarks"] = (v.get("remarks") or "") + " [WINNER_NOT_LOWEST]"
                log.warning(f"Pricing Anomaly: L1 winner {v['name']} price={winner_p} > min qualified quote={min_price}")

    return vendors


# ─── Drilldown Execution Entry ────────────────────────────────────────────────

async def drilldown_bid(page: Page, bid: dict) -> dict:
    """Drilldown and enrich a single bid record."""
    bid_id = bid["bid_id"]
    log.info(f"Drilling details for bid: {bid_id}")

    try:
        # 1. Load result page
        result_url = await open_bid_result(page, bid)
        if not result_url:
            log.warning(f"Could not load result page for {bid_id}")
            upsert_bid({**bid, "scrape_status": "error", "error_msg": "no_result_page"})
            return bid

        # 2. Parse result tables (retry once on transient navigation errors)
        try:
            parsed_data = await parse_bid_result_page(page)
        except Exception as parse_err:
            if "Execution context was destroyed" in str(parse_err):
                log.warning(f"Retrying parse for {bid_id} after navigation reset")
                await page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                parsed_data = await parse_bid_result_page(page)
            else:
                raise

        bid["winner_name"] = parsed_data.get("winner_name") or bid.get("winner_name") or ""
        bid["winner_price"] = parsed_data.get("winner_price") or bid.get("winner_price") or ""
        bid["num_bidders"] = parsed_data.get("num_bidders") or bid.get("num_bidders") or 0

        if not bid.get("bid_value") and bid["winner_price"]:
            bid["bid_value"] = bid["winner_price"]

        vendors = parsed_data.get("vendors") or []
        vendors = flag_anomalies(bid, vendors)

        if vendors:
            insert_vendors(bid_id, vendors)
            bid["raw_eval_json"] = json.dumps(vendors)
            bid["num_bidders"]   = bid.get("num_bidders") or len(vendors)

        bid["scrape_status"] = "done"
        upsert_bid(bid)
        log.info(f"  ✓ {bid_id} enriched | winner={bid.get('winner_name','')} | bidders={len(vendors)}")

    except Exception as e:
        log.error(f"Drilldown failed for {bid_id}: {e}")
        upsert_bid({**bid, "scrape_status": "error", "error_msg": str(e)})

    return bid


async def run_drilldown_scraper(page: Page, bids: list[dict]) -> list[dict]:
    """Run details drilldown over all listed pending bids."""
    enriched = []
    for i, bid in enumerate(bids, 1):
        log.info(f"[{i}/{len(bids)}] Drilling {bid['bid_id']}")
        result = await drilldown_bid(page, bid)
        enriched.append(result)
        await asyncio.sleep(1)  # polite throttle delay
    return enriched
