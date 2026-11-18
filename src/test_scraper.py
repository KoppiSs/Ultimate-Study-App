"""Opt-in integration test for a running Kiwix HTTP server."""

import os
import unittest

from local_ai_search import fetch_wikipedia_text


def test_scraper():
    if os.getenv("RUN_KIWIX_TESTS") != "1":
        raise unittest.SkipTest("Requires a running Kiwix server and Wikipedia archive")
    result = fetch_wikipedia_text("Quantum mechanics")
    assert "--- Article:" in result, result[:500]


if __name__ == "__main__":
    test_scraper()
