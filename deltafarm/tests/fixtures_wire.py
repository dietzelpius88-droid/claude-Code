"""Nachgebildete Boersenantworten.

Feldnamen und Verpackung stammen aus den offiziellen SDKs, nicht aus
Vermutungen:
  Extended: x10/models/market.py, x10/models/orderbook.py, x10/models/http.py,
            camelCase auf der Leitung (x10/models/base.py, to_camel)
  Lighter:  openapi.json, Schemata PerpsOrderBookDetail, FundingRate,
            OrderBookOrders, Fundings
"""


def extended_markets(funding_rate: str = "0.0000125") -> dict:
    return {
        "status": "OK",
        "data": [
            {
                "name": "BTC-USD",
                "type": "PERPETUAL",
                "assetName": "BTC",
                "active": True,
                "marketStats": {
                    "markPrice": "64000.5",
                    "indexPrice": "64001.0",
                    "fundingRate": funding_rate,
                    "nextFundingRate": 1789000000000,
                    "openInterest": "1234.5",
                    "dailyVolume": "98765432.1",
                },
                "tradingConfig": {
                    "minOrderSize": "0.0001",
                    "minOrderSizeChange": "0.0001",
                    "minPriceChange": "0.1",
                    "maxLeverage": "50",
                    "riskFactorConfig": [
                        {"upperBound": "100000", "riskFactor": "0.02"},
                        {"upperBound": "1000000", "riskFactor": "0.05"},
                    ],
                },
            },
            {
                # Listing, das wir nicht fuehren - muss uebersprungen werden.
                "name": "PEPE-USD",
                "type": "PERPETUAL",
                "assetName": "PEPE",
                "active": True,
                "marketStats": {"markPrice": "0.00001", "fundingRate": "0.00002"},
                "tradingConfig": {
                    "minOrderSize": "1",
                    "minOrderSizeChange": "1",
                    "minPriceChange": "0.00000001",
                    "maxLeverage": "10",
                },
            },
        ],
    }


def extended_orderbook() -> dict:
    return {
        "status": "OK",
        "data": {
            "market": "BTC-USD",
            "bid": [{"price": "63999.0", "qty": "1.5"}, {"price": "63998.0", "qty": "2.0"}],
            "ask": [{"price": "64001.0", "qty": "1.2"}, {"price": "64002.0", "qty": "3.0"}],
        },
    }


def extended_orderbook_kurzform() -> dict:
    """Dieselben Daten in der Kurzschreibweise, die das SDK ebenfalls akzeptiert."""
    return {
        "status": "OK",
        "data": {
            "m": "BTC-USD",
            "b": [{"p": "63999.0", "q": "1.5"}],
            "a": [{"p": "64001.0", "q": "1.2"}],
        },
    }


def extended_funding_history(stamps_ms: list[int]) -> dict:
    return {
        "status": "OK",
        "data": [{"m": "BTC-USD", "f": "0.0000125", "T": t} for t in stamps_ms],
    }


def lighter_details(mark_price: str = "64000.5") -> dict:
    return {
        "code": 200,
        "order_book_details": [
            {
                "symbol": "BTC",
                "market_id": 1,
                "market_type": "perps",
                "status": "active",
                "taker_fee": "0.0001",
                "maker_fee": "0.0000",
                "liquidation_fee": "0.005",
                "min_base_amount": "0.0001",
                "min_quote_amount": "10",
                "supported_size_decimals": 4,
                "supported_price_decimals": 1,
                "size_decimals": 4,
                "price_decimals": 1,
                "default_initial_margin_fraction": "0.02",
                "maintenance_margin_fraction": "0.01",
                "mark_price": mark_price,
                "index_price": "64001.0",
                "open_interest": "555.5",
                "daily_quote_token_volume": "12345678.9",
            },
            {
                "symbol": "PEPE",
                "market_id": 42,
                "market_type": "perps",
                "status": "active",
                "taker_fee": "0.0002",
                "maker_fee": "0.0000",
                "min_quote_amount": "10",
                "supported_size_decimals": 0,
                "supported_price_decimals": 8,
                "default_initial_margin_fraction": "0.1",
                "mark_price": "0.00001",
            },
        ],
    }


def lighter_funding_rates(rate: str = "10.95", exchange: str = "lighter") -> dict:
    """rate ist Prozent pro Jahr - 10,95 % p. a. entsprechen 0,00125 % pro Stunde."""
    return {
        "code": 200,
        "funding_rates": [
            {"market_id": 1, "exchange": exchange, "symbol": "BTC", "rate": rate},
            {"market_id": 1, "exchange": "binance", "symbol": "BTC", "rate": "78.84"},
        ],
    }


def lighter_orderbook() -> dict:
    return {
        "code": 200,
        "total_bids": 2,
        "bids": [
            {"price": "63999.0", "remaining_base_amount": "1.5", "order_index": 1},
            {"price": "63998.0", "remaining_base_amount": "2.0", "order_index": 2},
        ],
        "total_asks": 2,
        "asks": [
            {"price": "64001.0", "remaining_base_amount": "1.2", "order_index": 3},
            {"price": "64002.0", "remaining_base_amount": "3.0", "order_index": 4},
        ],
    }


def lighter_fundings(stamps_s: list[int]) -> dict:
    return {
        "code": 200,
        "resolution": "1h",
        "fundings": [
            {"timestamp": t, "value": "0.0001", "rate": "0.0000125", "direction": "long"}
            for t in stamps_s
        ],
    }
