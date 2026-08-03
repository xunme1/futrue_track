import os
import unittest
from unittest.mock import mock_open, patch

from backend.core.config import load_config, load_contracts


class ConfigTests(unittest.TestCase):
    def test_environment_overrides_credentials_and_source(self):
        content = """
account: {username: yaml-user, password: yaml-pass}
ricequant: {license_key: yaml-key}
data_source: {futures: ifind}
"""
        env = {
            "FUTURES_IFIND_USERNAME": "env-user",
            "FUTURES_IFIND_PASSWORD": "env-pass",
            "FUTURES_RQDATA_LICENSE_KEY": "env-key",
            "FUTURES_DATA_SOURCE": "ricequant",
        }
        with patch("builtins.open", mock_open(read_data=content)):
            with patch.dict(os.environ, env, clear=False):
                cfg = load_config("config.yaml")

        self.assertEqual(cfg["account"]["username"], "env-user")
        self.assertEqual(cfg["account"]["password"], "env-pass")
        self.assertEqual(cfg["ricequant"]["license_key"], "env-key")
        self.assertEqual(cfg["data_source"]["futures"], "ricequant")

    def test_contracts_reject_duplicate_symbols(self):
        content = """
contracts:
  - {symbol: rb2610.SHF, source: ricequant}
  - {symbol: rb2610.SHF, source: ricequant}
"""
        with patch("builtins.open", mock_open(read_data=content)):
            with self.assertRaisesRegex(ValueError, "重复合约"):
                load_contracts("contracts.yaml")


if __name__ == "__main__":
    unittest.main()
