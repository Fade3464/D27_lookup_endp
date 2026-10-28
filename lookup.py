#!/usr/bin/env python3
"""Call the supplied page's ping.php and print one campaign's lookup result.

Usage: python3 lookup.py 21625 2345678901
The PHP endpoint checks both campaigns; this client selects the requested result.
"""

import argparse
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", help='Campaign line or label, e.g. 21625 or "D27 (1)"')
    parser.add_argument("number", help="10-digit ANI; optional leading US country code")
    parser.add_argument(
        "--url",
        default="https://marstools.co/d27suppressioncheck/ping.php",
        help="Override the hosted ping.php URL",
    )
    args = parser.parse_args()

    number = re.sub(r"[^0-9]", "", args.number)
    if len(number) == 11 and number.startswith("1"):
        number = number[1:]
    if len(number) != 10:
        parser.error("number must contain 10 digits (or 11 starting with 1)")
    if urlsplit(args.url).scheme not in ("http", "https") or not urlsplit(args.url).netloc:
        parser.error("--url must be a full HTTP or HTTPS URL")

    request = Request(
        args.url,
        data=json.dumps({"ani": number}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, OSError) as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    try:
        data = json.loads(body)
    except ValueError:
        print(body)
        print("Expected a JSON response from ping.php.", file=sys.stderr)
        return 1
    if not isinstance(data, dict) or data.get("error"):
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 1

    results = data.get("results")
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and args.campaign.strip().casefold() in (
                str(item.get("line", "")).casefold(),
                str(item.get("label", "")).casefold(),
            ):
                print(json.dumps(item.get("result", item), indent=2, ensure_ascii=False))
                return 0

    print(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"No result found for campaign {args.campaign!r}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
