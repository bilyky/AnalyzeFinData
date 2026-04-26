import primal_funcs as pf


def tasuki_signal(data, open_column, close_column, buy_column, sell_column):
    """A bullish Tasuki pattern is composed of three candlesticks where the first one is a bullish candlestick, the
    second one is another bullish candlestick that gaps over the first candlestick, and the third candlestick is bearish
    but does not close below the close of the first candlestick.
    """
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] < data[i, open_column] and \
                    data[i, close_column] < data[i - 1, open_column] and \
                    data[i, close_column] > data[i - 2, close_column] and \
                    data[i - 1, close_column] > data[i - 1, open_column] and \
                    data[i - 1, open_column] > data[i - 2, close_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column]:
                print(f"----- {data[i - 2]}")
                print(f"----- {data[i - 1]}")
                print(f"----- {data[i]}")
                data[i + 1, buy_column] = 1
            # Bearish pattern
            elif data[i, close_column] > data[i, open_column] and \
                    data[i, close_column] > data[i - 1, open_column] and \
                    data[i, close_column] < data[i - 2, close_column] and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i - 1, open_column] < data[i - 2, close_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def three_methods_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column):
    """The Three Methods pattern is a complex configuration mainly composed of five candlesticks.
    The rising Three Methods pattern should occur in a bullish trend with the first candlestick being a big-bodied
    bullish one followed by three small-bodied bearish candlesticks typically contained within the range of the
    first candlestick.To confirm the pattern, one last big bullish candlestick must be printed with a close higher
    than the first candlestick's high.This is just like a bullish breakout of a small consolidation.
    """
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and\
              data[i, close_column] > data[i - 4, high_column] and\
              data[i, low_column] < data[i - 1, low_column] and\
              data[i - 1, close_column] < data[i - 4, close_column] and\
              data[i - 1, low_column] > data[i - 4, low_column] and\
              data[i - 2, close_column] < data[i - 4, close_column] and\
              data[i - 2, low_column] > data[i - 4, low_column] and\
              data[i - 3, close_column] < data[i - 4, close_column] and\
              data[i - 3, low_column] > data[i - 4, low_column] and\
              data[i - 4, close_column] > data[i - 4, open_column]:
                  
                    data[i + 1, buy_column] = 1
                    
            # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and\
                data[i, close_column] < data[i - 4, low_column] and\
                data[i, high_column] > data[i - 1, high_column] and\
                data[i - 1, close_column] > data[i - 4, close_column] and\
                data[i - 1, high_column] < data[i - 4, high_column] and\
                data[i - 2, close_column] > data[i - 4, close_column] and\
                data[i - 2, high_column] < data[i - 4, high_column] and\
                data[i - 3, close_column] > data[i - 4, close_column] and\
                data[i - 3, high_column] < data[i - 4, high_column] and\
                data[i - 4, close_column] < data[i - 4, open_column]:
                  
                    data[i + 1, sell_column] = -1
        except IndexError:
            pass
        
    return data


def hikkake_signal(data, open_column, high_column, low_column, close_column, buy_signal, sell_signal):
    data = pf.add_column(data, 5)
    for i in range(len(data)):   
        try:
            # Bullish pattern
            if data[i, close_column] > data[i - 3, high_column] and \
                    data[i, close_column] > data[i - 4, close_column] and \
                    data[i - 1, low_column] < data[i, open_column] and \
                    data[i - 1, close_column] < data[i, close_column] and \
                    data[i - 1, high_column] <= data[i - 3, high_column] and \
                    data[i - 2, low_column] < data[i, open_column] and \
                    data[i - 2, close_column] < data[i, close_column] and \
                    data[i - 2, high_column] <= data[i - 3, high_column] and \
                    data[i - 3, high_column] < data[i - 4, high_column] and \
                    data[i - 3, low_column] > data[i - 4, low_column] and \
                    data[i - 4, close_column] > data[i - 4, open_column]:
                data[i + 1, buy_signal] = 1
            
            # Bearish pattern
            elif data[i, close_column] < data[i - 3, low_column] and \
                    data[i, close_column] < data[i - 4, close_column] and \
                    data[i - 1, high_column] > data[i, open_column] and \
                    data[i - 1, close_column] > data[i, close_column] and \
                    data[i - 1, low_column] >= data[i - 3, low_column] and \
                    data[i - 2, high_column] > data[i, open_column] and \
                    data[i - 2, close_column] > data[i, close_column] and \
                    data[i - 2, low_column] >= data[i - 3, low_column] and \
                    data[i - 3, low_column] > data[i - 4, low_column] and \
                    data[i - 3, high_column] < data[i - 4, high_column] and \
                    data[i - 4, close_column] < data[i - 4, open_column]:
                data[i + 1, sell_signal] = -1
        
        except IndexError:
            pass
    return data


def quintuplets_signal(data, open_column, close_column, buy_column, sell_column, body):
    data = pf.add_column(data, 5)

    for i in range(len(data)):

        try:

            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, close_column] > data[i - 1, close_column] and \
                    data[i, close_column] - data[i, open_column] < body and \
                    data[i - 1, close_column] > data[i - 1, open_column] and \
                    data[i - 1, close_column] > data[i - 2, close_column] and \
                    data[i - 1, close_column] - data[i - 1, open_column] < body and \
                    data[i - 2, close_column] > data[i - 2, open_column] and \
                    data[i - 2, close_column] > data[i - 3, close_column] and \
                    data[i - 2, close_column] - data[i - 2, open_column] < body and \
                    data[i - 3, close_column] > data[i - 3, open_column] and \
                    data[i - 3, close_column] > data[i - 4, close_column] and \
                    data[i - 3, close_column] - data[i - 3, open_column] < body and \
                    data[i - 4, close_column] > data[i - 4, open_column] and \
                    data[i - 4, close_column] - data[i - 4, open_column] < body and \
                    data[i, buy_column] == 0:

                data[i + 1, 4] = 1

                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    data[i, close_column] < data[i - 1, close_column] and \
                    data[i, open_column] - data[i, close_column] < body and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i - 1, close_column] < data[i - 2, close_column] and \
                    data[i - 1, open_column] - data[i - 1, close_column] < body and \
                    data[i - 2, close_column] < data[i - 2, open_column] and \
                    data[i - 2, close_column] < data[i - 3, close_column] and \
                    data[i - 2, open_column] - data[i - 2, close_column] < body and \
                    data[i - 3, close_column] < data[i - 3, open_column] and \
                    data[i - 3, close_column] < data[i - 4, close_column] and \
                    data[i - 3, open_column] - data[i - 3, close_column] < body and \
                    data[i - 4, close_column] < data[i - 4, open_column] and \
                    data[i - 4, open_column] - data[i - 4, close_column] < body and \
                    data[i, sell_column] == 0:

                data[i + 1, 5] = -1

        except IndexError:

            pass

    return data


def double_trouble_signal(data, open_column, high_column, low_column, close_column, atr_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, close_column] > data[i - 1, close_column] and \
                    data[i - 1, close_column] > data[i - 1, open_column] and \
                    data[i, high_column] - data[i, low_column] > (2 * data[i - 1, atr_column]) and \
                    data[i, close_column] - data[i, open_column] > data[i - 1, close_column] - data[i - 1, open_column] and \
                    data[i, buy_column] == 0:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    data[i, close_column] < data[i - 1, close_column] and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i, high_column] - data[i, low_column] > (2 * data[i - 1, atr_column]) and \
                    data[i, open_column] - data[i, close_column] > data[i - 1, open_column] - data[i - 1, close_column] and \
                    data[i, sell_column] == 0:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def bottle_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, open_column] == data[i, low_column] and \
                    data[i - 1, close_column] > data[i - 1, open_column] and \
                    data[i, open_column] < data[i - 1, close_column] and \
                    data[i, buy_column] == 0:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    data[i, open_column] == data[i, high_column] and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i, open_column] > data[i - 1, close_column] and \
                    data[i, sell_column] == 0:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def slingshot_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i - 1, high_column] and \
                    data[i, close_column] > data[i - 2, high_column] and \
                    data[i, low_column] <= data[i - 3, high_column] and \
                    data[i, close_column] > data[i, open_column] and \
                    data[i - 1, close_column] >= data[i - 3, high_column] and \
                    data[i - 2, low_column] >= data[i - 3, low_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column] and \
                    data[i - 2, close_column] > data[i - 3, high_column] and \
                    data[i - 1, high_column] <= data[i - 2, high_column]:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] < data[i - 1, low_column] and \
                    data[i, close_column] < data[i - 2, low_column] and \
                    data[i, high_column] >= data[i - 3, low_column] and \
                    data[i, close_column] < data[i, open_column] and \
                    data[i - 1, high_column] <= data[i - 3, high_column] and \
                    data[i - 2, close_column] <= data[i - 3, low_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column] and \
                    data[i - 2, close_column] < data[i - 3, low_column] and \
                    data[i - 1, low_column] >= data[i - 2, low_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def h_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, close_column] > data[i - 1, close_column] and \
                    data[i, low_column] > data[i - 1, low_column] and \
                    data[i - 1, close_column] == data[i - 1, open_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column] and \
                    data[i - 2, high_column] < data[i - 1, high_column]:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    data[i, close_column] < data[i - 1, close_column] and \
                    data[i, low_column] < data[i - 1, low_column] and \
                    data[i - 1, close_column] == data[i - 1, open_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column] and \
                    data[i - 2, low_column] > data[i - 1, low_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def doji_signal(data, open_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, close_column] > data[i - 1, close_column] and \
                    data[i - 1, close_column] == data[i - 1, open_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column]:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    data[i, close_column] < data[i - 1, close_column] and \
                    data[i - 1, close_column] == data[i - 1, open_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def harami_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] < data[i - 1, open_column] and \
                    data[i, open_column] > data[i - 1, close_column] and \
                    data[i, high_column] < data[i - 1, high_column] and \
                    data[i, low_column] > data[i - 1, low_column] and \
                    data[i, close_column] > data[i, open_column] and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column]:
                data[i + 1, buy_column] = 1

                # Bearish pattern
            elif data[i, close_column] > data[i - 1, open_column] and \
                    data[i, open_column] < data[i - 1, close_column] and \
                    data[i, high_column] < data[i - 1, high_column] and \
                    data[i, low_column] > data[i - 1, low_column] and \
                    data[i, close_column] < data[i, open_column] and \
                    data[i - 1, close_column] > data[i - 1, open_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def harami_strict_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, high_column] < data[i - 1, open_column] and \
                    data[i, low_column] > data[i - 1, close_column] and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column]:

                data[i + 1, buy_column] = 1

                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    data[i, high_column] < data[i - 1, close_column] and \
                    data[i, low_column] > data[i - 1, open_column] and \
                    data[i - 1, close_column] > data[i - 1, open_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column]:

                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def neck_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)

    for i in range(len(data)):

        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, close_column] == data[i - 1, close_column] and \
                    data[i, open_column] < data[i - 1, close_column] and \
                    data[i - 1, close_column] < data[i - 1, open_column]:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    data[i, close_column] == data[i - 1, close_column] and \
                    data[i, open_column] > data[i - 1, close_column] and \
                    data[i - 1, close_column] > data[i - 1, open_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def tweezers_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column, body):
    data = pf.add_column(data, 5)

    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, low_column] == data[i - 1, low_column] and \
                    data[i, close_column] - data[i, open_column] < body and \
                    data[i - 1, close_column] - data[i - 1, open_column] < body and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column]:

                data[i + 1, buy_column] = 1

                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                 data[i, high_column] == data[i - 1, high_column] and \
                 data[i, close_column] - data[i, open_column] < body and \
                 data[i - 1, close_column] - data[i - 1, open_column] < body and\
                 data[i - 1, close_column] > data[i - 1, open_column] and \
                 data[i - 2, close_column] > data[i - 2, open_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def stick_sandwich_signal(data, open_column, high_column, low_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] < data[i, open_column] and \
                    data[i, high_column] > data[i - 1, high_column] and \
                    data[i, low_column] < data[i - 1, low_column] and \
                    data[i - 1, close_column] > data[i - 1, open_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column] and \
                    data[i - 2, high_column] > data[i - 1, high_column] and \
                    data[i - 2, low_column] < data[i - 1, low_column] and \
                    data[i - 2, close_column] < data[i - 3, close_column] and \
                    data[i - 3, close_column] < data[i - 3, open_column]:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] > data[i, open_column] and \
                    data[i, high_column] > data[i - 1, high_column] and \
                    data[i, low_column] < data[i - 1, low_column] and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column] and \
                    data[i - 2, high_column] > data[i - 1, high_column] and \
                    data[i - 2, low_column] < data[i - 1, low_column] and \
                    data[i - 2, close_column] > data[i - 3, close_column] and \
                    data[i - 3, close_column] > data[i - 3, open_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def star_signal(data, open_column, high_column, low_column, close_column,
           buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    max(data[i - 1, close_column], data[i - 1, open_column]) \
                    < data[i, open_column] and max(data[i - 1, close_column], \
                                                   data[i - 1, open_column]) < data[i - 2, close_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column]:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    min(data[i - 1, close_column], data[i - 1, open_column]) \
                    > data[i, open_column] and min(data[i - 1, close_column], \
                                                   data[i - 1, open_column]) > data[i - 2, close_column] \
                    and data[i - 2, close_column] > data[i - 2, open_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def piersing_signal(data, open_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, close_column] < data[i - 1, open_column] and \
                    data[i, close_column] > data[i - 1, close_column] and \
                    data[i, open_column] < data[i - 1, close_column] and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column]:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    data[i, close_column] > data[i - 1, open_column] and \
                    data[i, close_column] < data[i - 1, close_column] and \
                    data[i, open_column] > data[i - 1, close_column] and \
                    data[i - 1, close_column] > data[i - 1, open_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data


def engulfing_signal(data, open_column, close_column, buy_column, sell_column):
    data = pf.add_column(data, 5)
    for i in range(len(data)):
        try:
            # Bullish pattern
            if data[i, close_column] > data[i, open_column] and \
                    data[i, open_column] < data[i - 1, close_column] and \
                    data[i, close_column] > data[i - 1, open_column] and \
                    data[i - 1, close_column] < data[i - 1, open_column] and \
                    data[i - 2, close_column] < data[i - 2, open_column]:
                data[i + 1, buy_column] = 1
                # Bearish pattern
            elif data[i, close_column] < data[i, open_column] and \
                    data[i, open_column] > data[i - 1, close_column] and \
                    data[i, close_column] < data[i - 1, open_column] and \
                    data[i - 1, close_column] > data[i - 1, open_column] and \
                    data[i - 2, close_column] > data[i - 2, open_column]:
                data[i + 1, sell_column] = -1
        except IndexError:
            pass
    return data