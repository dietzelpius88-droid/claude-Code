"""Token-Bucket unter Last.

Die Tests laufen gegen eine virtuelle Uhr - sonst dauern sie so lange wie die
Wartezeiten, die sie pruefen. Der Schlafbefehl schiebt die Uhr vor, statt
wirklich zu warten.
"""

from decimal import Decimal

import pytest

from app.adapters.base import TokenBucket


class VirtuelleUhr:
    """Uhr und Schlafbefehl in einem, damit Wartezeiten messbar werden."""

    def __init__(self) -> None:
        self.jetzt = 0.0
        self.geschlafen: list[float] = []

    def __call__(self) -> float:
        return self.jetzt

    async def sleep(self, sekunden: float) -> None:
        self.geschlafen.append(sekunden)
        self.jetzt += sekunden


def _bucket(rate: float, capacity: int) -> tuple[TokenBucket, VirtuelleUhr]:
    uhr = VirtuelleUhr()
    return TokenBucket(rate_per_second=rate, capacity=capacity, clock=uhr, sleeper=uhr.sleep), uhr


async def test_erste_anfragen_laufen_ohne_warten_durch():
    bucket, uhr = _bucket(rate=1.0, capacity=10)
    for _ in range(10):
        await bucket.acquire()
    assert uhr.geschlafen == []
    assert uhr.jetzt == 0.0


async def test_elfte_anfrage_wartet_genau_eine_sekunde():
    # Variational nennt 10 Anfragen je 10 Sekunden - also eine pro Sekunde.
    bucket, uhr = _bucket(rate=1.0, capacity=10)
    for _ in range(10):
        await bucket.acquire()
    await bucket.acquire()
    assert uhr.jetzt == pytest.approx(1.0)


async def test_last_wird_auf_die_konfigurierte_rate_gedrosselt():
    """20 Anfragen bei 10/s und Kapazitaet 10.

    Die ersten zehn laufen sofort, die naechsten zehn brauchen zusammen
    genau eine Sekunde.
    """
    bucket, uhr = _bucket(rate=10.0, capacity=10)
    for _ in range(20):
        await bucket.acquire()
    assert uhr.jetzt == pytest.approx(1.0, abs=1e-6)


async def test_dauerlast_haelt_die_rate_ein():
    bucket, uhr = _bucket(rate=5.0, capacity=5)
    for _ in range(55):
        await bucket.acquire()
    # 55 Anfragen, 5 davon aus dem vollen Eimer -> 50 / 5 pro Sekunde = 10 s.
    assert uhr.jetzt == pytest.approx(10.0, abs=1e-6)


async def test_eimer_fuellt_sich_in_der_pause_wieder_auf():
    bucket, uhr = _bucket(rate=2.0, capacity=4)
    for _ in range(4):
        await bucket.acquire()
    uhr.jetzt += 2.0  # Pause von zwei Sekunden -> vier Token zurueck
    for _ in range(4):
        await bucket.acquire()
    assert uhr.geschlafen == []


async def test_eimer_laeuft_nicht_ueber_die_kapazitaet_hinaus():
    bucket, uhr = _bucket(rate=2.0, capacity=4)
    uhr.jetzt += 3600.0  # eine Stunde Pause
    for _ in range(4):
        await bucket.acquire()
    assert uhr.geschlafen == []
    await bucket.acquire()
    assert uhr.jetzt > 3600.0  # der fuenfte muss warten


async def test_mehrere_token_auf_einmal():
    bucket, uhr = _bucket(rate=1.0, capacity=10)
    await bucket.acquire(tokens=10)
    await bucket.acquire(tokens=2)
    assert uhr.jetzt == pytest.approx(2.0)


async def test_anforderung_groesser_als_kapazitaet_wird_abgelehnt():
    # Sonst wartet der Aufrufer endlos auf Token, die nie kommen.
    bucket, _ = _bucket(rate=1.0, capacity=5)
    with pytest.raises(ValueError):
        await bucket.acquire(tokens=6)


async def test_ungueltige_konfiguration_wird_abgelehnt():
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=0, capacity=5)
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=1, capacity=0)


async def test_lange_last_endet_und_schlaeft_nicht_in_winzigen_schritten():
    """Regressionstest.

    Die erste Umsetzung zaehlte Token mit und sammelte dabei Float-Restbetraege
    ein. Nach wenigen Runden war die errechnete Wartezeit so klein, dass sie
    die Uhr nicht mehr bewegte - die Schleife lief endlos. Hier wird geprueft,
    dass jede gedrosselte Anfrage genau einmal wartet und keine Wartezeit
    unterhalb der Aufloesung der Uhr entsteht.
    """
    bucket, uhr = _bucket(rate=10.0, capacity=10)
    for _ in range(200):
        await bucket.acquire()

    assert len(uhr.geschlafen) == 190  # 10 aus dem vollen Eimer, 190 gedrosselt
    assert all(dauer > 1e-6 for dauer in uhr.geschlafen)
    assert uhr.jetzt == pytest.approx(19.0, abs=1e-6)
