"""
Standalone connectivity test for public prediction market APIs.

Run before starting the full system to verify network access:
    python scripts/check_endpoints.py
"""
import json
import sys

import httpx

GAMMA = "https://gamma.polymarket.com"
CLOB = "https://clob.polymarket.com"


def check(label: str, url: str, params: dict | None = None):
    try:
        r = httpx.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        n = len(data) if isinstance(data, list) else 1
        print(f"[OK]  {label}: {n} record(s)")
        return data
    except httpx.ConnectError as e:
        print(f"[ERR] {label}: DNS/connection failed — {e}")
        print("      Check your internet connection or firewall settings.")
        return None
    except httpx.HTTPStatusError as e:
        print(f"[ERR] {label}: HTTP {e.response.status_code} — {e.response.text[:200]}")
        return None
    except Exception as e:
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
        return None


def main():
    print("=== Polymarket CLOB endpoint check ===\n")
    print("  (scanner now uses CLOB API only — Gamma DNS issues don't matter)\n")

    # 1. CLOB /markets — primary market list endpoint
    resp = check("CLOB /markets (active, limit=3)", f"{CLOB}/markets", {"active": "true", "limit": 3})

    token_id = None
    if resp and isinstance(resp, dict):
        markets_data = resp.get("data", [])
        print(f"  Markets returned: {len(markets_data)}")
        if markets_data:
            sample = markets_data[0]
            print(f"  Question:    {sample.get('question', 'n/a')[:80]}")
            print(f"  Active:      {sample.get('active')}")
            print(f"  Closed:      {sample.get('closed')}")
            print(f"  volume_num:  {sample.get('volume_num')}")
            print(f"  volume:      {sample.get('volume')}")
            print(f"  end_date_iso:{sample.get('end_date_iso') or sample.get('endDateIso')}")
            print(f"  condition_id:{str(sample.get('condition_id', sample.get('conditionId', 'n/a')))[:30]}")
            print(f"  Token fields (first token): {list(sample.get('tokens', [{}])[0].keys()) if sample.get('tokens') else 'none'}")
            for tok in sample.get("tokens", []):
                if tok.get("outcome", "").lower() == "yes":
                    token_id = tok.get("token_id")
                    price = tok.get("price", "n/a")
                    print(f"  YES price:   {price}  token_id: {str(token_id)[:30]}...")
                    break
            # Show ALL fields available so we can see what the API returns
            print(f"\n  All top-level keys: {list(sample.keys())}")

    print()

    # 2. CLOB /book — orderbook for a YES token
    if token_id:
        book = check("CLOB /book (YES token)", f"{CLOB}/book", {"token_id": token_id})
        if book:
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            print(f"  Bids: {len(bids)}, Asks: {len(asks)}")

    print()

    # Summary
    if resp:
        print("CLOB endpoints reachable. Ready to run main.py.")
        sys.exit(0)
    else:
        print("CLOB unreachable. Check internet connection.")
        sys.exit(1)


if __name__ == "__main__":
    main()
