#!/usr/bin/env python3
"""
run.py — The ultimate interactive command-line dashboard and web server launcher 
for the GemEdge GeM Bid Scraper & Data Structuring assignment.
"""

import os
import sys
import subprocess
import sqlite3
import json
import time
import webbrowser
import http.server
import socketserver

# ANSI Colors for Premium Terminal aesthetics
BLUE    = "\033[1;34m"
GREEN   = "\033[1;32m"
YELLOW  = "\033[1;33m"
RED     = "\033[1;31m"
CYAN    = "\033[1;36m"
RESET   = "\033[0m"
BOLD    = "\033[1m"

VENV_PYTHON = "./venv/bin/python"

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Zero-dependency local server serving dashboard.html and the scraped bids dataset."""
    def log_message(self, format, *args):
        # Suppress noisy standard GET requests logging
        pass

    def do_GET(self):
        if self.path == '/data':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                with open('output/bids_final.json', 'r', encoding='utf-8') as f:
                    data = f.read()
                self.wfile.write(data.encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        elif self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            try:
                with open('output/dashboard.html', 'r', encoding='utf-8') as f:
                    html = f.read()
                self.wfile.write(html.encode('utf-8'))
            except Exception as e:
                self.wfile.write(f"Error loading dashboard: {e}".encode('utf-8'))
        elif self.path == '/bids_final.json':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            try:
                with open('output/bids_final.json', 'r', encoding='utf-8') as f:
                    data = f.read()
                self.wfile.write(data.encode('utf-8'))
            except Exception:
                self.wfile.write(b"[]")
        else:
            # Fallback to serving standard static file request from output or current dir
            super().do_GET()

def start_web_dashboard():
    """Starts the zero-dependency local web dashboard on an available port starting from 8000."""
    # Check if files exist, if not alert the user
    if not os.path.exists("output/bids_final.json"):
        print(f"\n{RED}[!] Warning: output/bids_final.json not found.{RESET}")
        print(f"{YELLOW}Please run the pipeline or data cleaning first before viewing the dashboard.{RESET}\n")

    handler = DashboardHandler
    socketserver.TCPServer.allow_reuse_address = True
    
    port = 8000
    httpd = None
    while port < 8020:
        try:
            httpd = socketserver.TCPServer(("", port), handler)
            break
        except OSError as e:
            # Errno 48 is address in use on macOS/Linux, 98 on Linux, 10048 on Windows
            if e.errno in [48, 98, 10048]:
                port += 1
            else:
                print(f"{YELLOW}[i] Port {port} occupied or unavailable: {e}. Trying next...{RESET}")
                port += 1
                
    if not httpd:
        print(f"\n{RED}[x] Failed to start Web Dashboard: No free ports between 8000 and 8020.{RESET}")
        return

    try:
        with httpd:
            url = f"http://localhost:{port}"
            print(f"\n{GREEN}[✓] Web Dashboard server started successfully at {url}{RESET}")
            print(f"{CYAN}[i] Press Ctrl+C in this menu to shut down the server.{RESET}\n")
            # Automatically open browser
            webbrowser.open(url)
            httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}[i] Shutting down Web Dashboard server...{RESET}")
    except Exception as e:
        print(f"\n{RED}[x] Failed to start Web Dashboard: {e}{RESET}")

def print_header():
    os.system('clear' if os.name == 'posix' else 'cls')
    print(f"{BLUE}" + "=" * 70)
    print(f"       {BOLD}GemEdge {CYAN}INTELLIGENT PROCUREMENT — PIPELINE CONTROLLER{RESET}")
    print(f"{BLUE}" + "=" * 70 + RESET)

def view_terminal_stats():
    """Reads the SQLite database directly and outputs complete data statistics."""
    DB_FILE = "gem_bids.db"
    if not os.path.exists(DB_FILE):
        print(f"\n{RED}[x] Error: Database file {DB_FILE} does not exist.{RESET}")
        print(f"{YELLOW}Run Option 1 or 2 first to initialize the database.{RESET}")
        return

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT count(*) FROM bids")
        bids_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM vendors")
        vendors_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM bids WHERE scrape_status = 'done'")
        done_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM bids WHERE scrape_status = 'error'")
        error_count = cursor.fetchone()[0]

        conn.close()

        print(f"\n{BOLD}{CYAN}Live Database Statistics:{RESET}")
        print(f"  - Total Scraped Bids:             {GREEN}{bids_count}{RESET}")
        print(f"  - Rich Vendor Profiles:           {GREEN}{vendors_count}{RESET}")
        print(f"  - Detailed Extraction Completed:  {GREEN}{done_count}{RESET}")
        print(f"  - Bypassed / Direct Redirection:  {YELLOW}{error_count}{RESET}")

        # Run cleaner.py to fetch summary metrics
        print(f"\n{BOLD}{CYAN}Executing Anomaly Engine & Insight Generator...{RESET}")
        subprocess.run([VENV_PYTHON, "cleaner.py"])
        
    except Exception as e:
        print(f"\n{RED}[x] Error reading database statistics: {e}{RESET}")

def reset_database():
    """Prompts and resets the SQLite database to start fresh."""
    confirm = input(f"\n{RED}[!] CAUTION: Are you sure you want to delete and reset the SQLite database? (y/N): {RESET}").strip().lower()
    if confirm == 'y':
        DB_FILE = "gem_bids.db"
        if os.path.exists(DB_FILE):
            try:
                os.remove(DB_FILE)
                print(f"{GREEN}[✓] Database {DB_FILE} deleted successfully.{RESET}")
            except Exception as e:
                print(f"{RED}[x] Error deleting database: {e}{RESET}")
        
        # Re-initialize
        print(f"{CYAN}[i] Initializing new database schema...{RESET}")
        subprocess.run([VENV_PYTHON, "db.py"])
        print(f"{GREEN}[✓] Clean database schema created.{RESET}")
    else:
        print(f"{BLUE}[i] Database reset aborted.{RESET}")

def check_venv():
    """Verify that virtual environment exists and is functional."""
    if not os.path.exists(VENV_PYTHON):
        print(f"{RED}[x] Python virtual environment not found at {VENV_PYTHON}!{RESET}")
        print(f"{YELLOW}Please configure the virtual environment and install requirements first:{RESET}")
        print("  python3 -m venv venv")
        print("  ./venv/bin/pip install -r requirements.txt")
        return False
    return True

def main_menu():
    if not check_venv():
        sys.exit(1)

    while True:
        print_header()
        print(f"{BOLD}Choose an action to coordinate the Bid Extraction Pipeline:{RESET}\n")
        print(f"  {CYAN}[1]{RESET} {BOLD}Run Full Scraper Pipeline{RESET} (Filters -> Scrapes Listing -> Drills Down Result -> Clean)")
        print(f"  {CYAN}[2]{RESET} Run Listing-Level Scraper Only (Filters & gathers direct links only)")
        print(f"  {CYAN}[3]{RESET} Run Drilldowns & Deep Evaluation Only (Enriches pending bids)")
        print(f"  {CYAN}[4]{RESET} Run Data Cleaning & Normalization Report Only")
        print(f"  {CYAN}[5]{RESET} {BOLD}Launch Premium Web Dashboard{RESET} (Served locally on port 8000)")
        print(f"  {CYAN}[6]{RESET} View Live Scraped Insights & DB Stats")
        print(f"  {CYAN}[7]{RESET} {RED}Reset SQLite Database{RESET} (Delete all cached records)")
        print(f"  {CYAN}[8]{RESET} Exit")
        print(f"\n{BLUE}" + "=" * 70 + RESET)
        
        choice = input(f"{BOLD}Enter choice [1-8]: {RESET}").strip()
        
        if choice == '1':
            print_header()
            print(f"{GREEN}[i] Starting Full Pipeline execution...{RESET}")
            subprocess.run([VENV_PYTHON, "pipeline.py"])
            input(f"\n{YELLOW}Press Enter to return to menu...{RESET}")
            
        elif choice == '2':
            print_header()
            print(f"{GREEN}[i] Running Listing Card Scraper only...{RESET}")
            subprocess.run([VENV_PYTHON, "pipeline.py", "--force-listing", "--listing-only"])
            input(f"\n{YELLOW}Press Enter to return to menu...{RESET}")
            
        elif choice == '3':
            print_header()
            print(f"{GREEN}[i] Running Evaluation Drilldown only on pending bids...{RESET}")
            subprocess.run([VENV_PYTHON, "pipeline.py", "--drilldown-only"])
            input(f"\n{YELLOW}Press Enter to return to menu...{RESET}")
            
        elif choice == '4':
            print_header()
            print(f"{GREEN}[i] Executing cleaning and normalization processes...{RESET}")
            subprocess.run([VENV_PYTHON, "cleaner.py"])
            input(f"\n{YELLOW}Press Enter to return to menu...{RESET}")
            
        elif choice == '5':
            print_header()
            print(f"{GREEN}[i] Launching local Web Dashboard...{RESET}")
            start_web_dashboard()
            input(f"\n{YELLOW}Press Enter to return to menu...{RESET}")
            
        elif choice == '6':
            print_header()
            view_terminal_stats()
            input(f"\n{YELLOW}Press Enter to return to menu...{RESET}")
            
        elif choice == '7':
            print_header()
            reset_database()
            input(f"\n{YELLOW}Press Enter to return to menu...{RESET}")
            
        elif choice == '8':
            print(f"\n{GREEN}Thank you for choosing GemEdge. Exiting...{RESET}\n")
            break
            
        else:
            print(f"\n{RED}[!] Invalid choice. Please select between 1 and 8.{RESET}")
            time.sleep(1.5)

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}[i] Operation cancelled by user. Exiting pipeline controller...{RESET}\n")
