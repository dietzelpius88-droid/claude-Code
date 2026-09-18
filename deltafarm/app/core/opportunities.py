"""Paarbildung: aus normalisierten Funding-Raten die besten Paare ableiten."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from itertools import combinations
from typing import Iterable, Mapping, Optional

from app.core.breakeven import breakeven_hours, round_trip_cost
from app.core.funding import net_hourly_rate, to_apr
from app.models.domain import FundingInfo, Opportunity


def build_opportunities(
    funding: Iterable[FundingInfo],
    *,
    taker_fees: Optional[Mapping[str, Decimal]] = None,
    spreads: Optional[Mapping[str, Decimal]] = None,
    depths_usd: Optional[Mapping[tuple[str, str], Decimal]] = None,
) -> list[Opportunity]:
    """Bildet je Symbol alle Boersenpaare und sortiert nach Netto-APR.

    Die Richtung wird nicht geraten: geshortet wird die Boerse mit der hoeheren
    Rate, gelongt die mit der niedrigeren. Damit ist die Netto-Rate nie negativ.

    taker_fees und spreads sind optional. Fehlen sie, bleibt breakeven_hours
    bewusst leer statt geschaetzt - die UI zeigt das ueber cost_basis_known an.
    """
    taker_fees = taker_fees or {}
    spreads = spreads or {}
    depths_usd = depths_usd or {}

    nach_symbol: dict[str, list[FundingInfo]] = defaultdict(list)
    for info in funding:
        nach_symbol[info.symbol].append(info)

    ergebnis: list[Opportunity] = []
    for symbol, infos in nach_symbol.items():
        for a, b in combinations(sorted(infos, key=lambda i: i.venue), 2):
            # Short gehoert auf die hoehere Rate, Long auf die niedrigere.
            short, long_ = (a, b) if a.rate_hourly >= b.rate_hourly else (b, a)

            net = net_hourly_rate(
                long_rate_hourly=long_.rate_hourly,
                short_rate_hourly=short.rate_hourly,
            )

            fee_long = taker_fees.get(long_.venue)
            fee_short = taker_fees.get(short.venue)
            kosten_bekannt = fee_long is not None and fee_short is not None

            hours = None
            if kosten_bekannt:
                hours = breakeven_hours(
                    cost=round_trip_cost(
                        taker_fee_a=fee_long,
                        taker_fee_b=fee_short,
                        spread=spreads.get(symbol, Decimal(0)),
                    ),
                    net_rate_hourly=net,
                )

            tiefen = [
                d
                for d in (
                    depths_usd.get((long_.venue, symbol)),
                    depths_usd.get((short.venue, symbol)),
                )
                if d is not None
            ]

            ergebnis.append(
                Opportunity(
                    symbol=symbol,
                    long_venue=long_.venue,
                    short_venue=short.venue,
                    long_rate_hourly=long_.rate_hourly,
                    short_rate_hourly=short.rate_hourly,
                    net_rate_hourly=net,
                    net_apr=to_apr(net),
                    breakeven_hours=hours,
                    cost_basis_known=kosten_bekannt,
                    min_depth_usd=min(tiefen) if tiefen else None,
                    as_of=min(i.as_of for i in (a, b)),
                )
            )

    # Absteigend nach Netto-APR; bei Gleichstand alphabetisch, damit die
    # Reihenfolge zwischen zwei Aufrufen stabil bleibt.
    ergebnis.sort(key=lambda o: (-o.net_apr, o.symbol, o.long_venue))
    return ergebnis
