"""
pipeline.py — Main orchestrator for the GeM bid scraping pipeline.
Coordinater: db initialization -> listing scrape -> drilldown details -> data cleaning.
"""

import asyncio
import logging
import argparse
import sys
from playwright.async_api import async_playwright

from config import *
import db
from scraper_listing import run_listing_scraper
from scraper_drilldown import run_drilldown_scraper

# ─── Logging Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("pipeline")


async def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description="GemEdge Bid Scraper Pipeline")
    parser.add_argument("--force-listing", action="store_true", help="Force listing scraper to run even if DB has bids")
    parser.add_argument("--drilldown-only", action="store_true", help="Skip listing scrape and only run drilldown on pending bids")
    parser.add_argument("--listing-only", action="store_true", help="Only run listing scraper, skipping drilldown details")
    parser.add_argument("--headless", type=bool, default=None, help="Override headless mode in config")
    args = parser.parse_args()

    # Initialize Database
    log.info("Initializing database...")
    db.init_db()

    # Determine headless mode
    headless_mode = args.headless if args.headless is not None else HEADLESS
    log.info(f"Launching browser (headless={headless_mode}, slow_mo={SLOW_MO_MS}ms)...")

    async with async_playwright() as pw:
        # Launch chromium
        browser = await pw.chromium.launch(
            headless=headless_mode,
            slow_mo=SLOW_MO_MS,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1280,1024",
                "--disable-blink-features=AutomationControlled"  # Avoid simple bot detection
            ]
        )
        
        # Create standard context and page
        context = await browser.new_context(
            viewport={"width": 1280, "height": 1024},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            # ─── Phase 1: Listing Scraper ──────────────────────────────────────
            # Check how many bids we currently have
            all_bids = db.get_all_bids()
            log.info(f"Currently stored bids in database: {len(all_bids)}")

            run_listing = (
                args.force_listing
                or args.listing_only
                or (not args.drilldown_only and len(all_bids) < TARGET_BIDS)
            )
            
            if args.drilldown_only:
                log.info("Drilldown-only flag provided. Skipping listing phase.")
                run_listing = False

            if run_listing:
                log.info("Starting Listing Scraper phase...")
                # Run listing scraper (filters, paginates, extracts cards, and updates db)
                listed_bids = await run_listing_scraper(page)
                log.info(f"Listing Scraper phase finished. Scraped {len(listed_bids)} bids on this run.")
            else:
                log.info("Listing phase skipped (sufficient bids in DB or explicitly skipped).")

            # ─── Phase 2: Drilldown Scraper ────────────────────────────────────
            # Retrieve bids that need detailed results and evaluation scraping
            if args.listing_only:
                log.info("Listing-only flag provided. Skipping drilldown phase.")
            else:
                pending_bids = db.get_pending_bids()
                log.info(f"Bids pending detailed extraction: {len(pending_bids)}")

                if pending_bids:
                    log.info(f"Starting Drilldown Scraper phase for {len(pending_bids)} bids...")
                    # Run drilldown (opens result pages, clicks evaluation details, updates db)
                    await run_drilldown_scraper(page, pending_bids)
                    log.info("Drilldown Scraper phase finished.")
                else:
                    log.info("No bids pending drilldown details.")

        except Exception as e:
            log.exception(f"Pipeline crashed during execution: {e}")
        finally:
            log.info("Closing browser...")
            await browser.close()

    # ─── Phase 3: Data Cleaning & Insight Analysis ─────────────────────────────
    log.info("Starting Data Cleaning & Insight Generation phase...")
    try:
        import subprocess
        # Run cleaner.py to compile outputs and analysis using the current python executable
        import sys
        result = subprocess.run([sys.executable, "cleaner.py"], capture_output=True, text=True)
        if result.returncode == 0:
            log.info("Data cleaning completed successfully.")
            print(result.stdout)
        else:
            log.error(f"Cleaner failed: {result.stderr}")
    except Exception as e:
        log.exception(f"Failed to run data cleaning script: {e}")

    log.info("Pipeline execution complete.")


if __name__ == "__main__":
    asyncio.run(main())
