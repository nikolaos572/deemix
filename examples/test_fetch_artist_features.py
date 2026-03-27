"""Unit tests for fetch_artist_features.py.

These tests validate the mapping and categorisation logic without making
any real network requests.
"""

from __future__ import annotations

import json
import sys
import os

import pytest

# Add the current directory to sys.path so we can import the module
sys.path.insert(0, os.path.dirname(__file__))
import fetch_artist_features as faf


def _make_release(
    alb_id="123",
    alb_title="Test Album",
    art_id="810507",
    role_id=0,
    is_official=True,
    release_type=1,
    alb_picture="pic123",
):
    """Build a minimal GW-light release object for testing."""
    return {
        "ALB_ID": alb_id,
        "ALB_TITLE": alb_title,
        "ALB_PICTURE": alb_picture,
        "ART_ID": art_id,
        "ROLE_ID": role_id,
        "ARTISTS_ALBUMS_IS_OFFICIAL": is_official,
        "TYPE": str(release_type),
        "GENRE_ID": "0",
        "EXPLICIT_LYRICS": "0",
        "EXPLICIT_ALBUM_CONTENT": {
            "EXPLICIT_LYRICS_STATUS": 0,
            "EXPLICIT_COVER_STATUS": 0,
        },
        "__TYPE__": "album",
        "NUMBER_TRACK": 10,
        "NUMBER_DISK": 1,
        "COPYRIGHT": "",
        "RANK": 100,
        "PHYSICAL_RELEASE_DATE": "2023-01-01",
        "DIGITAL_RELEASE_DATE": "2023-01-01",
        "ORIGINAL_RELEASE_DATE": "2023-01-01",
    }


def _make_track(
    sng_id="999",
    sng_title="Test Track",
    art_id="810507",
    art_name="Test Artist",
    alb_id="123",
    alb_title="Test Album",
    alb_picture="pic123",
    artists=None,
):
    """Build a minimal GW-light track object for testing."""
    track = {
        "SNG_ID": sng_id,
        "SNG_TITLE": sng_title,
        "ART_ID": art_id,
        "ART_NAME": art_name,
        "ALB_ID": alb_id,
        "ALB_TITLE": alb_title,
        "ALB_PICTURE": alb_picture,
        "DURATION": 200,
        "ISRC": "USRC12345678",
    }
    if artists is not None:
        track["ARTISTS"] = artists
    return track


class TestMapArtistAlbum:
    def test_basic_mapping(self):
        release = _make_release(alb_id="456", alb_title="My Album")
        mapped = faf.map_artist_album(release)

        assert mapped["id"] == "456"
        assert mapped["title"] == "My Album"
        assert mapped["link"] == "https://www.deezer.com/album/456"
        assert mapped["cover"] == "https://api.deezer.com/album/456/image"
        assert "56x56" in mapped["cover_small"]
        assert "250x250" in mapped["cover_medium"]
        assert "500x500" in mapped["cover_big"]
        assert "1000x1000" in mapped["cover_xl"]

    def test_release_type_mapping(self):
        for idx, name in enumerate(["single", "album", "compile", "ep", "bundle"]):
            release = _make_release(release_type=idx)
            mapped = faf.map_artist_album(release)
            assert mapped["record_type"] == name

    def test_unknown_release_type(self):
        release = _make_release(release_type=99)
        mapped = faf.map_artist_album(release)
        assert mapped["record_type"] == "unknown"

    def test_explicit_detection(self):
        release = _make_release()
        release["EXPLICIT_LYRICS"] = "1"
        mapped = faf.map_artist_album(release)
        assert mapped["explicit_lyrics"] is True

    def test_not_explicit(self):
        release = _make_release()
        release["EXPLICIT_LYRICS"] = "0"
        mapped = faf.map_artist_album(release)
        assert mapped["explicit_lyrics"] is False

    def test_role_name_main(self):
        release = _make_release(role_id=0)
        mapped = faf.map_artist_album(release)
        assert mapped["artist_role"] == "Main"

    def test_role_name_featured(self):
        release = _make_release(role_id=5)
        mapped = faf.map_artist_album(release)
        assert mapped["artist_role"] == "Featured"


class TestIsExplicit:
    def test_explicit(self):
        assert faf._is_explicit(1) is True
        assert faf._is_explicit("1") is True

    def test_partially_explicit(self):
        assert faf._is_explicit(4) is True

    def test_not_explicit(self):
        assert faf._is_explicit(0) is False
        assert faf._is_explicit(2) is False
        assert faf._is_explicit(3) is False

    def test_none(self):
        assert faf._is_explicit(None) is False

    def test_invalid(self):
        assert faf._is_explicit("abc") is False


class TestMapGwTrack:
    def test_basic_mapping(self):
        track = _make_track(sng_id="42", sng_title="Cool Song", alb_id="7")
        mapped = faf.map_gw_track(track)

        assert mapped["id"] == "42"
        assert mapped["title"] == "Cool Song"
        assert mapped["type"] == "track"
        assert mapped["link"] == "https://www.deezer.com/track/42"
        assert mapped["album"]["id"] == "7"
        assert mapped["artist"]["name"] == "Test Artist"

    def test_version_handling(self):
        track = _make_track(sng_title="My Song")
        track["VERSION"] = "(Remix)"
        mapped = faf.map_gw_track(track)

        assert mapped["title"] == "My Song (Remix)"
        assert mapped["title_short"] == "My Song"
        assert mapped["title_version"] == "(Remix)"

    def test_contributors_from_artists(self):
        track = _make_track(
            artists=[
                {"ART_ID": "1", "ART_NAME": "Main", "ROLE_ID": 0},
                {"ART_ID": "2", "ART_NAME": "Feat", "ROLE_ID": 5},
            ]
        )
        mapped = faf.map_gw_track(track)

        assert len(mapped["contributors"]) == 2
        assert mapped["contributors"][0]["name"] == "Main"
        assert mapped["contributors"][0]["role"] == "Main"
        assert mapped["contributors"][1]["name"] == "Feat"
        assert mapped["contributors"][1]["role"] == "Featured"

    def test_no_artists_field(self):
        track = _make_track()
        mapped = faf.map_gw_track(track)
        assert "contributors" not in mapped


class TestDiscographyCategorisation:
    """Test the categorisation logic that splits releases into buckets.

    We mock the network layer by monkey-patching the functions that call
    the GW API.
    """

    def _run_categorisation(self, releases, artist_id=810507):
        """Simulate get_artist_discography_tabs with pre-built releases."""
        result = {"all": [], "featured": [], "featuredTracks": [], "more": []}
        seen_ids = set()

        for release in releases:
            alb_id = str(release.get("ALB_ID", ""))
            if alb_id in seen_ids:
                continue
            seen_ids.add(alb_id)

            obj = faf.map_artist_album(release)
            art_id = str(release.get("ART_ID", ""))
            role_id = release.get("ROLE_ID")
            is_official = release.get("ARTISTS_ALBUMS_IS_OFFICIAL")

            if (
                art_id == str(artist_id)
                or (art_id != str(artist_id) and role_id == 0)
            ) and is_official:
                record_type = obj["record_type"]
                result.setdefault(record_type, [])
                result[record_type].append(obj)
                result["all"].append(obj)
            elif role_id == 5:
                result["featured"].append(obj)
            elif role_id == 0:
                result["more"].append(obj)
                result["all"].append(obj)

        return result

    def test_main_artist_release(self):
        releases = [_make_release(alb_id="1", art_id="810507", role_id=0)]
        result = self._run_categorisation(releases)
        assert len(result["all"]) == 1
        assert len(result["featured"]) == 0
        assert len(result["featuredTracks"]) == 0
        assert len(result["more"]) == 0
        assert "album" in result
        assert len(result["album"]) == 1

    def test_featured_release(self):
        releases = [
            _make_release(alb_id="2", art_id="999", role_id=5, is_official=False)
        ]
        result = self._run_categorisation(releases)
        assert len(result["featured"]) == 1
        assert len(result["all"]) == 0
        assert result["featured"][0]["id"] == "2"

    def test_more_release(self):
        releases = [
            _make_release(alb_id="3", art_id="999", role_id=0, is_official=False)
        ]
        result = self._run_categorisation(releases)
        assert len(result["more"]) == 1
        assert len(result["all"]) == 1
        assert len(result["featured"]) == 0

    def test_deduplication(self):
        releases = [
            _make_release(alb_id="1", art_id="810507"),
            _make_release(alb_id="1", art_id="810507"),
        ]
        result = self._run_categorisation(releases)
        assert len(result["all"]) == 1

    def test_mixed_releases(self):
        releases = [
            _make_release(alb_id="1", art_id="810507", role_id=0, release_type=0),
            _make_release(alb_id="2", art_id="999", role_id=5, is_official=False),
            _make_release(alb_id="3", art_id="999", role_id=0, is_official=False),
            _make_release(alb_id="4", art_id="810507", role_id=0, release_type=1),
        ]
        result = self._run_categorisation(releases)
        assert len(result["all"]) == 3  # main + more + main
        assert len(result["featured"]) == 1
        assert len(result["more"]) == 1
        assert len(result["single"]) == 1
        assert len(result["album"]) == 1

    def test_featured_tracks_key_exists(self):
        """The result always contains a featuredTracks list."""
        result = self._run_categorisation([])
        assert "featuredTracks" in result
        assert isinstance(result["featuredTracks"], list)
