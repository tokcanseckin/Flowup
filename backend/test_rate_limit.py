#!/usr/bin/env python3
"""
Rate-limit + alerting smoke test.

Tests:
  1. forgot-password respects 3/15min limit (4th request → 429)
  2. Retry-After header is present and positive
  3. Subsequent requests still 429 (not suddenly allowed)
  4. Admin reports API shows a new rate_limit Report after breach
  5. No second Report created when breaching again (dedup within 24h)
"""

import base64
import json
import os
import sys
import time

import requests

BASE = "https://singoling.com"
FP_URL = f"{BASE}/api/auth/forgot-password"
REPORTS_URL = f"{BASE}/api/admin/reports"

# ── admin credentials from env or ~/.credentials ─────────────────────────────
ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

if not ADMIN_EMAIL or not ADMIN_PASSWORD:
    # Try sourcing from ~/.credentials file
    creds_path = os.path.expanduser("~/.credentials")
    if os.path.exists(creds_path):
        with open(creds_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export ADMIN_EMAIL="):
                    ADMIN_EMAIL = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("export ADMIN_PASSWORD="):
                    ADMIN_PASSWORD = line.split("=", 1)[1].strip().strip('"').strip("'")

ADMIN_AUTH = (
    base64.b64encode(f"{ADMIN_EMAIL}:{ADMIN_PASSWORD}".encode()).decode()
    if ADMIN_EMAIL and ADMIN_PASSWORD else None
)

BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"

passed = 0
failed = 0


def ok(msg: str) -> None:
    global passed
    passed += 1
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    global failed
    failed += 1
    print(f"  {RED}✗{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {YELLOW}·{RESET} {msg}")


def hit_forgot_password(n: int = 1) -> list[requests.Response]:
    """Send n requests to forgot-password with a fake email."""
    responses = []
    for _ in range(n):
        r = requests.post(FP_URL, json={"email": "test-rate-limit@example.invalid"}, timeout=10)
        responses.append(r)
    return responses


def get_latest_rate_limit_report() -> dict | None:
    """Fetch admin reports and return the most recent rate_limit entry, or None."""
    if not ADMIN_AUTH:
        return None
    r = requests.get(
        REPORTS_URL,
        headers={"Authorization": f"Basic {ADMIN_AUTH}"},
        timeout=10,
    )
    if r.status_code != 200:
        info(f"Admin reports fetch failed: {r.status_code}")
        return None
    reports = r.json()
    rl_reports = [x for x in reports if x.get("kind") == "rate_limit"]
    return rl_reports[0] if rl_reports else None


# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{BOLD}Rate-limit smoke test — {FP_URL}{RESET}")
print(f"Limit: 3 req / 15 min\n")

# ── Test 1: first 3 requests must not be rate-limited ─────────────────────────
print(f"{BOLD}[1] First 3 requests should not be rate-limited{RESET}")
responses = hit_forgot_password(3)
blocked = [r for r in responses if r.status_code == 429]
if not blocked:
    ok(f"Requests 1-3 all returned {[r.status_code for r in responses]} (none blocked)")
else:
    fail(f"Unexpected 429 within first 3 requests: {[r.status_code for r in responses]}")
    info("The 15-min window may already be active from a previous test run. Wait and retry.")

# ── Test 2: 4th request must be rate-limited ──────────────────────────────────
print(f"\n{BOLD}[2] 4th request should be blocked (429){RESET}")
r4 = hit_forgot_password(1)[0]
if r4.status_code == 429:
    ok(f"Got 429 as expected")
else:
    fail(f"Expected 429, got {r4.status_code}")

# ── Test 3: Retry-After header ────────────────────────────────────────────────
print(f"\n{BOLD}[3] Retry-After header{RESET}")
retry_after = r4.headers.get("Retry-After")
if retry_after is not None:
    try:
        secs = int(retry_after)
        if 0 < secs <= 15 * 60:
            ok(f"Retry-After: {secs}s (within expected 15-min window)")
        else:
            fail(f"Retry-After value out of range: {secs}")
    except ValueError:
        fail(f"Retry-After is not an integer: {retry_after!r}")
else:
    fail("Retry-After header missing from 429 response")

# ── Test 4: body check ────────────────────────────────────────────────────────
print(f"\n{BOLD}[4] 429 response body{RESET}")
try:
    body = r4.json()
    detail = body.get("detail", "")
    if "too many" in detail.lower() or "rate" in detail.lower():
        ok(f"detail: {detail!r}")
    else:
        fail(f"Unexpected detail: {detail!r}")
except Exception:
    fail(f"Could not parse JSON from 429 response: {r4.text[:100]}")

# ── Test 5: 5th request still blocked ────────────────────────────────────────
print(f"\n{BOLD}[5] 5th request should still be blocked{RESET}")
r5 = hit_forgot_password(1)[0]
if r5.status_code == 429:
    ok("Still 429 on 5th request")
else:
    fail(f"Expected 429, got {r5.status_code}")

# ── Test 6: Report created in DB ─────────────────────────────────────────────
print(f"\n{BOLD}[6] Report created in database{RESET}")
if not ADMIN_AUTH:
    info("No admin credentials available — skipping DB check")
    info("Set ADMIN_EMAIL and ADMIN_PASSWORD env vars to enable this check")
else:
    report = get_latest_rate_limit_report()
    if report:
        ok(f"Found rate_limit Report id={report['id']}, context={report.get('context')!r}")
        try:
            msg = json.loads(report.get("message") or "{}")
            info(f"  ip={msg.get('ip')!r}, endpoint={msg.get('endpoint')!r}, "
                 f"attempts={msg.get('attempt_count')}, ua={str(msg.get('user_agent', ''))[:40]!r}")
        except Exception:
            pass
    else:
        fail("No rate_limit Report found in admin reports")

# ── Test 7: dedup — second breach must not create another Report ──────────────
print(f"\n{BOLD}[7] Breach again — no duplicate Report within 24 h{RESET}")
if not ADMIN_AUTH:
    info("Skipping (no admin credentials)")
else:
    report_before = get_latest_rate_limit_report()
    id_before = report_before["id"] if report_before else None

    hit_forgot_password(2)   # both should 429; no new Report
    time.sleep(0.5)          # give the server a moment

    report_after = get_latest_rate_limit_report()
    id_after = report_after["id"] if report_after else None

    if id_before == id_after:
        ok(f"No new Report created (still id={id_after})")
    else:
        fail(f"New Report created unexpectedly: before={id_before}, after={id_after}")

# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'─'*50}")
total = passed + failed
colour = GREEN if failed == 0 else RED
print(f"{colour}{BOLD}{passed}/{total} passed{RESET}\n")
sys.exit(0 if failed == 0 else 1)
