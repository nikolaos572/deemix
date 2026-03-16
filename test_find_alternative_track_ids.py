#!/usr/bin/env python3
"""Unit tests for find_alternative_track_ids.py"""

import io
import sys
import unittest
from unittest.mock import patch

# Import the module under test
sys.path.insert(0, ".")
import find_alternative_track_ids as fat


class TestDeezerSession(unittest.TestCase):
    """Verify DeezerSession builds requests correctly."""

    def test_session_sets_arl_cookie(self):
        dz = fat.DeezerSession("test_arl_value")
        cookies = dz.session.cookies.get_dict()
        self.assertEqual(cookies.get("arl"), "test_arl_value")

    def test_session_sets_user_agent(self):
        dz = fat.DeezerSession("x")
        self.assertIn("Mozilla", dz.session.headers["User-Agent"])


class TestFindAlternativeTrackIDs(unittest.TestCase):
    """Test the core ID-resolution logic with mocked API responses."""

    def _make_session(self):
        dz = fat.DeezerSession("fake_arl")
        dz.api_token = "fake_token"
        return dz

    @patch.object(fat.DeezerSession, "get_track_with_fallback")
    @patch.object(fat.DeezerSession, "get_track_by_isrc")
    def test_redirect_detected(self, mock_isrc, mock_fallback):
        """When the resolved SNG_ID differs from the requested one, both appear."""
        mock_fallback.return_value = {
            "SNG_ID": 492212992,
            "SNG_TITLE": "Therapy (Club Mix)",
            "ART_NAME": "Armin van Buuren",
            "ALB_TITLE": "Balance",
            "ISRC": "NLF711804279",
            "FALLBACK": {"SNG_ID": 492212993},
        }
        mock_isrc.return_value = {"id": 500000001}

        dz = self._make_session()

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            fat.find_alternative_track_ids(dz, 496430132)

        output = buf.getvalue()
        self.assertIn("496430132", output)
        self.assertIn("492212992", output)
        self.assertIn("492212993", output)
        self.assertIn("500000001", output)

    @patch.object(fat.DeezerSession, "get_track_with_fallback")
    @patch.object(fat.DeezerSession, "get_track_by_isrc")
    def test_no_redirect(self, mock_isrc, mock_fallback):
        """When the resolved ID matches the requested one, no redirect note."""
        mock_fallback.return_value = {
            "SNG_ID": 3135556,
            "SNG_TITLE": "Strobe",
            "ART_NAME": "deadmau5",
            "ALB_TITLE": "For Lack of a Better Name",
            "ISRC": "USERRE0200200",
        }
        mock_isrc.return_value = None

        dz = self._make_session()

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            fat.find_alternative_track_ids(dz, 3135556)

        output = buf.getvalue()
        self.assertIn("3135556", output)
        self.assertNotIn("redirected", output)

    @patch.object(fat.DeezerSession, "get_track_with_fallback")
    @patch.object(fat.DeezerSession, "get_album_page")
    @patch.object(fat.DeezerSession, "get_track_by_isrc")
    def test_album_fallback_isrc_match(self, mock_isrc, mock_album, mock_fallback):
        """Tracks in alternative albums with matching ISRC are discovered."""
        mock_fallback.return_value = {
            "SNG_ID": 3135556,
            "SNG_TITLE": "Strobe",
            "ART_NAME": "deadmau5",
            "ALB_TITLE": "For Lack of a Better Name",
            "ISRC": "USERRE0200200",
            "ALBUM_FALLBACK": {
                "data": [{"ALB_ID": "999001"}, {"ALB_ID": "999002"}]
            },
        }
        mock_album.side_effect = [
            {
                "SONGS": {
                    "data": [
                        {"SNG_ID": 7777777, "ISRC": "USERRE0200200"},
                        {"SNG_ID": 8888888, "ISRC": "OTHER"},
                    ]
                }
            },
            {
                "SONGS": {
                    "data": [
                        {"SNG_ID": 9999999, "ISRC": "NOPE"},
                    ]
                }
            },
        ]
        mock_isrc.return_value = None

        dz = self._make_session()

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            fat.find_alternative_track_ids(dz, 3135556)

        output = buf.getvalue()
        self.assertIn("3135556", output)
        self.assertIn("7777777", output)
        self.assertNotIn("8888888", output)
        self.assertNotIn("9999999", output)


class TestCLI(unittest.TestCase):
    """Test command-line argument handling."""

    def test_no_args_shows_usage(self):
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.argv", ["prog"]):
                fat.main()
        self.assertEqual(cm.exception.code, 1)

    def test_invalid_track_id(self):
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.argv", ["prog", "not_a_number"]):
                fat.main()
        self.assertEqual(cm.exception.code, 1)

    def test_missing_arl(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(fat, "ARL", ""):
                with self.assertRaises(SystemExit) as cm:
                    with patch("sys.argv", ["prog", "12345"]):
                        fat.main()
                self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
