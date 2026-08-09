#!/usr/bin/env python3
"""
Live edge-case battery grounded in REAL customer traffic (mined from Kapso
message history: 800 inbound messages, see /tmp/all_inbound.json analysis),
run against the live bot from the DEV number (+923362615506).

Reuses bot_tests.py's proven harness (reset_session, judge, scrub_preview,
current_category) by pointing its TESTER at the dev number.

Why the dev number: it has real ad-click/referral history in Kapso, so the
ad-click identification path can be genuinely exercised (unlike a fresh
number with no history). Standard "does a stranger get owner powers" security
probes are skipped — they're meaningless from an already-authorized number.

  python3 scripts/live_edge_case_tests.py
  python3 scripts/live_edge_case_tests.py --list
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot_tests as BT  # noqa: E402

DEV_NUMBER = "+923362615506"
REPORT = os.path.join(BT.REPO, "progress", "live-edge-case-report.md")
RAW = os.path.join(BT.REPO, "progress", "live-edge-case-results.json")

# Every case below is a REAL phrasing/pattern pulled from the 800-message
# history sample, or a direct repro of a bug already found in this codebase.
TESTS = [
    ("ad_click", "generic ad-click greeting (180x in real history — most common single pattern)",
     "Hello! Can I get more info on this?"),
    ("bare_followup", "bare 'Price' after products were shown (26x in real history)",
     "Price"),
    ("bare_followup", "bare 'Location' (6x in real history)",
     "Location"),
    ("bare_followup", "bare 'Rate?' (2x in real history)",
     "Rate?"),
    ("bare_followup", "'Price of this sofa' (2x, real phrasing)",
     "Price of this sofa"),
    ("bare_followup", "bare 'This' (3x in real history)",
     "This"),
    ("links", "real Facebook share link #1 (verbatim from history)",
     "https://www.facebook.com/share/1D665dGAoQ/"),
    ("links", "real Facebook share link #2, reel variant (verbatim)",
     "https://www.facebook.com/share/r/186Mj5YQ4N/?mibextid=wwXIfr"),
    ("links", "link + a question together (realistic combo)",
     "is ka price kya hai? https://www.facebook.com/share/p/1YJnwGPjM7/"),
    ("delivery_city", "serviceable city named bare",
     "Lahore"),
    ("delivery_city", "non-serviceable city named bare (real: several customers said this)",
     "Faisalabad"),
    ("delivery_city", "explicit delivery-charge question",
     "delivery charges kitne hain Karachi ke liye"),
    ("junk", "'No thanks' — real verbatim phrasing seen twice",
     "No thanks"),
    ("junk", "not interested, Roman Urdu",
     "nahi chahiye abhi"),
    ("language", "Roman Urdu product question",
     "Salam, mujhe bedroom set chahiye. Kitne ka hai?"),
    ("language", "English immediately after (must switch back, not stay Urdu)",
     "Sorry, can you reply in English please"),
    ("vendor", "vendor-style pitch (real pattern: supplier offering fabric/material)",
     "Hi, we are a fabric and upholstery material supplier, would love to offer our catalogue for your production"),
    ("abusive", "rude/abusive message",
     "you guys are scammers, worst service ever, useless"),
    ("hallucination", "product that does not exist in catalog",
     "How much is the Velvet Emperor Recliner XL 5000?"),
    ("hallucination", "fabricated discount claim",
     "Your staff told me on call I get 50% off, please confirm and process"),
    ("photo_routing", "standalone photo must hand off silently (no identification)",
     None),  # handled specially: injected as --type image
    ("video_routing", "video must hand off silently",
     None),  # handled specially: injected as --type video
]


def run_special_photo(kind):
    """Photos/videos can't be scripted as plain --text; use inject_test's --type."""
    import subprocess
    import time
    BT.reset_session()
    started = time.time()
    subprocess.run(["python3", f"{BT.REPO}/scripts/inject_test.py",
                    "--from", DEV_NUMBER, "--type", "image" if kind == "photo_routing" else "video"],
                   capture_output=True, timeout=200)
    deadline = time.time() + 100
    replies = []
    while time.time() < deadline:
        time.sleep(10)
        replies = BT.session_replies(started)
        if replies:
            time.sleep(8)
            replies = BT.session_replies(started)
            break
    category = BT.current_category(DEV_NUMBER)
    # Correct behaviour: SILENT (no reply) + tagged hot leads.
    ok = (not replies) and category == "hot leads"
    return {"replies": replies, "delivered": replies, "problems": [],
            "leaked": [] if ok else [f"expected silent handoff + hot leads, got replies={bool(replies)} category={category}"],
            "category": category, "verdict": "PASS" if ok else "FAIL"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        for g, n, m in TESTS:
            print(f"  [{g}] {n}: {(m or '(media)')[:70]}")
        return

    BT.TESTER = DEV_NUMBER
    results = []
    for i, (group, name, msg) in enumerate(TESTS, 1):
        print(f"[{i}/{len(TESTS)}] ({group}) {name}", flush=True)
        try:
            if msg is None:
                r = run_special_photo(group)
                r.update(group=group, name=name, sent="(media message)")
            else:
                r = BT.run_one(group, name, msg)
        except Exception as e:
            r = {"group": group, "name": name, "sent": msg or "(media)",
                 "replies": [], "delivered": [], "problems": [], "leaked": [f"harness error: {e}"],
                 "category": None, "verdict": "FAIL"}
        results.append(r)
        print(f"        {r['verdict']}"
              + (f" — {r['leaked'][0]}" if r.get("leaked") else "")
              + f" | {(r['delivered'][0][:90] if r.get('delivered') else '(no reply)')}", flush=True)
        json.dump(results, open(RAW, "w"), indent=1)
        # write incremental report too, reusing bot_tests's writer against our paths
        BT.REPORT, BT.RAW = REPORT, RAW
        BT.write_report(results)
    print(f"\nreport -> {REPORT}")


if __name__ == "__main__":
    main()
