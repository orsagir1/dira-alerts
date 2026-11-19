"""
Checks "מספר הגרלות פתוחות" on https://dira.moch.gov.il/ProjectsList
and sends a push notification via ntfy.sh when it is greater than 0.

State (last seen value) is kept in state.json so you get ONE notification
when lotteries open, not one every 30 minutes.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://dira.moch.gov.il/ProjectsList"
LABEL = "מספר הגרלות פתוחות"
STATE_FILE = Path(__file__).with_name("state.json")
NTFY_TOPIC = os.environ["NTFY_TOPIC"]          # set as a GitHub secret
FORCE_REPORT = os.environ.get("FORCE_REPORT") == "true"  # manual test run


def notify(title: str, message: str, priority: str = "high") -> None:
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            # HTTP headers must be ASCII, so the Hebrew goes in the body
            "Title": title,
            "Priority": priority,
            "Click": URL,
            "Tags": "house",
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30)


# Runs inside the page: find the element holding the label, then climb up
# until a number appears next to it.
JS_FIND_COUNT = r"""
(label) => {
  const all = Array.from(document.querySelectorAll('body *'));
  // smallest element whose text contains the label
  const hits = all.filter(e => (e.textContent || '').includes(label))
                  .sort((a, b) => a.textContent.length - b.textContent.length);
  if (!hits.length) return null;
  let el = hits[0];
  for (let i = 0; i < 4 && el; i++, el = el.parentElement) {
    const rest = el.textContent.replace(label, ' ');
    const m = rest.match(/\d+/);
    if (m) return { count: parseInt(m[0], 10), context: el.textContent.trim().slice(0, 200) };
  }
  return null;
}
"""


def read_count() -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="he-IL")
        page.goto(URL, wait_until="networkidle", timeout=90_000)
        page.wait_for_function(
            "(l) => document.body.innerText.includes(l)", arg=LABEL, timeout=60_000
        )
        page.wait_for_timeout(3000)  # let the numbers finish rendering
        result = page.evaluate(JS_FIND_COUNT, LABEL)
        if result is None:
            page.screenshot(path="debug.png", full_page=True)
        browser.close()
    if result is None:
        raise RuntimeError(f"Found the page but no number next to '{LABEL}'")
    return result


def main() -> None:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    prev = state.get("count")
    now = datetime.now(timezone.utc)

    try:
        result = read_count()
    except Exception as e:  # site down / blocked / layout changed
        print("ERROR:", e)
        if state.get("status") != "error":  # alert once, not every run
            notify("Dira alert: check FAILED",
                   f"הבדיקה נכשלה: {e}\nכדאי להיכנס ל-GitHub Actions ולבדוק.",
                   priority="default")
        state["status"] = "error"
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        sys.exit(1)

    count = result["count"]
    print(f"{LABEL}: {count}   (context: {result['context']!r})")

    if FORCE_REPORT:
        notify("Dira alert: test", f"הבדיקה עובדת ✅\n{LABEL}: {count}", priority="default")
    elif count > 0 and (prev is None or prev == 0):
        notify("Dira: OPEN LOTTERY!", f"🏠 נפתחו הגרלות!\n{LABEL}: {count}", priority="urgent")
    elif state.get("status") == "error":
        notify("Dira alert: back to normal", f"הבדיקה עובדת שוב. {LABEL}: {count}",
               priority="low")

    state.update({
        "count": count,
        "status": "ok",
        # changes once a month -> a commit keeps GitHub from pausing the schedule
        "heartbeat_month": now.strftime("%Y-%m"),
    })
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
