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


# --- Account (Phase 2) ----------------------------------------------------
# Extended: Felder aus x10/models/balance.py und x10/models/position.py,
# camelCase auf der Leitung. Lighter: Schemata DetailedAccount,
# AccountPosition und PositionFunding aus der openapi.json.


def extended_balance() -> dict:
    return {
        "status": "OK",
        "data": {
            "collateralName": "USD",
            "balance": "10000",
            "equity": "10250.5",
            "availableForTrade": "8000",
            "availableForWithdrawal": "7500",
            "unrealisedPnl": "250.5",
            "initialMargin": "2050.1",
            "marginRatio": "0.2",
            "updatedTime": 1789000000000,
        },
    }


def extended_positions() -> dict:
    return {
        "status": "OK",
        "data": [
            {
                "id": 1,
                "accountId": 42,
                "market": "BTC-USD",
                "status": "OPENED",
                "side": "LONG",
                "leverage": "10",
                "size": "0.5",
                "value": "32000",
                "openPrice": "63500",
                "markPrice": "64000",
                "liquidationPrice": "58000",
                "unrealisedPnl": "250",
                "realisedPnl": "0",
                "createdAt": 1789000000000,
                "updatedAt": 1789000000000,
            },
            {
                # Markt, den wir nicht fuehren - muss uebersprungen werden.
                "id": 2,
                "market": "PEPE-USD",
                "status": "OPENED",
                "side": "SHORT",
                "size": "1000",
                "value": "10",
                "openPrice": "0.00001",
                "markPrice": "0.00001",
                "unrealisedPnl": "0",
                "realisedPnl": "0",
            },
            {
                # Groesse null - keine echte Position.
                "id": 3,
                "market": "ETH-USD",
                "status": "CLOSED",
                "side": "LONG",
                "size": "0",
                "value": "0",
                "openPrice": "3100",
                "markPrice": "3100",
                "unrealisedPnl": "0",
                "realisedPnl": "0",
            },
        ],
    }


def extended_fees() -> dict:
    return {
        "status": "OK",
        "data": [
            {
                "market": "BTC-USD",
                "makerFeeRate": "0.0002",
                "takerFeeRate": "0.0005",
                "builderFeeRate": "0",
            }
        ],
    }


def extended_positions_history() -> dict:
    return {
        "status": "OK",
        "data": [
            {
                "id": 7,
                "market": "BTC-USD",
                "side": "LONG",
                "size": "0.5",
                "realisedPnl": "120",
                "realisedPnlBreakdown": {
                    "tradePnl": "100",
                    "fundingFees": "35.25",
                    "openFees": "-8",
                    "closeFees": "-7",
                },
                "createdTime": 1788900000000,
                "closedTime": 1789000000000,
            }
        ],
    }


def lighter_account() -> dict:
    return {
        "code": 200,
        "account_index": 5,
        "l1_address": "0xabc",
        "collateral": "10000",
        "available_balance": "8000",
        "total_asset_value": "10250.5",
        "cross_initial_margin_requirement": "2050.1",
        "cross_maintenance_margin_requirement": "1025",
        "positions": [
            {
                "market_id": 1,
                "symbol": "BTC",
                "sign": -1,
                "position": "0.5",
                "avg_entry_price": "63800",
                "position_value": "32000",
                "unrealized_pnl": "-100",
                "realized_pnl": "0",
                "liquidation_price": "70000",
                "total_funding_paid_out": "12.5",
                "initial_margin_fraction": "0.02",
            },
            {
                "market_id": 42,
                "symbol": "PEPE",
                "sign": 1,
                "position": "1000",
                "avg_entry_price": "0.00001",
                "position_value": "10",
                "unrealized_pnl": "0",
                "realized_pnl": "0",
            },
        ],
    }


def lighter_position_funding() -> dict:
    return {
        "code": 200,
        "position_fundings": [
            {
                "timestamp": 1789000000,
                "market_id": 1,
                "funding_id": 99,
                "change": "1.25",
                "discount": "0",
                "rate": "10.95",
                "position_size": "0.5",
                "position_side": "short",
            },
            {
                "timestamp": 1789003600,
                "market_id": 1,
                "funding_id": 100,
                "change": "-0.75",
                "discount": "0",
                "rate": "-6.57",
                "position_size": "0.5",
                "position_side": "short",
            },
        ],
    }
