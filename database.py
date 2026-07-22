"""
Legacy PostgreSQL connector — not used by any current AETHER module.
Kept for historical reference only.

Set the connection URL in config.json under "database.url", or via the
DATABASE_URL environment variable:
    DATABASE_URL=postgresql://user:pass@host:port/dbname
"""
import json
import sqlalchemy as db

try:
    from config import CFG as _CFG
    _DB_URL = _CFG.database_url
except Exception:
    import os
    _DB_URL = os.environ.get("DATABASE_URL", "")


def connect_to_db():
    if not _DB_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    engine = db.create_engine(_DB_URL)
    conn = engine.connect()
    output = conn.execute("SELECT * FROM test_table")
    print(output.fetchall())
    conn.close()


def update_daily_ohlcv_from_file():
    with open("Data\\symbols_to_check.txt", "r") as f:
        for line in f.readlines():
            symbol = line.strip().split()[-1]
            update_daily_ohlcv(symbol)


def update_daily_ohlcv(symbol: str):
    if not _DB_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    engine = db.create_engine(_DB_URL)
    conn = engine.connect()
    try:
        with open(f"Data\\Symbol_full\\{symbol}_daily.json", "r") as f:
            data = json.load(f).get('Time Series (Daily)', {})
            for date, value in sorted(data.items()):
                ss = [float(value.get("1. open", 0)),
                      float(value.get("2. high", 0)),
                      float(value.get("3. low", 0)),
                      float(value.get("4. close", 0)),
                      int(value.get("5. volume", 0))]
                output = conn.execute(
                    "SELECT * FROM daily_ohlcv WHERE date=:d AND symbol=:s",
                    {"d": date, "s": symbol.upper()}
                ).fetchall()
                if not output:
                    conn.execute(
                        "INSERT INTO daily_ohlcv(symbol, date, open, high, low, close, volume) "
                        "VALUES (:sym, :d, :o, :h, :l, :c, :v)",
                        {"sym": symbol, "d": date, "o": ss[0], "h": ss[1],
                         "l": ss[2], "c": ss[3], "v": ss[4]}
                    )
                    print(f"Inserted {symbol} {date}")
                elif float(output[0][3]) != float(ss[0]):
                    conn.execute(
                        "UPDATE public.daily_ohlcv "
                        "SET open=:o, high=:h, low=:l, close=:c, volume=:v "
                        "WHERE date=:d AND symbol=:s",
                        {"o": ss[0], "h": ss[1], "l": ss[2], "c": ss[3],
                         "v": ss[4], "d": date, "s": symbol.upper()}
                    )
                    print(f"Updated {symbol} {date}")
    except Exception as ex:
        print(ex)
    finally:
        conn.close()


if __name__ == '__main__':
    update_daily_ohlcv_from_file()
