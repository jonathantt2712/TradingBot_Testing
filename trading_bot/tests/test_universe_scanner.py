"""UniverseScanner.get_breakouts: sudden-mover detection for the 5-min monitor."""
import asyncio

from data.universe_scanner import UniverseScanner


def _mover(symbol, pct=5.0, price=50.0, volume=1_000_000, name=""):
    return {
        "symbol": symbol,
        "percent_change": pct,
        "price": price,
        "volume": volume,
        "name": name,
    }


def _scanner_with_movers(monkeypatch, gainers=None, losers=None):
    scanner = UniverseScanner("key", "secret")

    async def fake_fetch_market_movers(self, session, top=25):
        return {"gainers": gainers or [], "losers": losers or []}

    monkeypatch.setattr(UniverseScanner, "_fetch_market_movers", fake_fetch_market_movers)
    return scanner


def test_returns_big_movers_not_already_tracked(monkeypatch):
    scanner = _scanner_with_movers(monkeypatch, gainers=[_mover("NEWCO", pct=8.0)])
    result = asyncio.run(scanner.get_breakouts(existing_tickers=set()))
    assert result == ["NEWCO"]


def test_excludes_existing_tickers(monkeypatch):
    scanner = _scanner_with_movers(monkeypatch, gainers=[_mover("AAPL", pct=8.0)])
    result = asyncio.run(scanner.get_breakouts(existing_tickers={"AAPL"}))
    assert result == []


def test_excludes_known_etfs_and_etf_like_names(monkeypatch):
    scanner = _scanner_with_movers(monkeypatch, gainers=[
        _mover("TQQQ", pct=10.0),                       # known ETF ticker
        _mover("XYZ", pct=10.0, name="XYZ Leveraged Trust"),  # ETF-like name
    ])
    result = asyncio.run(scanner.get_breakouts(existing_tickers=set()))
    assert result == []


def test_below_change_threshold_excluded(monkeypatch):
    scanner = _scanner_with_movers(monkeypatch, gainers=[_mover("SMALL", pct=1.0)])
    result = asyncio.run(scanner.get_breakouts(existing_tickers=set(), min_change_pct=3.0))
    assert result == []


def test_price_and_volume_filters(monkeypatch):
    scanner = _scanner_with_movers(monkeypatch, gainers=[
        _mover("PENNY", pct=10.0, price=1.0),       # below min_price
        _mover("THIN",  pct=10.0, volume=100),       # below min_volume
        _mover("GOOD",  pct=10.0, price=50.0, volume=1_000_000),
    ])
    result = asyncio.run(scanner.get_breakouts(existing_tickers=set()))
    assert result == ["GOOD"]


def test_sorted_by_momentum_and_capped_at_top(monkeypatch):
    movers = [_mover(f"SYM{i}", pct=3.0 + i) for i in range(15)]
    scanner = _scanner_with_movers(monkeypatch, gainers=movers)
    result = asyncio.run(scanner.get_breakouts(existing_tickers=set(), top=10))
    assert len(result) == 10
    # Highest % change (SYM14) should be ranked first.
    assert result[0] == "SYM14"
