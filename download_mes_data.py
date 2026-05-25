#!/usr/bin/env python3
"""
Download MES (Micro E-mini S&P 500) 1-minute OHLCV historical data from Databento.

Default range: 2023-03-25 ~ 2026-03-25 (matches dashboard MES Backtest period).
Output: MES_1min_data.csv in current directory.

Schema: ohlcv-1m
Dataset: GLBX.MDP3 (CME Globex)
Symbol:  MES.c.0  (continuous front-month, auto-rolled)

Cost note: ~3 years of 1-min OHLCV for one continuous contract is well under
$1 from Databento's $125 free credits.
"""
import os
import sys
import argparse
from datetime import datetime

# Force UTF-8 stdout on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    import databento as db
except ImportError:
    print("[ERROR] databento package not installed. Run: pip install databento")
    sys.exit(1)


def download(api_key: str, symbol: str, start: str, end: str, out_path: str):
    print("=" * 80)
    print(f"  Databento MES Download")
    print(f"  Dataset:  GLBX.MDP3")
    print(f"  Symbol:   {symbol}")
    print(f"  Schema:   ohlcv-1m")
    print(f"  Range:    {start} ~ {end}")
    print(f"  Output:   {out_path}")
    print("=" * 80)

    client = db.Historical(api_key)

    # Cost estimate first
    try:
        cost = client.metadata.get_cost(
            dataset="GLBX.MDP3",
            schema="ohlcv-1m",
            symbols=symbol,
            stype_in="continuous",
            start=start,
            end=end,
        )
        print(f"[*] Estimated cost: ${cost:.4f}")
    except Exception as e:
        print(f"[!] Cost estimate failed (continuing): {e}")

    print("[*] Fetching data...")
    t0 = datetime.now()
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        schema="ohlcv-1m",
        symbols=symbol,
        stype_in="continuous",
        start=start,
        end=end,
    )
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"[*] Downloaded in {elapsed:.1f}s.")

    print("[*] Converting to DataFrame...")
    df = data.to_df()
    if df.empty:
        print("[ERROR] Empty result. Check symbol/dates.")
        sys.exit(1)
    print(f"[*] Got {len(df):,} bars. Date range: {df.index.min()} ~ {df.index.max()}")

    # Normalize columns to match Polygon-style CSV (timestamp/open/high/low/close/volume)
    # Databento OHLCV-1m comes with index=ts_event (UTC tz-aware) and columns: open, high, low, close, volume, ...
    df_out = df[["open", "high", "low", "close", "volume"]].copy()
    df_out.index.name = "timestamp"
    # Strip tz so the existing backtest tz_localize("UTC") works the same way
    df_out.index = df_out.index.tz_convert("UTC").tz_localize(None)

    df_out.to_csv(out_path)
    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"[OK] Saved: {out_path} ({size_mb:.2f} MB, {len(df_out):,} rows)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="MES.c.0", help="Continuous front-month MES")
    p.add_argument("--start", default="2023-03-25")
    p.add_argument("--end", default="2026-03-25")
    p.add_argument("--out", default="MES_1min_data.csv")
    args = p.parse_args()

    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        print("[ERROR] DATABENTO_API_KEY not set in environment / .env")
        sys.exit(1)

    download(api_key, args.symbol, args.start, args.end, args.out)
