from media_quotes.coverage import CoverageStats


def test_coverage_counts():
    stats = CoverageStats()
    stats.record("discovered", 10)
    stats.record("fetched", 9)
    stats.record("failed")
    assert stats.to_dict() == {
        "discovered": 10,
        "fetched": 9,
        "parsed": 0,
        "rejected": 0,
        "failed": 1,
        "skipped": 0,
    }
