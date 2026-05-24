#!/usr/bin/env python3
"""
Sends a product image via WhatsApp to a specific customer number.
The agent calls this script when displaying a product.

Usage:
  python3 ./send_image.py --image /abs/path/to/image.jpg --to +921234567890
  python3 ./send_image.py --image /abs/path/to/image.jpg --to +921234567890 --caption "Product name"
"""

import argparse
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image",   required=True, help="Absolute path to image file")
    parser.add_argument("--to",      required=True, help="Recipient E.164 phone number")
    parser.add_argument("--caption", default="",    help="Optional caption text")
    args = parser.parse_args()

    cmd = [
        "openclaw", "message", "send",
        "--channel", "whatsapp",
        "--target", args.to,
        "--media", args.image,
    ]
    if args.caption:
        cmd += ["--message", args.caption]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"[OK] Image sent to {args.to}")
    else:
        print(f"[FAIL] {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
