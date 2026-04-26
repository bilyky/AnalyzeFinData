import pyetrade

# Your active PROD API key is: ba5857a1b82dafee4f94a8142084dafa, and secret is: 5878c6883ec3d92a78ecca21fa3242cca828e6eb28d084e258dc14fa2b81214c.
# Your active SANDBOX API key is: 7368cfd017a8d45288f91e6dc95efcff, and secret is: 42e7835fffb6f95225f805a9f5b233f362014eba1a8a771db1ffe02338c73c5a.


consumer_key = "7368cfd017a8d45288f91e6dc95efcff"
consumer_secret = "42e7835fffb6f95225f805a9f5b233f362014eba1a8a771db1ffe02338c73c5a"
# # Using the EtradeOAuth object to retrive the URL to request tokens
# oauth = pyetrade.ETradeOAuth(consumer_key, consumer_secret)
# print(oauth.get_request_token())  # Use the printed URL
#
# # Use the printed URL to retrive Verification code
# verifier_code = input("Enter verification code: ")
# tokens = oauth.get_access_token(verifier_code)
# print(tokens)
tokens = {'oauth_token': '5fL4HlxDvIarDIIa3oxp3+Q61esJxRy/mRbCNWFVkm0=', 'oauth_token_secret': 'edqzUZVr9cXgg0CIBUzui3zBTyWYRCx4uGkoCP0jA0w='}
# {'LookupResponse': {'Data': [{'symbol': 'A', 'description': 'AGILENT TECHNOLOGIES INC COM'}, {'symbol': 'AA', 'description': 'ALCOA INC COM'}, {'symbol': 'AU', 'description': 'ANGLOGOLD ASHANTI LTD SPONSORED ADR'}, {'symbol': 'AAPL', 'description': 'APPLE INC COM'}, {'symbol': 'AAUKF', 'description': 'ANGLO AMERICAN PLC SHS'}, {'symbol': 'AAUKY', 'description': 'ANGLO AMERN PLC ADR NEW'}, {'symbol': 'AB', 'description': 'ALLIANCEBERNSTEIN HOLDING LP UNIT LTD PARTN'}, {'symbol': 'ABT', 'description': 'ABBOTT LABS COM'}, {'symbol': 'ABV', 'description': 'COMPANHIA DE BEBIDAS DAS AMERS SPON ADR PFD'}, {'symbol': 'ABX', 'description': 'BARRICK GOLD CORP COM'}]}}
# {'LookupResponse': {'Data': [{'symbol': 'A', 'description': 'AGILENT TECHNOLOGIES INC COM'}, {'symbol': 'AA', 'description': 'ALCOA INC COM'}, {'symbol': 'AU', 'description': 'ANGLOGOLD ASHANTI LTD SPONSORED ADR'}, {'symbol': 'AAPL', 'description': 'APPLE INC COM'}, {'symbol': 'AAUKF', 'description': 'ANGLO AMERICAN PLC SHS'}, {'symbol': 'AAUKY', 'description': 'ANGLO AMERN PLC ADR NEW'}, {'symbol': 'AB', 'description': 'ALLIANCEBERNSTEIN HOLDING LP UNIT LTD PARTN'}, {'symbol': 'ABT', 'description': 'ABBOTT LABS COM'}, {'symbol': 'ABV', 'description': 'COMPANHIA DE BEBIDAS DAS AMERS SPON ADR PFD'}, {'symbol': 'ABX', 'description': 'BARRICK GOLD CORP COM'}]}}
# {'QuoteResponse': {'QuoteData': [{'dateTime': '16:00:00 EDT 06-20-2012', 'dateTimeUTC': 1340222400, 'quoteStatus': 'REALTIME', 'ahFlag': 'false', 'All': {'adjustedFlag': False, 'ask': 579.73, 'askSize': 100, 'askTime': '16:00:00 EDT 06-20-2012', 'bid': 574.04, 'bidExchange': '', 'bidSize': 100, 'bidTime': '16:00:00 EDT 06-20-2012', 'changeClose': 0.0, 'changeClosePercentage': 0.0, 'companyName': 'GOOGLE INC CL A', 'daysToExpiration': 0, 'dirLast': '1', 'dividend': 0.0, 'eps': 32.99727, 'estEarnings': 43.448, 'exDividendDate': 1344947183, 'high': 0.0, 'high52': 670.25, 'lastTrade': 577.51, 'low': 0.0, 'low52': 473.02, 'open': 0.0, 'openInterest': 0, 'optionStyle': '', 'previousClose': 577.51, 'previousDayVolume': 2433786, 'primaryExchange': 'NASDAQ NM', 'symbolDescription': 'GOOGLE INC CL A', 'totalVolume': 0, 'upc': 0, 'cashDeliverable': 0, 'marketCap': 188282697750.0, 'sharesOutstanding': 326025, 'nextEarningDate': '', 'beta': 0.93, 'yield': 0.0, 'declaredDividend': 0.0, 'dividendPayableDate': 0, 'pe': 17.5017, 'week52LowDate': 1308908670, 'week52HiDate': 1325673870, 'intrinsicValue': 0.0, 'timePremium': 0.0, 'optionMultiplier': 0.0, 'contractSize': 0.0, 'expirationDate': 0, 'timeOfLastTrade': 1341334800, 'averageVolume': 13896435}, 'Product': {'symbol': 'GOOG', 'securityType': 'EQ'}}]}}
# {'OptionChainResponse': {'OptionPair': [{'Call': {'optionCategory': 'STANDARD', 'optionRootSymbol': 'AAPL', 'timeStamp': 1363975980, 'adjustedFlag': False, 'displaySymbol': "AAPL Mar 22 '13 $485 Call", 'optionType': 'CALL', 'strikePrice': 485.0, 'symbol': 'AAPL', 'bid': 0.02, 'ask': 0.01, 'bidSize': 0, 'askSize': 25, 'inTheMoney': 'n', 'volume': 178, 'openInterest': 2782, 'netChange': -0.01, 'lastPrice': 0.01, 'quoteDetail': 'https://api.sit.etrade.com/v1/market/quote/AAPL:2013:3:22:CALL:485.000000', 'osiKey': 'AAPL--130322C00485000', 'OptionGreeks': {'rho': 0.0095, 'vega': 0.0751, 'theta': -0.018, 'delta': 0.0848, 'gamma': 0.0316, 'iv': 0.1407, 'currentValue': False}}, 'Put': {'optionCategory': 'STANDARD', 'optionRootSymbol': 'AAPL', 'timeStamp': 1363974660, 'adjustedFlag': False, 'displaySymbol': "AAPL Mar 22 '13 $485 Put", 'optionType': 'PUT', 'strikePrice': 485.0, 'symbol': 'AAPL', 'bid': 23.6, 'ask': 23.9, 'bidSize': 4, 'askSize': 2, 'inTheMoney': 'y', 'volume': 81, 'openInterest': 273, 'netChange': -8.95, 'lastPrice': 23.7, 'quoteDetail': 'https://api.sit.etrade.com/v1/market/quote/AAPL:2013:3:22:PUT:485.000000', 'osiKey': 'AAPL--130322P00485000', 'OptionGreeks': {'rho': 0.0095, 'vega': 0.0751, 'theta': -0.018, 'delta': 0.0848, 'gamma': 0.0316, 'iv': 0.1407, 'currentValue': False}}}], 'SelectedED': {'month': 3, 'year': 2013, 'day': 22}}}
# {'OptionChainResponse': {'OptionPair': [{'Call': {'optionCategory': 'STANDARD', 'optionRootSymbol': 'AAPL', 'timeStamp': 1363975980, 'adjustedFlag': False, 'displaySymbol': "AAPL Mar 22 '13 $485 Call", 'optionType': 'CALL', 'strikePrice': 485.0, 'symbol': 'AAPL', 'bid': 0.02, 'ask': 0.01, 'bidSize': 0, 'askSize': 25, 'inTheMoney': 'n', 'volume': 178, 'openInterest': 2782, 'netChange': -0.01, 'lastPrice': 0.01, 'quoteDetail': 'https://api.sit.etrade.com/v1/market/quote/AAPL:2013:3:22:CALL:485.000000', 'osiKey': 'AAPL--130322C00485000', 'OptionGreeks': {'rho': 0.0095, 'vega': 0.0751, 'theta': -0.018, 'delta': 0.0848, 'gamma': 0.0316, 'iv': 0.1407, 'currentValue': False}}, 'Put': {'optionCategory': 'STANDARD', 'optionRootSymbol': 'AAPL', 'timeStamp': 1363974660, 'adjustedFlag': False, 'displaySymbol': "AAPL Mar 22 '13 $485 Put", 'optionType': 'PUT', 'strikePrice': 485.0, 'symbol': 'AAPL', 'bid': 23.6, 'ask': 23.9, 'bidSize': 4, 'askSize': 2, 'inTheMoney': 'y', 'volume': 81, 'openInterest': 273, 'netChange': -8.95, 'lastPrice': 23.7, 'quoteDetail': 'https://api.sit.etrade.com/v1/market/quote/AAPL:2013:3:22:PUT:485.000000', 'osiKey': 'AAPL--130322P00485000', 'OptionGreeks': {'rho': 0.0095, 'vega': 0.0751, 'theta': -0.018, 'delta': 0.0848, 'gamma': 0.0316, 'iv': 0.1407, 'currentValue': False}}}], 'SelectedED': {'month': 3, 'year': 2013, 'day': 22}}}




class ETrade:
    def __init__(self):
        print('AA')




def get_tokens():
    oauth = pyetrade.ETradeOAuth(consumer_key, consumer_secret)
    print(oauth.get_request_token())  # Use the printed URL
    verifier_code = input("Enter verification code: ")
    tokens = oauth.get_access_token(verifier_code)
    print(tokens)
    return tokens


def get_quote():
    # Setting up the object used for alerts activity
    # Arg dev determines the environment Sandbox (dev=True)
    # or Live/Production (dev=False)
    # tokens = get_tokens()
    market = pyetrade.ETradeMarket(
        consumer_key,
        consumer_secret,
        tokens['oauth_token'],
        tokens['oauth_token_secret'],
        dev=True
    )

    # Getting products symbol with search string
    print(market.look_up_product('alphabet', resp_format='json'))
    print(market.look_up_product('American', resp_format='json'))

    # Getting market quote
    print(market.get_quote(['GOOG'], resp_format='json', detail_flag='week_52'))

    # Getting Options chain with expiry_date=None
    print(market.get_option_chains('GOOG', expiry_date=None, resp_format='json'))

    # Getting Options chain with expiry_date specified with datetime
    import datetime as dt
    datt = dt.datetime(year=2020, month=10, day=16)

    print(market.get_option_chains('GOOG', expiry_date=datt, resp_format='json'))