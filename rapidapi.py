import requests
import json
import numpy as np
import time
# https://rapidapi.com/alphavantage/api/alpha-vantage/
# default-application_6870957
# rapidapi.com
# gmail.com


def get_data(symbol: str):
    url = "https://alpha-vantage.p.rapidapi.com/query"

    querystring = {"function": "TIME_SERIES_DAILY",
                   "symbol": symbol,
                   "outputsize": "full",
                   "datatype": "json"}

    headers = {
        "X-RapidAPI-Key": "972a5cbb65mshca89b90d95cb79ap1076d7jsn5470d3d00386",
        "X-RapidAPI-Host": "alpha-vantage.p.rapidapi.com"
    }

    response = requests.request("GET", url, headers=headers, params=querystring)

    # print(response.text)
    return response.text


def save_quotas():
    with open("Data\\symbols_to_check.txt", "r") as f:
        for line in f.readlines():
            try:
                split_line = line.strip().split()
                symbol = split_line[-1]
                print(f"{symbol}")
                resp = get_data(symbol)
                with open(f"Data\\Symbol_full\\{symbol}_daily.json", "w") as fw:
                    resp_js = json.loads(resp)
                    json.dump(resp_js, fw)
            except Exception as ex:
                print(f"{ex}")
            time.sleep(14)


def get_quotes(time_frame, year=2022, month=1, day=1, symbol='MSFT'):
    result = []
    if time_frame == 'D1':
        with open(f"Data\\Symbol_full\\{symbol}_daily.json", "r") as f:
            data = json.load(f).get('Time Series (Daily)', {})
        for date, value in data.items():
            if date == '2021-12-31':
                break
            ss = [float(value.get("1. open", 0)),
                  float(value.get("2. high", 0)),
                  float(value.get("3. low", 0)),
                  float(value.get("4. close", 0))]
            result.insert(0, ss)
    return np.array(result)


if __name__ == '__main__':
    print(f'START')
    save_quotas()
