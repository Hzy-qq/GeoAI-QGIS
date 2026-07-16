from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from geoai_agent.redis_bus import check_redis, redis_enabled


class RedisFallbackTests(unittest.TestCase):
    def test_unconfigured_redis_is_an_explicit_optional_fallback(self) -> None:
        with patch.dict(os.environ, {"REDIS_URL": ""}, clear=False):
            self.assertFalse(redis_enabled())
            health = check_redis()
            self.assertFalse(health["configured"])
            self.assertFalse(health["required"])
            self.assertIn("fallback", health["detail"].lower())


if __name__ == "__main__":
    unittest.main()
