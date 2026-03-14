"""
Fetch Artist Featured Releases from the Deezer GW API

This script replicates how Deemix fetches "featured" releases for a specific
artist from the Deezer internal (gateway) API. There is no official public
Deezer API endpoint for featured releases, but the internal GW-light API
exposes them through the ``album.getDiscography`` method.

Mechanism
---------
1. A CSRF token is obtained by calling ``deezer.getUserData`` on the GW API.
2. The ``album.getDiscography`` method is called with the artist ID and
   ``discography_mode: "all"`` to retrieve every release associated with the
   artist (paginated in batches of 100).
3. Each release carries a ``ROLE_ID`` field that indicates the artist's role on
   that release:
   - ``0`` – Main artist
   - ``5`` – Featured artist
4. Releases are de-duplicated by album ID and categorised into ``all``,
   ``featured``, and ``more`` buckets, mirroring the logic in
   ``packages/deezer-sdk/src/gw.ts`` (``get_artist_discography_tabs``).
5. For each featured album, the individual tracks are fetched via
   ``song.getListByAlbum``.  Tracks where the queried artist appears in the
   ``ARTISTS`` array are collected into a ``featuredTracks`` list so that
   consumers can access both the album-level and track-level featured data.

Usage
-----
::

    python fetch_artist_features.py <artist_id> [output.json]

Example::

    python fetch_artist_features.py 810507 artist_features.json

Requirements
------------
* Python 3.8+
* ``requests`` (``pip install requests``)
"""

from __future__ import annotations

import json
import sys
from typing import Any

import requests

GW_URL = "http://www.deezer.com/ajax/gw-light.php"

RELEASE_TYPES = ["single", "album", "compile", "ep", "bundle"]
ROLE_NAMES = ["Main", None, None, None, None, "Featured"]


def _gw_api_call(
    session: requests.Session,
    method: str,
    args: dict[str, Any] | None = None,
    api_token: str = "null",
) -> Any:
    """Perform a single call to the Deezer GW-light API."""
    params = {
        "api_version": "1.0",
        "api_token": api_token,
        "input": "3",
        "method": method,
    }
    response = session.post(GW_URL, params=params, json=args or {})
    response.raise_for_status()
    data = response.json()
    if data.get("error") and (
        isinstance(data["error"], list)
        and len(data["error"])
        or isinstance(data["error"], dict)
        and len(data["error"])
    ):
        raise RuntimeError(f"GW API error for {method}: {data['error']}")
    return data["results"]


def get_api_token(session: requests.Session) -> str:
    """Obtain a CSRF token from ``deezer.getUserData``."""
    user_data = _gw_api_call(session, "deezer.getUserData")
    return user_data["checkForm"]


def get_artist_discography(
    session: requests.Session,
    api_token: str,
    artist_id: int,
    index: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Fetch a page of the artist's full discography."""
    return _gw_api_call(
        session,
        "album.getDiscography",
        {
            "ART_ID": artist_id,
            "discography_mode": "all",
            "nb": limit,
            "nb_songs": 0,
            "start": index,
        },
        api_token=api_token,
    )


def get_album_tracks(
    session: requests.Session,
    api_token: str,
    album_id: int | str,
) -> list[dict[str, Any]]:
    """Fetch all tracks for a given album via ``song.getListByAlbum``."""
    body = _gw_api_call(
        session,
        "song.getListByAlbum",
        {"ALB_ID": album_id, "nb": -1},
        api_token=api_token,
    )
    tracks: list[dict[str, Any]] = []
    for idx, track in enumerate(body.get("data", [])):
        track["POSITION"] = idx
        tracks.append(track)
    return tracks


def map_gw_track(track: dict[str, Any]) -> dict[str, Any]:
    """Map a GW-light track object to the standardised Deezer API format.

    This mirrors ``mapGwTrackToDeezer`` in ``packages/deezer-sdk/src/utils.ts``.
    """
    sng_id = track.get("SNG_ID", 0)
    alb_id = track.get("ALB_ID", "")
    alb_picture = track.get("ALB_PICTURE", "")
    art_id = track.get("ART_ID", "")

    result: dict[str, Any] = {
        "id": sng_id,
        "readable": True,
        "title": track.get("SNG_TITLE", ""),
        "title_short": track.get("SNG_TITLE", ""),
        "isrc": track.get("ISRC"),
        "link": f"https://www.deezer.com/track/{sng_id}",
        "duration": track.get("DURATION"),
        "md5_image": alb_picture,
        "artist": {
            "id": art_id,
            "name": track.get("ART_NAME", ""),
            "link": f"https://www.deezer.com/artist/{art_id}",
            "tracklist": f"https://api.deezer.com/artist/{art_id}/top?limit=50",
            "type": "artist",
        },
        "album": {
            "id": alb_id,
            "title": track.get("ALB_TITLE", ""),
            "link": f"https://www.deezer.com/album/{alb_id}",
            "cover": f"https://api.deezer.com/album/{alb_id}/image",
            "cover_small": f"https://e-cdns-images.dzcdn.net/images/cover/{alb_picture}/56x56-000000-80-0-0.jpg",
            "cover_medium": f"https://e-cdns-images.dzcdn.net/images/cover/{alb_picture}/250x250-000000-80-0-0.jpg",
            "cover_big": f"https://e-cdns-images.dzcdn.net/images/cover/{alb_picture}/500x500-000000-80-0-0.jpg",
            "cover_xl": f"https://e-cdns-images.dzcdn.net/images/cover/{alb_picture}/1000x1000-000000-80-0-0.jpg",
            "md5_image": alb_picture,
            "tracklist": f"https://api.deezer.com/album/{alb_id}/tracks",
            "type": "album",
        },
        "type": "track",
    }

    version = (track.get("VERSION") or "").strip()
    if version:
        title_short = result["title_short"]
        if version in title_short:
            title_short = title_short.replace(version, "").strip()
        result["title_short"] = title_short
        result["title"] = f"{title_short} {version}".strip()
        result["title_version"] = version

    if track.get("ARTISTS"):
        result["contributors"] = [
            {
                "id": c.get("ART_ID"),
                "name": c.get("ART_NAME"),
                "link": f"https://www.deezer.com/artist/{c.get('ART_ID')}",
                "role": (
                    ROLE_NAMES[c["ROLE_ID"]]
                    if c.get("ROLE_ID") is not None
                    and c["ROLE_ID"] < len(ROLE_NAMES)
                    else None
                ),
                "type": "artist",
            }
            for c in track["ARTISTS"]
        ]

    return result


def _is_explicit(explicit_lyrics: Any) -> bool:
    """Return ``True`` when the content is marked as explicit."""
    try:
        status = int(explicit_lyrics)
    except (TypeError, ValueError):
        status = 2  # Unknown
    return status in (1, 4)  # EXPLICIT or PARTIALLY_EXPLICIT


def map_artist_album(album: dict[str, Any]) -> dict[str, Any]:
    """Map a GW-light album object to the standardised Deezer API format.

    This mirrors ``map_artist_album`` in ``packages/deezer-sdk/src/utils.ts``.
    """
    alb_id = album["ALB_ID"]
    alb_picture = album.get("ALB_PICTURE", "")
    release_type_index = int(album.get("TYPE", 0))
    explicit_album = album.get("EXPLICIT_ALBUM_CONTENT", {})

    return {
        "id": alb_id,
        "title": album.get("ALB_TITLE", ""),
        "link": f"https://www.deezer.com/album/{alb_id}",
        "cover": f"https://api.deezer.com/album/{alb_id}/image",
        "cover_small": f"https://cdns-images.dzcdn.net/images/cover/{alb_picture}/56x56-000000-80-0-0.jpg",
        "cover_medium": f"https://cdns-images.dzcdn.net/images/cover/{alb_picture}/250x250-000000-80-0-0.jpg",
        "cover_big": f"https://cdns-images.dzcdn.net/images/cover/{alb_picture}/500x500-000000-80-0-0.jpg",
        "cover_xl": f"https://cdns-images.dzcdn.net/images/cover/{alb_picture}/1000x1000-000000-80-0-0.jpg",
        "md5_image": alb_picture,
        "genre_id": album.get("GENRE_ID"),
        "fans": None,
        "release_date": album.get("PHYSICAL_RELEASE_DATE"),
        "record_type": (
            RELEASE_TYPES[release_type_index]
            if release_type_index < len(RELEASE_TYPES)
            else "unknown"
        ),
        "tracklist": f"https://api.deezer.com/album/{alb_id}/tracks",
        "explicit_lyrics": _is_explicit(album.get("EXPLICIT_LYRICS")),
        "type": album.get("__TYPE__"),
        # Extras
        "nb_tracks": album.get("NUMBER_TRACK"),
        "nb_disk": album.get("NUMBER_DISK"),
        "copyright": album.get("COPYRIGHT"),
        "rank": album.get("RANK"),
        "digital_release_date": album.get("DIGITAL_RELEASE_DATE"),
        "original_release_date": album.get("ORIGINAL_RELEASE_DATE"),
        "physical_release_date": album.get("PHYSICAL_RELEASE_DATE"),
        "is_official": album.get("ARTISTS_ALBUMS_IS_OFFICIAL"),
        "explicit_content_cover": explicit_album.get("EXPLICIT_LYRICS_STATUS"),
        "explicit_content_lyrics": explicit_album.get("EXPLICIT_COVER_STATUS"),
        "artist_role": (
            ROLE_NAMES[album["ROLE_ID"]]
            if album.get("ROLE_ID") is not None
            and album["ROLE_ID"] < len(ROLE_NAMES)
            else None
        ),
    }


def get_artist_discography_tabs(
    artist_id: int,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch and categorise all releases for *artist_id*.

    This mirrors ``get_artist_discography_tabs`` in
    ``packages/deezer-sdk/src/gw.ts``.

    Returns a dict with at least the keys ``all``, ``featured``,
    ``featuredTracks``, and ``more``.  Additional keys are created for each
    record type encountered (e.g. ``single``, ``album``, ``compile``, ``ep``).
    """
    session = requests.Session()
    api_token = get_api_token(session)

    # Paginate through all releases
    releases: list[dict[str, Any]] = []
    index = 0
    limit = 100
    while True:
        page = get_artist_discography(session, api_token, artist_id, index, limit)
        releases.extend(page.get("data", []))
        index += limit
        if index >= page.get("total", 0):
            break

    # Categorise releases (same logic as the TypeScript implementation)
    result: dict[str, list[dict[str, Any]]] = {
        "all": [],
        "featured": [],
        "featuredTracks": [],
        "more": [],
    }
    seen_ids: set[str] = set()

    for release in releases:
        alb_id = str(release.get("ALB_ID", ""))
        if alb_id in seen_ids:
            continue
        seen_ids.add(alb_id)

        obj = map_artist_album(release)
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

    # Fetch individual tracks from featured albums
    for featured_album in result["featured"]:
        try:
            album_tracks = get_album_tracks(session, api_token, featured_album["id"])
            for track in album_tracks:
                artists = track.get("ARTISTS", [])
                if any(str(a.get("ART_ID")) == str(artist_id) for a in artists):
                    result["featuredTracks"].append(map_gw_track(track))
        except Exception:
            # Skip albums where track fetching fails
            pass

    return result


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <artist_id> [output.json]")
        sys.exit(1)

    artist_id = int(sys.argv[1])
    output_path = sys.argv[2] if len(sys.argv) > 2 else "artist_features.json"

    print(f"Fetching discography tabs for artist {artist_id} …")
    tabs = get_artist_discography_tabs(artist_id)

    for key, items in tabs.items():
        print(f"  {key}: {len(items)} release(s)")

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(tabs, fh, indent=2, ensure_ascii=False)

    print(f"Saved to {output_path}")


if __name__ == "__main__":
    main()
