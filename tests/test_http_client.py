from __future__ import annotations

import http.client
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from geoai_agent.errors import TransientError
from geoai_agent.http_client import (
    download_file,
    endpoint_available,
    request_json,
    reset_endpoint_health,
)


class HttpClientTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_endpoint_health()

    @patch.dict(
        "os.environ",
        {"DATA_CIRCUIT_FAILURE_THRESHOLD": "1", "DATA_CIRCUIT_COOLDOWN_SECONDS": "60"},
    )
    def test_repeated_endpoint_failure_opens_circuit(self) -> None:
        url = "https://overpass-api.de/api/interpreter"
        with (
            patch(
                "geoai_agent.http_client.request.urlopen",
                side_effect=http.client.RemoteDisconnected("remote closed connection"),
            ) as opened,
            patch("geoai_agent.http_client.time.sleep"),
        ):
            with self.assertRaises(TransientError):
                request_json(url, timeout=1, retries=0)
            self.assertFalse(endpoint_available(url))
            with self.assertRaises(TransientError):
                request_json(url, timeout=1, retries=0)
        self.assertEqual(opened.call_count, 1)

    def test_remote_disconnect_is_retried_and_classified_as_transient(self) -> None:
        error = http.client.RemoteDisconnected("remote closed connection")
        with (
            patch("geoai_agent.http_client.request.urlopen", side_effect=error) as opened,
            patch("geoai_agent.http_client.time.sleep"),
        ):
            with self.assertRaises(TransientError):
                request_json(
                    "https://vector.openstreetmap.org/shortbread_v1/tilejson.json",
                    timeout=1,
                    retries=1,
                )
        self.assertEqual(opened.call_count, 2)

    def test_vector_tile_download_wraps_remote_disconnect(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "tile.pbf"
            error = http.client.RemoteDisconnected("remote closed connection")
            with (
                patch("geoai_agent.http_client.request.urlopen", side_effect=error) as opened,
                patch("geoai_agent.http_client.time.sleep"),
            ):
                with self.assertRaises(TransientError):
                    download_file(
                        "https://vector.openstreetmap.org/shortbread_v1/11/1699/831.mvt",
                        output,
                        timeout=1,
                        retries=1,
                    )
            self.assertEqual(opened.call_count, 2)
            self.assertFalse(output.exists())

    def test_incomplete_content_length_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "tile.pbf"
            response = MagicMock()
            response.__enter__.return_value = response
            response.headers = {"Content-Length": "100"}
            response.read.side_effect = [b"short", b""]
            with (
                patch("geoai_agent.http_client.request.urlopen", return_value=response),
                patch("geoai_agent.http_client.time.sleep"),
            ):
                with self.assertRaises(TransientError):
                    download_file(
                        "https://vector.openstreetmap.org/shortbread_v1/11/1699/830.mvt",
                        output,
                        timeout=1,
                        retries=0,
                    )
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".pbf.part").exists())


if __name__ == "__main__":
    unittest.main()
