"""
config.py — All active scraper settings and selectors in one place.
"""

# ── Scraper Behaviour ──────────────────────────────────────────────
BASE_URL        = "https://bidplus.gem.gov.in/all-bids"
TARGET_BIDS     = 30        # minimum bids to extract
MAX_PAGES       = 20        # safety cap on pagination
PAGE_TIMEOUT_MS = 30_000    # Playwright page timeout

# ── Browser ───────────────────────────────────────────────────────
HEADLESS        = True     # set True for background run; False for headed debug
SLOW_MO_MS      = 150       # slows Playwright actions to avoid JS race conditions

# ── Output ────────────────────────────────────────────────────────
OUTPUT_CSV      = "output/bids_final.csv"
OUTPUT_JSON     = "output/bids_final.json"
LOG_FILE        = "logs/scraper.log"

# ── DOM Selectors ─────────────────────────────────────────────────
SEL_BID_CARD         = "div#bidCard div.card"
SEL_BID_NUMBER       = "a.bid_no_hover"
SEL_RESULT_LINK      = "a[href*='getBidResultView'], a[href*='getSinglePacketResultView'], a[href*='ResultView']"
SEL_CATEGORY_POPOVER  = "a[data-toggle='popover']"
SEL_BUYER_COLUMN     = "div.col-md-5"
