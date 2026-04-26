import json
import sqlalchemy as db


def connect_to_db():
    engine = db.create_engine("postgresql://root:yu_75299527_yu@10.0.0.156:2665/market_DB")
    conn = engine.connect()
    output = conn.execute("SELECT * FROM test_table")
    print(output.fetchall())
    conn.close()


def update_daily_ohlcv_from_file():
    with open("Data\\symbols_to_check.txt", "r") as f:
        for line in f.readlines():
            split_line = line.strip().split()
            symbol = split_line[-1]
            update_daily_ohlcv(symbol)


def update_daily_ohlcv(symbol: str):
    engine = db.create_engine("postgresql://root:yu_75299527_yu@10.0.0.156:2665/market_DB")
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
                query = f"SELECT * FROM daily_ohlcv WHERE date='{date}' AND symbol='{symbol.upper()}'"
                output = conn.execute(query).fetchall()
                if not output:
                    conn.execute(f"INSERT INTO daily_ohlcv(symbol, date, open, high, low, close, volume) "
                                 f"VALUES ('{symbol}', '{date}', {ss[0]}, {ss[1]}, {ss[2]}, {ss[3]}, {ss[4]}); ")
                    print(f"Inserted {symbol} {date}")
                    # break
                elif float(output[0][3]) != float(ss[0]):
                    conn.execute(f"UPDATE public.daily_ohlcv "
                                 f"SET open={ss[0]}, high={ss[1]}, low={ss[2]}, close={ss[3]}, volume={ss[4]} "
                                 f"WHERE date='{date}' AND symbol='{symbol.upper()}'")
                    print(f"Updated {symbol} {date} {output[0][3]}  ---  {ss[0]}")
                else:
                    continue
                    print(f"Skipped {symbol} {date} {output[0][3]}  ---  {ss[0]}")

    except Exception as ex:
        print(ex)
    finally:
        conn.close()


def getSum(n):
    sum = 0
    for digit in str(n).replace('.', '') :
        sum += int(digit)
    return sum


def getSingleSumNumber(n):
    result = int(getSum(n))
    if result > 9:
        return getSingleSumNumber(result)
    return result


def print_win_matrix(symbol: str):
    result = {}
    with open(f"Data\\Symbol_full\\{symbol}_daily.json", "r") as f:
        data = json.load(f).get('Time Series (Daily)', {})
        for date, value in sorted(data.items()):
            ss = [float(value.get("1. open", 0)),
                  float(value.get("2. high", 0)),
                  float(value.get("3. low", 0)),
                  float(value.get("4. close", 0)),
                  int(value.get("5. volume", 0))]
            m_num = getSingleSumNumber(ss[0])
            if m_num not in result:
                result[m_num] = [0, 0, 0]
            if ss[0] < ss[3]:
                result[m_num][0] += 1
            elif ss[0] > ss[3]:
                result[m_num][1] += 1
            else:
                result[m_num][2] += 1
        print(result)


if __name__ == '__main__':
    # connect_to_db()
    update_daily_ohlcv_from_file()