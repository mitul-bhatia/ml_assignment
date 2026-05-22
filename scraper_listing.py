"""
scraper_listing.py
Step 1: Navigate to all-bids, apply Awarded filter.
Step 2: Paginate and extract 30+ bid listing entries.
"""

import re
import logging
from typing import Optional
from playwright.async_api import Page

from config import *
from db import upsert_bid

log = logging.getLogger("listing")


# ─── Filter Application ────────────────────────────────────────────────────────

async def apply_awarded_filter(page: Page):
    """
    On the all-bids page, uncheck 'Ongoing Bids/RA' and check 'Bid/RA Status' → 'Awarded'.
    Uses direct JS clicks to bypass custom iCheck UI elements overlay.
    """
    log.info("Applying Awarded filter...")

    await page.goto(BASE_URL, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
    await page.wait_for_timeout(2000)

    async def click_if_present(selectors: list[str], label: str) -> bool:
        for sel in selectors:
            el = await page.query_selector(sel)
            if el:
                await el.evaluate("el => el.click()")
                log.info(f"Clicked {label} via JS selector={sel}")
                return True
        return False

    async def uncheck_if_checked(selectors: list[str], label: str) -> bool:
        for sel in selectors:
            el = await page.query_selector(sel)
            if el:
                was_checked = await el.evaluate("el => !!el.checked")
                if was_checked:
                    await el.evaluate("el => el.click()")
                    log.info(f"Unchecked {label} via JS selector={sel}")
                    return True
        return False

    # 1. Uncheck "Ongoing" if present
    ongoing_selectors = [
        "#ongoing",
        "#ongoing_bid",
        "input[value='Ongoing']",
        "input[name*='ongoing']",
        "input[id*='ongoing']",
    ]
    await uncheck_if_checked(ongoing_selectors, "Ongoing")

    # 2. Click "Bid/RA Status" trigger
    bid_status_selectors = [
        "#bidrastatus",
        "input[value='Bid/RA Status']",
        "input[id*='bidra']",
    ]
    if await click_if_present(bid_status_selectors, "Bid/RA Status"):
        await page.wait_for_timeout(2000)

    # 3. Click "Awarded" trigger
    awarded_selectors = [
        "#bid_awarded",
        "input[value='Awarded']",
        "input[id*='awarded']",
    ]
    if await click_if_present(awarded_selectors, "Awarded"):
        await page.wait_for_timeout(5000)  # wait for AJAX content reload

    log.info("Filter applied successfully.")


# ─── Single Card Extraction ────────────────────────────────────────────────────

async def extract_card(card) -> Optional[dict]:
    """
    Extract listing-level fields from a single bid card element.
    Extracts: Bid/RA Numbers, Items/Category, Department/Buyer, Quantity, and direct Result URLs.
    """
    try:
        card_text = await card.inner_text()

        # ── Bid & RA Numbers ──────────────────────────────────────────────────
        bid_links = await card.query_selector_all(SEL_BID_NUMBER)
        bid_id = ""
        ra_number = ""
        
        if len(bid_links) > 0:
            bid_id = (await bid_links[0].inner_text()).strip()
        if len(bid_links) > 1:
            ra_number = (await bid_links[1].inner_text()).strip()

        # Regex fallback for Bid Number
        if not bid_id:
            match = re.search(r"GEM/\d{4}/B/\d+", card_text)
            if match:
                bid_id = match.group(0)
            else:
                match_any = re.search(r"GEM/\d{4}/[BR]/\d+", card_text)
                if match_any:
                    bid_id = match_any.group(0)

        if not bid_id:
            return None

        # ── Direct Results URL (bid_url) ──────────────────────────────────────
        res_link = await card.query_selector(SEL_RESULT_LINK)
        bid_url = ""
        if res_link:
            href = await res_link.get_attribute("href") or ""
            if href:
                bid_url = href if href.startswith("http") else "https://bidplus.gem.gov.in" + href
        
        # Fallback Results URL construction if direct link not found
        if not bid_url and len(bid_links) > 0:
            href = await bid_links[0].get_attribute("href") or ""
            doc_match = re.search(r"showbidDocument/(\d+)", href)
            if doc_match:
                doc_id = doc_match.group(1)
                bid_url = f"https://bidplus.gem.gov.in/bidding/bid/getBidResultView/{doc_id}"

        # ── Category / Item Description ───────────────────────────────────────
        # Extract from tooltip popovers to avoid ellipsis truncation
        popover = await card.query_selector(SEL_CATEGORY_POPOVER)
        category = ""
        if popover:
            category = (await popover.get_attribute("data-content") or "").strip()
        if not category:
            item_el = await card.query_selector(".items-text, .item-name, a[href*='catalog'], span.item")
            category = (await item_el.inner_text()).strip() if item_el else ""
        if not category:
            m = re.search(r"Items?:\s*(.+?)(?:\n|Quantity|Dept)", card_text, re.I)
            category = m.group(1).strip() if m else ""

        # ── Department / Buyer ───────────────────────────────────────────────
        col_dept = await card.query_selector(SEL_BUYER_COLUMN)
        buyer = ""
        if col_dept:
            dept_text = await col_dept.inner_text()
            buyer = dept_text.replace("Department Name And Address:", "").strip()
            buyer = re.sub(r"\s+", " ", buyer)
        if not buyer:
            m = re.search(r"Department.*?:\s*(.+?)(?:\n|State|City|Start Date)", card_text, re.I | re.S)
            buyer = m.group(1).strip()[:200] if m else ""

        # ── Quantity ─────────────────────────────────────────────────────────
        m_qty = re.search(r"Quantity\s*:\s*([\d,]+)", card_text, re.I)
        quantity = m_qty.group(1).replace(",", "") if m_qty else "1"

        # Estimated contract value is populated from L1 winner quote during drilldown phase
        bid_value = ""

        # Dates
        start_m = re.search(r"Start\s*Date\s*:\s*([\d\-/]+\s*[\d:]+\s*[AP]M?)", card_text, re.I)
        end_m   = re.search(r"End\s*Date\s*:\s*([\d\-/]+\s*[\d:]+\s*[AP]M?)", card_text, re.I)
        start_date = start_m.group(1).strip() if start_m else ""
        award_date = end_m.group(1).strip() if end_m else ""

        return {
            "bid_id":      bid_id,
            "ra_number":   ra_number,
            "category":    category,
            "buyer":       buyer,
            "quantity":    quantity,
            "bid_value":   bid_value,
            "start_date":  start_date,
            "award_date":  award_date,
            "bid_url":     bid_url,
            "scrape_status": "listing",
        }
    except Exception as e:
        log.error(f"Card parse error for card: {e}")
        return None


# ─── Page-Level Extraction ─────────────────────────────────────────────────────

async def scrape_current_page(page: Page) -> list[dict]:
    """Extract all bid cards on the currently loaded search page."""
    await page.wait_for_load_state("networkidle", timeout=15000)

    cards = await page.query_selector_all(SEL_BID_CARD)
    if not cards:
        # Generic backup check
        cards = await page.query_selector_all("div.card, div.bid-card")

    results = []
    for card in cards:
        data = await extract_card(card)
        if data:
            results.append(data)
    return results


# ─── Pagination ───────────────────────────────────────────────────────────────

async def go_next_page(page: Page) -> bool:
    """Click 'Next' page pagination button. Returns False if no next page exists."""
    next_selectors = [
        "li.next:not(.disabled) > a",
        "a[aria-label='Next']:not([disabled])",
        "a:has-text('›'):not(.disabled)",
        "a:has-text('Next'):not(.disabled)",
        "button:has-text('Next'):not([disabled])",
        "li.page-item:not(.disabled) > a[rel='next']",
    ]
    for sel in next_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.scroll_into_view_if_needed()
                await btn.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
                await page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


# ─── Main Listing Entry ────────────────────────────────────────────────────────

async def run_listing_scraper(page: Page) -> list[dict]:
    """
    Apply parameters, navigate pagination pages, and extract at least 30 bids.
    """
    await apply_awarded_filter(page)

    all_bids = []
    page_num = 0

    while len(all_bids) < TARGET_BIDS and page_num < MAX_PAGES:
        page_num += 1
        log.info(f"Scraping listing page {page_num} | collected={len(all_bids)}")

        bids = await scrape_current_page(page)
        log.info(f"  → {len(bids)} bids on page {page_num}")

        for bid in bids:
            upsert_bid(bid)
            all_bids.append(bid)

        if len(all_bids) >= TARGET_BIDS:
            break

        has_next = await go_next_page(page)
        if not has_next:
            log.info("No next page found - listing phase complete.")
            break

    log.info(f"Listing scrape finished. Collected total of {len(all_bids)} bids.")
    return all_bids
