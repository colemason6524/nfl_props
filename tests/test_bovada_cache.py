"""Cache-safety tests for the Bovada fetcher.

The core invariant: an HTTP 200 response with an invalid shape (`{}`, or
anything that is not a list) must never overwrite a valid last-good cache.
Transient failures retry once, then fall back to the cache; if there is no
valid cache the fetcher must raise rather than export a silent empty board.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nfl_props.sources import bovada

VALID = [{"path": [{"description": "Football"}], "events": []}]


class FetchJsonCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_dir = Path(self.tmp.name)
        patcher = mock.patch.object(bovada, "CACHE_DIR", self.cache_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _seed_cache(self, name="bovada_nfl.json", payload=None):
        path = self.cache_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload if payload is not None else VALID))

    def test_fresh_valid_list_writes_cache(self):
        with mock.patch.object(bovada, "_fetch_once",
                               return_value=("[{\"x\": 1}]", 200, 8)):
            res = bovada._fetch_json("http://x", "bovada_nfl.json")
        self.assertEqual(res.mode, "fresh")
        self.assertEqual(res.attempts, 1)
        self.assertEqual(res.http_status, 200)
        self.assertEqual(res.payload, [{"x": 1}])

    def test_http200_empty_dict_does_not_overwrite_valid_cache(self):
        self._seed_cache()
        with mock.patch.object(bovada, "_fetch_once",
                               return_value=("{}", 200, 2)):
            res = bovada._fetch_json("http://x", "bovada_nfl.json")
        self.assertEqual(res.mode, "cache")
        self.assertEqual(res.payload, VALID)
        self.assertIsNotNone(res.error)

    def test_http200_empty_dict_without_cache_raises(self):
        with mock.patch.object(bovada, "_fetch_once",
                               return_value=("{}", 200, 2)):
            with self.assertRaises(RuntimeError):
                bovada._fetch_json("http://x", "bovada_nfl.json")

    def test_malformed_json_falls_back_to_cache(self):
        self._seed_cache()
        with mock.patch.object(bovada, "_fetch_once",
                               return_value=("not json", 200, 8)):
            res = bovada._fetch_json("http://x", "bovada_nfl.json")
        self.assertEqual(res.mode, "cache")
        self.assertEqual(res.payload, VALID)

    def test_empty_list_is_a_valid_fresh_slate(self):
        with mock.patch.object(bovada, "_fetch_once",
                               return_value=("[]", 200, 2)):
            res = bovada._fetch_json("http://x", "bovada_nfl.json")
        self.assertEqual(res.mode, "fresh")
        self.assertEqual(res.payload, [])

    def test_network_error_retries_once_then_uses_cache(self):
        self._seed_cache()
        with mock.patch.object(bovada, "_fetch_once",
                               side_effect=OSError("boom")) as fetch:
            with mock.patch.object(bovada.time, "sleep", return_value=None):
                res = bovada._fetch_json("http://x", "bovada_nfl.json")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(res.mode, "cache")
        self.assertEqual(res.attempts, 2)

    def test_client_404_does_not_retry(self):
        from urllib.error import HTTPError
        err = HTTPError("http://x", 404, "nope", None, None)
        self._seed_cache()
        with mock.patch.object(bovada, "_fetch_once", side_effect=err) as fetch:
            res = bovada._fetch_json("http://x", "bovada_nfl.json")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(res.mode, "cache")

    def test_no_refresh_reads_valid_cache(self):
        self._seed_cache()
        res = bovada._fetch_json("http://x", "bovada_nfl.json", refresh=False)
        self.assertEqual(res.mode, "cache")
        self.assertEqual(res.payload, VALID)

    def test_no_refresh_with_invalid_cache_raises(self):
        self._seed_cache(payload={})
        with self.assertRaises(RuntimeError):
            bovada._fetch_json("http://x", "bovada_nfl.json", refresh=False)


if __name__ == "__main__":
    unittest.main()
