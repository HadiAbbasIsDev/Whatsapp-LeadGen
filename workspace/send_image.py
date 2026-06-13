#!/usr/bin/env python3
"""
Sends a product image WITH a caption via WhatsApp as a SINGLE message.

Remote image URLs (from products.json) are downloaded into a local cache the
first time they're used, so repeat sends are fast and don't depend on
renovate.pk being reachable each time. Falls back to sending the URL directly
if the download fails.

Usage:
  python3 ./send_image.py --image <url-or-path> --to +923XXXXXXXXX --caption "Item ... PKR ..."
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "cache")
# database/images lives one level up from the workspace dir
DB_IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "images")


def resolve_openclaw():
    """Find the openclaw binary even if it's not on PATH."""
    found = shutil.which("openclaw")
    if found:
        return found
    for candidate in (
        "/usr/local/node-v22.21.1/bin/openclaw",
        os.path.expanduser("~/.npm-global/bin/openclaw"),
        "/usr/local/bin/openclaw",
    ):
        if os.path.exists(candidate):
            return candidate
    return "openclaw"  # last resort; errors clearly if missing


def local_path_for(image):
    """Return a usable local file path for the image, caching remote URLs.

    Returns None if it can't produce a local file (caller falls back to the URL).
    """
    if not image.lower().startswith(("http://", "https://")):
        # Local path. If it exists, use it.
        if os.path.exists(image):
            return image
        # Foreign/broken path (e.g. a Windows "D:\...\5903.jpg" from another
        # machine): try to recover by basename from the local database/images dir.
        base = os.path.basename(image.replace("\\", "/"))
        if base:
            local = os.path.join(DB_IMAGES_DIR, base)
            if os.path.exists(local):
                return local
        return None

    os.makedirs(CACHE_DIR, exist_ok=True)
    base = os.path.basename(image.split("?")[0]) or "img"
    if "." not in base:
        base += ".jpg"
    short = hashlib.sha1(image.encode()).hexdigest()[:8]
    dest = os.path.join(CACHE_DIR, f"{short}_{base}")

    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest  # cache hit

    try:
        req = urllib.request.Request(image, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        if os.path.getsize(dest) > 0:
            return dest
        os.remove(dest)
    except Exception as e:
        print(f"[warn] image download failed ({e}); will send URL directly", file=sys.stderr)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image",   required=True, help="Image file path or URL")
    parser.add_argument("--to",      required=True, help="Recipient E.164 phone number")
    parser.add_argument("--caption", default="",    help="Caption sent with the image (one message)")
    args = parser.parse_args()

    media = local_path_for(args.image) or args.image  # fall back to URL if caching failed

    cmd = [
        resolve_openclaw(), "message", "send",
        "--channel", "whatsapp",
        "--target", args.to,
        "--media", media,
    ]
    if args.caption:
        cmd += ["--message", args.caption]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"[OK] sent to {args.to} (media: {media})")
    else:
        print(f"[FAIL] {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
