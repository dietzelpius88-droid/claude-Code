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

# Ab dieser relativen Abweichung der Mark-Preise gelten zwei Notierungen nicht
# mehr als dasselbe Asset. Zwei Boersen weichen bei demselben Basiswert
# normalerweise um deutlich unter einem Prozent ab; zwei verschiedene Assets um
# Groessenordnungen. Zwei Prozent trennt das sicher, ohne bei Volatilitaet
# falschen Alarm zu schlagen.
DEFAULT_MAX_PRICE_DEVIATION = Decimal("0.02")


def price_deviation(a: Optional[Decimal], b: Optional[Decimal]) -> Optional[Decimal]:
    """Relative Abweichung zweier Preise, bezogen auf den kleineren.

    Der kleinere als Bezug, damit der Wert bei voellig verschiedenen Assets
    gross wird statt sich gegen 1 zu saettigen. None, wenn ein Preis fehlt -
    dann wird nichts behauptet.
    """
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return abs(a - b) / min(a, b)


def build_opportunities(
    funding: Iterable[FundingInfo],
    *,
    taker_fees: Optional[Mapping[str, Decimal]] = None,
    spreads: Optional[Mapping[str, Decimal]] = None,
    depths_usd: Optional[Mapping[tuple[str, str], Decimal]] = None,
    mark_prices: Optional[Mapping[tuple[str, str], Decimal]] = None,
    max_price_deviation: Decimal = DEFAULT_MAX_PRICE_DEVIATION,
) -> list[Opportunity]:
    """Bildet je Symbol alle Boersenpaare und sortiert nach Netto-APR.

    Die Richtung wird nicht geraten: geshortet wird die Boerse mit der hoeheren
    Rate, gelongt die mit der niedrigeren. Damit ist die Netto-Rate nie negativ.

    Ein gleicher Ticker allein macht zwei Listings noch nicht zum selben Asset.
    Liegen Mark-Preise vor, muessen sie das bestaetigen; weichen sie zu stark
    ab, wird das Paar als asset_match=False gekennzeichnet und einsortiert,
    statt es stillschweigend als Chance auszuweisen.

    taker_fees und spreads sind optional. Fehlen sie, bleibt breakeven_hours
    bewusst leer statt geschaetzt - die UI zeigt das ueber cost_basis_known an.
    """
    taker_fees = taker_fees or {}
    spreads = spreads or {}
    depths_usd = depths_usd or {}
    mark_prices = mark_prices or {}

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

            abweichung = price_deviation(
                mark_prices.get((long_.venue, symbol)),
                mark_prices.get((short.venue, symbol)),
            )
            passt = None if abweichung is None else abweichung <= max_price_deviation

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
                    price_deviation=abweichung,
                    asset_match=passt,
                    min_depth_usd=min(tiefen) if tiefen else None,
                    as_of=min(i.as_of for i in (a, b)),
                )
            )

    # Zuerst die Paare, deren Preise dasselbe Asset bestaetigen, dann die
    # ungeprueften, zuletzt die widerlegten. Ein hoher APR darf ein Paar nicht
    # nach oben bringen, wenn die beiden Beine gar nicht dasselbe Asset sind.
    def rang(o: Opportunity) -> int:
        return {True: 0, None: 1, False: 2}[o.asset_match]

    ergebnis.sort(key=lambda o: (rang(o), -o.net_apr, o.symbol, o.long_venue))
    return ergebnis
