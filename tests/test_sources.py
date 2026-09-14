"""Quellen: Duplikatunterdrueckung, Live-Transkript, Katalog."""
from pathlib import Path

import pytest

from newsbot.models import SourceTier
from newsbot.sources.base import NewsSource
from newsbot.sources.catalog import build_sources, load_specs, summary
from newsbot.sources.poller import federal_register_extract
from newsbot.sources.replay import ReplaySource
from newsbot.sources.social import SocialAccountSource, WATCHLIST
from newsbot.sources.speech import (SpeechStreamSource, TranscriptChunk,
                                    transcript_from_lines)

ROOT = Path(__file__).resolve().parent.parent


class _Dummy(NewsSource):
    def stream(self):
        raise NotImplementedError


def test_duplikate_innerhalb_der_quelle():
    s = _Dummy("x", SourceTier.WIRE)
    assert s.emit("Meldung A", url="u1") is not None
    assert s.emit("Meldung A", url="u1") is None
    assert s.stats.duplicates == 1
    assert s.emit("Meldung B", url="u2") is not None


def test_latenz_wird_gemessen():
    from newsbot.models import now_ms
    s = _Dummy("x", SourceTier.WIRE)
    n = s.emit("Meldung", url="u", published_ms=now_ms() - 2_000)
    assert 1_500 < n.source_latency_ms < 3_000
    assert s.stats.median_latency_ms > 0


def test_vertrauensstufen_sind_geordnet():
    assert SourceTier.PRIMARY.trust > SourceTier.WIRE.trust > SourceTier.SOCIAL.trust
    assert 0 < SourceTier.SOCIAL.trust < 0.5


# ---------------------------------------------------------------- Rede
async def test_rede_wird_in_saetze_zerlegt():
    lines = [(0, "We are going to make America"), (700, "the crypto capital of the world."),
             (1500, "And I looked at hyper liquid, very impressive.")]
    src = SpeechStreamSource("speech", transcript_from_lines(lines, speaker="POTUS"))
    out = [n async for n in src.stream()]
    assert len(out) == 2
    assert out[0].headline.endswith("world.")
    assert out[0].author == "POTUS"


async def test_spracherkennungsfehler_wird_korrigiert():
    lines = [(0, "I looked at hyper liquid and it is impressive.")]
    src = SpeechStreamSource("speech", transcript_from_lines(lines))
    out = [n async for n in src.stream()]
    assert "Hyperliquid" in out[0].headline


async def test_vorlaeufige_hypothesen_werden_ignoriert():
    async def chunks():
        yield TranscriptChunk("We will ban all crypto immediately.", is_final=False)
        yield TranscriptChunk("We will back all crypto immediately.", is_final=True)
    src = SpeechStreamSource("speech", chunks())
    out = [n async for n in src.stream()]
    assert len(out) == 1
    assert "back" in out[0].headline


async def test_unsichere_erkennung_wird_verworfen():
    async def chunks():
        yield TranscriptChunk("Something garbled here entirely.", confidence=0.2)
        yield TranscriptChunk("This sentence is clear and long enough.", confidence=0.95)
    src = SpeechStreamSource("speech", chunks())
    out = [n async for n in src.stream()]
    assert len(out) == 1 and "clear" in out[0].headline


async def test_kontextfenster_wird_mitgegeben():
    lines = [(0, "We will make America the crypto capital of the world."),
             (900, "And we are going to do it immediately.")]
    src = SpeechStreamSource("speech", transcript_from_lines(lines))
    out = [n async for n in src.stream()]
    assert "crypto capital" in out[1].body


async def test_halbsatz_wird_nach_wartezeit_ausgewertet():
    """Ohne Zwangs-Flush wuerde ein unbeendeter Satz Sekunden kosten."""
    lines = [(0, "We are going to impose very large tariffs on China right now"),
             (2000, "and")]
    src = SpeechStreamSource("speech", transcript_from_lines(lines), max_wait_ms=1000)
    out = [n async for n in src.stream()]
    assert out and "tariffs" in out[0].headline


# ---------------------------------------------------------------- Social
async def test_social_quelle_filtert_reposts():
    posts = [
        {"id": "1", "text": "Wir bauen eine Bitcoin-Reserve auf.", "created_ms": 1, "is_repost": False},
        {"id": "2", "text": "Geteilter Beitrag von jemand anderem.", "created_ms": 2, "is_repost": True},
    ]
    ausgabe = []

    async def fetch(account):
        return posts

    src = SocialAccountSource("realDonaldTrump", fetch, poll_s=0.01)
    src._first_pass = False
    for p in sorted(posts, key=lambda x: x["created_ms"]):
        if p.get("is_repost"):
            continue
        n = src.emit(p["text"], key=p["id"])
        if n:
            ausgabe.append(n)
    assert len(ausgabe) == 1


def test_originalaccount_ist_primaerquelle():
    assert WATCHLIST["realDonaldTrump"][1] is SourceTier.PRIMARY
    assert WATCHLIST["DeItaone"][1] is SourceTier.AGGREGATOR


async def test_unbekannter_account_ist_nur_social():
    async def fetch(a):
        return []
    src = SocialAccountSource("irgendein_bot", fetch)
    assert src.tier is SourceTier.SOCIAL


# ---------------------------------------------------------------- Katalog
def test_federal_register_parser():
    data = {"results": [{"title": "Proclamation on Tariffs", "abstract": "abc",
                         "html_url": "https://x/1", "publication_date": "2026-03-04",
                         "document_number": "2026-1234"}]}
    out = federal_register_extract(data)
    assert len(out) == 1
    assert out[0]["headline"] == "Proclamation on Tariffs"
    assert out[0]["key"] == "2026-1234"
    assert out[0]["published_ms"] > 0


def test_federal_register_parser_vertraegt_muell():
    assert federal_register_extract({}) == []
    assert federal_register_extract({"results": [{"abstract": "ohne Titel"}]}) == []


def test_katalog_laedt_und_ist_konsistent():
    specs = load_specs(ROOT / "config" / "sources.json")
    assert len(specs) > 25
    assert all(1 <= s.tier <= 5 for s in specs)
    assert all(s.latency in "ABCD" for s in specs)
    assert all(s.id for s in specs)
    # Alle Kategorien vertreten
    assert {"geldpolitik", "handel_und_zoelle", "krypto_regulierung",
            "reden_live"} <= {s.category for s in specs}
    assert summary(specs)


def test_katalog_baut_nur_lauffaehige_quellen():
    specs = load_specs(ROOT / "config" / "sources.json")
    gebaut = build_sources(specs)
    assert gebaut
    # social/speech/websocket brauchen Zugangsdaten und werden nicht automatisch gebaut
    assert len(gebaut) < len(specs)


def test_latenzfilter_wirkt():
    specs = load_specs(ROOT / "config" / "sources.json")
    assert len(build_sources(specs, max_latency_class="C")) <= len(build_sources(specs))


# ---------------------------------------------------------------- Replay
async def test_replay_liest_szenario(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"t_ms":0,"source_id":"x","tier":1,"headline":"Erste"}\n'
                 '{"t_ms":10,"source_id":"x","tier":2,"headline":"Zweite"}\n',
                 encoding="utf-8")
    src = ReplaySource(p, speed=0)
    out = [n async for n in src.stream()]
    assert [n.headline for n in out] == ["Erste", "Zweite"]
    assert out[0].tier is SourceTier.PRIMARY


def test_replay_meldet_fehlende_datei(tmp_path):
    with pytest.raises(FileNotFoundError):
        ReplaySource(tmp_path / "gibtsnicht.jsonl")
