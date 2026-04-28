import datetime

import MetaTrader5
import pytz
import pandas as pd
import MetaTrader5 as mt5

import etrade
import primal_funcs as pf
import powergauge
import numpy as np
import pyetrade
import rapidapi
import signals
import database

frame_M15 = mt5.TIMEFRAME_M15
frame_M30 = mt5.TIMEFRAME_M30
frame_H1 = mt5.TIMEFRAME_H1
frame_H4 = mt5.TIMEFRAME_H4
frame_D1 = mt5.TIMEFRAME_D1
frame_W1 = mt5.TIMEFRAME_W1
frame_M1 = mt5.TIMEFRAME_MN1

assets = ['EURUSD', 'USDCHF', 'GBPUSD', 'USDCAD', 'SP500m']


def get_quotes(time_frame, year=2022, month=1, day=1, asset='SP500m'):
    if not mt5.initialize():
        print(f'Filed to initialize: {mt5.last_error()}')
        return
    timezone = pytz.timezone("America/Los_Angeles")
    time_from = datetime.datetime(year, month, day, tzinfo=timezone)
    time_to = datetime.datetime.now(timezone) + datetime.timedelta(days=1)
    rates = mt5.copy_rates_range(asset, time_frame, time_from, time_to)
    rates_frame = pd.DataFrame(rates)
    return rates_frame


def mass_import(asset, time_frame):
    if time_frame == 'H1':
        data = get_quotes(frame_H1, 2022, 1, 1, asset=assets[asset])
        data = data.iloc[:, 1:5].values
        data = data.round(decimals=5)

    if time_frame == 'D1':
        data = get_quotes(frame_D1, 2022, 1, 1, asset=assets[asset])
        data = data.iloc[:, 1:5].values
        data = data.round(decimals=5)

    return data


def start_one():
    sourse = 'md5'

    # data = mass_import(4, 'D1')
    # data = rapidapi.get_quotes('D1', 2022, 1, 1, symbol='MSFT')
    # data = rapidapi.get_quotes('D1', 2022, 1, 1, symbol='PYPL')
    data = rapidapi.get_quotes('D1', 2022, 1, 1, symbol='ADBE')
    print(f'Quotes = {data}')
    # pf.ohlc_plot_bars(data, 500)
    # my_data = signals.marubozu_signal(data, 0, 1, 2, 3, 4, 5)
    # my_data = signals.three_candles_signal(data, 0, 3, 4, 5, 5)
    # my_data = signals.tasuki_signal(data, 0, 3, 4, 5)
    # my_data = signals.three_methods_signal(data, 0, 1, 2, 3, 4, 5)
    # my_data = signals.hikkake_signal(data, 0, 1, 2, 3, 4, 5)
    # my_data = signals.quintuplets_signal(data, 0, 3, 4, 5, 5)
    # my_data = signals.double_trouble_signal(data, 0, 1, 2, 3, 6, 4, 5)
    # my_data = signals.bottle_signal(data, 0, 1, 2, 3, 4, 5)
    # my_data = signals.doji_signal(data, 0, 3, 4, 5)
    # my_data = signals.harami_signal(data, 0, 1, 2, 3, 4, 5)
    my_data = signals.neck_signal(data, 0, 1, 2, 3, 4, 5)
    pf.signal_chart(my_data, 0, 4, 5, window=500)
    my_data = pf.performance(my_data, 0, 4, 5, 6, 7, 8)
    for dd in my_data:
        if dd[4]+dd[5] != 0:
            print(dd)


if __name__ == '__main__':
    print(f'START')
    # database.connect_to_db()
    # database.update_daily_ohlcv_from_file()
    # etrade.get_quote()
    form_cache = True
    dd = datetime.datetime.now()
    # dd = datetime.datetime(2024, 6, 17)
    powergauge.check_from_file(form_cache, dd)
    powergauge.check_from_xls(form_cache, dd)

    print(f'CHARTING support - resistance')
    print(f'INDICATOR MA RCI')
    print(f'PATTERNS ...')
    # start_one()
    # rapidapi.save_quotas()
    # pd.read_excel('')



