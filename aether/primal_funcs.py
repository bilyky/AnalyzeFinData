import numpy as np
import matplotlib.pyplot as plt


def add_column(data, times):
    for i in range(1, times + 1):
        new = np.zeros((len(data), 1), dtype=float)
        data = np.append(data, new, axis=1)
    return data


def delete_column(data, index, times):
    for i in range(1, times + 1):
        data = np.delete(data, index, axis=1)
    return data


def add_row(data, times):
    for i in range(1, times + 1):
        columns = np.shape(data)[1]
        new = np.zeros((1, columns), dtype=float)
        data = np.append(data, new, axis=0)
    return data


def delete_row(data, number):
    data = data[number:, ]
    return data


def rounding(data, how_far):
    data = data.round(decimals=how_far)
    return data


def signal_alpha(data):
    data = add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish Alpha
            if data[i, 2] < data[i - 5, 2] and data[i, 2] < data[i - 13, 2]\
                    and data[i, 2] > data[i - 21, 2] and data[i, 3] > data[i - 1, 3] and data[i, 4] == 0:
                data[i + 1, 4] = 1
                # Bearish Alpha
            elif data[i, 1] > data[i - 5, 1] and data[i, 1] > data[i - 13, 1] and data[i, 1] < data[i - 21, 1]\
                    and data[i, 3] < data[i - 1, 3] and data[i, 5] == 0:
                data[i + 1, 5] = -1
        except IndexError:
            pass
    return data


def ohlc_plot_bars(data, window):
    sample = data[-window:, ]
    for i in range(len(sample)):
        plt.vlines(x=i, ymin=sample[i, 2], ymax=sample[i, 1], color='black', linewidth=1)
        if sample[i, 3] > sample[i, 0]:
            plt.vlines(x=i, ymin=sample[i, 0], ymax=sample[i, 3], color='black', linewidth=1)
        if sample[i, 3] < sample[i, 0]:
            plt.vlines(x=i, ymin=sample[i, 3], ymax=sample[i, 0], color='black', linewidth=1)
        if sample[i, 3] == sample[i, 0]:
            plt.vlines(x=i, ymin=sample[i, 3], ymax=sample[i, 0] + 0.00003, color='black', linewidth=1.00)
    plt.grid()


def ohlc_plot_candles(data, window):
    sample = data[-window:, ]
    for i in range(len(sample)):
        plt.vlines(x=i, ymin=sample[i, 2], ymax=sample[i, 1], color='black', linewidth=1)
        if sample[i, 3] > sample[i, 0]:
            plt.vlines(x=i, ymin=sample[i, 0], ymax=sample[i, 3], color='green', linewidth=3)
        if sample[i, 3] < sample[i, 0]:
            plt.vlines(x=i, ymin=sample[i, 3], ymax=sample[i, 0], color='red', linewidth=3)
        if sample[i, 3] == sample[i, 0]:
            plt.vlines(x=i, ymin=sample[i, 3], ymax=sample[i, 0] + 0.00003, color='black', linewidth=3)
    plt.grid()


def signal_chart(data, position, buy_column, sell_column, window=500):
    # ohlc_plot_bars(data, window)
    sample = data[-window:, ]
    fig, ax = plt.subplots(figsize=(10, 5))
    ohlc_plot_candles(data, window)
    for i in range(len(sample)):
        if sample[i, buy_column] == 1:
            x = i
            y = sample[i, position]
            ax.annotate(' ', xy=(x, y),
                        arrowprops=dict(width=9, headlength=11,
                        headwidth=11, facecolor='blue', color='blue'))
        elif sample[i, sell_column] == -1:
            x = i
            y = sample[i, position]
            ax.annotate(' ', xy=(x, y),
                        arrowprops=dict(width=9, headlength=-11,
                        headwidth=-11, facecolor='yellow', color='yellow'))
    plt.show()


def performance(data,
                open_price,
                buy_column,
                sell_column,
                long_result_col,
                short_result_col,
                total_result_col):
    # Variable holding period
    for i in range(len(data)):
        try:
            if data[i, buy_column] == 1:
                for a in range(i + 1, i + 1000):
                    if data[a, buy_column] == 1 or data[a, sell_column] == -1:
                        data[a, long_result_col] = data[a, open_price] - data[i, open_price]
                        break
                    else:
                        continue
            else:
                continue
        except IndexError:
            pass

    for i in range(len(data)):
        try:
            if data[i, sell_column] == -1:
                for a in range(i + 1, i + 1000):
                    if data[a, buy_column] == 1 or data[a, sell_column] == -1:
                        data[a, short_result_col] = data[i, open_price] - data[a, open_price]
                        break
                    else:
                        continue
            else:
                continue
        except IndexError:
            pass

            # Aggregating the long & short results into one column
    data[:, total_result_col] = data[:, long_result_col] + data[:, short_result_col]
    # Profit factor
    total_net_profits = data[data[:, total_result_col] > 0, total_result_col]
    total_net_losses = data[data[:, total_result_col] < 0, total_result_col]
    total_net_losses = abs(total_net_losses)
    profit_factor = round(np.sum(total_net_profits) / np.sum(total_net_losses), 2)
    # Hit ratio
    hit_ratio = 0
    if len(total_net_losses) + len(total_net_profits) != 0:
        hit_ratio = len(total_net_profits) / (len(total_net_losses) + len(total_net_profits))
    hit_ratio = hit_ratio * 100
    # Risk-reward ratio
    average_gain = total_net_profits.mean()
    average_loss = total_net_losses.mean()
    realized_risk_reward = average_gain / average_loss

    # Number of trades
    trades = len(total_net_losses) + len(total_net_profits)

    print('Hit Ratio         = ', hit_ratio)
    print('Profit factor     = ', profit_factor)
    print('Realized RR       = ', round(realized_risk_reward, 3))
    print('Number of trades  = ', trades)
    return data


def moving_average(data, lookback, close, position):
    data = add_column(data, 1)
    for i in range(len(data)):
        try:
            data[i, position] = (data[i - lookback + 1:i+1, close].mean())
        except IndexError:
            pass
    data = delete_row(data, lookback)
    return data


def smoothed_ma(data, alpha, lookback, close, position):
    lookback = (2 * lookback) - 1
    alpha = alpha / (lookback + 1.0)
    beta = 1 - alpha
    data = moving_average(data, lookback, close, position)

    data[lookback + 1, position] = (data[lookback + 1, close] * alpha) + (data[lookback, position] * beta)
    for i in range(lookback + 2, len(data)):
        try:
            data[i, position] = (data[i, close] * alpha) + (data[i - 1, position] * beta)

        except IndexError:
            pass
    return data


def rsi(data, lookback, close, position):
    data = add_column(data, 5)
    for i in range(len(data)):
        data[i, position] = data[i, close] - data[i - 1, close]
    for i in range(len(data)):
        if data[i, position] > 0:
            data[i, position + 1] = data[i, position]
        elif data[i, position] < 0:
            data[i, position + 2] = abs(data[i, position])

    data = smoothed_ma(data, 2, lookback, position + 1, position + 3)
    data = smoothed_ma(data, 2, lookback, position + 2, position + 4)
    data[:, position + 5] = data[:, position + 3] / data[:, position + 4]
    data[:, position + 6] = (100 - (100 / (1 + data[:, position + 5])))
    data = delete_column(data, position, 6)
    data = delete_row(data, lookback)

    return data


def atr(data, lookback, high_column, low_column, close_column, position):
    # Average True range
    data = add_column(data, 1)
    for i in range(len(data)):
        try:
            data[i, position] = max(data[i, high_column] - data[i, low_column],
                                    abs(data[i, high_column] - data[i - 1, close_column]),
                                    abs(data[i, low_column] - data[i - 1, close_column]))
        except ValueError:
            pass
    data[0, position] = 0
    data = smoothed_ma(data, 2, lookback, position, position + 1)
    data = delete_column(data, position, 1)
    data = delete_row(data, lookback)
    return data


def marubozu_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column) -> dict:
    # marubozu indicate indicate a stock has traded strongly in one direction throughout the session and closed
    # at its high or low price of the day. A marubozu candle is represented only by a body; it has no wicks or
    # shadows extending from the top or bottom of the candle. A white marubozu candle has a long white body and
    # is formed when the open equals the low and the close equals the high
    data = add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and data[i, high_column] == data[i, close_column] and \
                data[i, low_column] == data[i, open_column] and data[i, buy_column] == 0:
                data[i + 1, buy_column] = 1
            # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and data[i, high_column] == data[i, open_column] and \
                data[i, low_column] == data[i, close_column] and data[i, sell_column] == 0:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def three_candles_signal(data, open_column, close_column, buy_column, sell_column, body) -> dict:
    """
    The pattern consists of three consecutive long-bodied candlesticks that open within the previous candle's
    real body and a close that exceeds the previous candle's high. These candlesticks should not have very long
    shadows and ideally open within the real body of the preceding candle in the pattern.
    :param data:
    :param open_column:
    :param close_column:
    :param buy_column:
    :param sell_column:
    :param body:
    :return:
    """
    data = add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] - data[i, open_column] > body and \
                data[i - 1, close_column] - data[i - 1, open_column] > \
                body and data[i - 2, close_column] - \
                data[i - 2, open_column] > body and data[i, close_column] > \
                data[i - 1, close_column] and data[i - 1, close_column] > \
                data[i - 2, close_column] and data[i - 2, close_column] > \
                data[i - 3, close_column] and data[i, buy_column] == 0:
                data[i + 1, buy_column] = 1

            # Bearish pattern
            elif data[i, close_column] - data[i, open_column] > body and \
                data[i - 1, close_column] - data[i - 1, open_column] > \
                body and data[i - 2, close_column] - \
                data[i - 2, open_column] > body and data[i, close_column] \
                < data[i - 1, close_column] and data[i - 1, close_column] \
                < data[i - 2, close_column] and data[i - 2, close_column] \
                < data[i - 3, close_column] and data[i, sell_column] == 0:
                data[i + 1, sell_column] = -1
        except IndexError:

            pass
    return data