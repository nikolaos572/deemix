#!/usr/bin/env python3
"""
Standalone script to find all alternative track IDs for a Deezer track.

This script replicates the track-ID resolution logic used internally by deemix.
It queries Deezer's private GW API (``gw-light.php``) and the public REST API
(``api.deezer.com``) to discover every alternative identifier that Deezer knows
about for a given track.

Usage:
    python find_alternative_track_ids.py <track_id>

Environment:
    DEEZER_ARL  – Your Deezer ARL cookie value (required for GW API calls).
                  You can also hard-code it in the ARL variable below.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional, Set

import requests

# ---------------------------------------------------------------------------
# Configuration – set your ARL here or via the DEEZER_ARL environment variable
# ---------------------------------------------------------------------------
ARL = os.environ.get("DEEZER_ARL", "")

GW_URL = "http://www.deezer.com/ajax/gw-light.php"
PUBLIC_API_URL = "https://api.deezer.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

class DeezerSession:
    """Minimal Deezer session that mirrors the SDK's GW + public-API logic."""

    def __init__(self, arl: str) -> None:
        self.arl = arl
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        if arl:
            self.session.cookies.set("arl", arl, domain=".deezer.com")
        self.api_token: Optional[str] = None

    # -- GW helpers ---------------------------------------------------------

    def _gw_call(self, method: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """Call the Deezer GW-light API, exactly like ``GW.api_call`` in gw.ts."""
        if args is None:
            args = {}

        if self.api_token is None and method != "deezer.getUserData":
            self.api_token = self._get_token()

        params = {
            "api_version": "1.0",
            "api_token": "null" if method == "deezer.getUserData" else self.api_token,
            "input": "3",
            "method": method,
        }

        resp = self.session.post(GW_URL, params=params, json=args)
        resp.raise_for_status()
        result = resp.json()

        if result.get("error"):
            err = result["error"]
            err_str = json.dumps(err) if isinstance(err, dict) else str(err)
            # Handle expired / invalid token
            if err_str in (
                '{"GATEWAY_ERROR":"invalid api token"}',
                '{"VALID_TOKEN_REQUIRED":"Invalid CSRF token"}',
            ):
                self.api_token = self._get_token()
                return self._gw_call(method, args)
            # Handle server-side FALLBACK redirect
            payload = result.get("payload") or {}
            if payload.get("FALLBACK"):
                for key, value in payload["FALLBACK"].items():
                    args[key] = value
                return self._gw_call(method, args)
            raise RuntimeError(f"GW API error for {method}: {err_str}")

        results = result.get("results", {})

        # Persist the CSRF / API token from getUserData
        if method == "deezer.getUserData" and self.api_token is None:
            self.api_token = results.get("checkForm")

        return results

    def _get_token(self) -> str:
        data = self._gw_call("deezer.getUserData")
        return data["checkForm"]

    # -- High-level GW methods ----------------------------------------------

    def get_track_page(self, sng_id: int) -> Dict[str, Any]:
        """``deezer.pageTrack`` – returns DATA, LYRICS, ISRC, etc."""
        return self._gw_call("deezer.pageTrack", {"SNG_ID": sng_id})

    def get_track(self, sng_id: int) -> Dict[str, Any]:
        """``song.getData`` – simpler track-data call."""
        return self._gw_call("song.getData", {"SNG_ID": sng_id})

    def get_track_with_fallback(self, sng_id: int) -> Dict[str, Any]:
        """Mirrors ``GW.get_track_with_fallback`` in gw.ts."""
        body = None
        if sng_id > 0:
            try:
                body = self.get_track_page(sng_id)
            except Exception:
                pass

        if body:
            data = body.get("DATA", {})
            if body.get("LYRICS"):
                data["LYRICS"] = body["LYRICS"]
            if body.get("ISRC"):
                data["ALBUM_FALLBACK"] = body["ISRC"]
            return data

        return self.get_track(sng_id)

    def get_album_page(self, alb_id: str) -> Dict[str, Any]:
        """``deezer.pageAlbum`` – returns album metadata including SONGS."""
        return self._gw_call(
            "deezer.pageAlbum",
            {"ALB_ID": alb_id, "lang": "en", "header": True, "tab": 0},
        )

    # -- Public API helpers -------------------------------------------------

    def public_api_get(self, endpoint: str) -> Any:
        """Query the public ``api.deezer.com`` REST API."""
        url = f"{PUBLIC_API_URL}/{endpoint}"
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def get_track_by_isrc(self, isrc: str) -> Optional[Dict[str, Any]]:
        """Look up a track by ISRC via the public API."""
        data = self.public_api_get(f"track/isrc:{isrc}")
        if data.get("error"):
            return None
        return data


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def find_alternative_track_ids(dz: DeezerSession, requested_id: int) -> None:
    """Discover and print every alternative track ID for *requested_id*.

    The resolution strategy mirrors deemix's three-tier fallback:
      1. The **resolved SNG_ID** returned by ``deezer.pageTrack`` (may differ
         from the requested ID when Deezer silently redirects a track).
      2. The **FALLBACK.SNG_ID** field (an alternative version of the track).
      3. For each album listed in **ALBUM_FALLBACK**, fetch the album's track
         list and look for tracks whose ISRC matches the original.
      4. Query the public API by ISRC to find yet more alternatives.
    """

    print(f"\n{'=' * 60}")
    print(f"Looking up alternative IDs for track {requested_id}")
    print(f"{'=' * 60}\n")

    # Step 1 – fetch the track via GW
    track = dz.get_track_with_fallback(requested_id)

    resolved_id = int(track.get("SNG_ID", 0))
    title = track.get("SNG_TITLE", "Unknown")
    artist = track.get("ART_NAME", "Unknown")
    isrc = track.get("ISRC", "")
    album_title = track.get("ALB_TITLE", "Unknown")

    print(f"Track info:  {artist} – {title}")
    print(f"Album:       {album_title}")
    print(f"ISRC:        {isrc or '(none)'}")
    print()

    # Collect every discovered ID in insertion order
    alternatives: dict[int, str] = {}  # id → source label

    # -- requested vs resolved --
    if resolved_id != requested_id:
        alternatives[requested_id] = "requested (redirected away)"
    alternatives[resolved_id] = "resolved (primary)"

    # -- FALLBACK field --
    fallback = track.get("FALLBACK")
    if fallback and fallback.get("SNG_ID"):
        fb_id = int(fallback["SNG_ID"])
        if fb_id not in alternatives:
            alternatives[fb_id] = "FALLBACK"

    # -- ALBUM_FALLBACK (alternative albums sharing the ISRC) --
    album_fallback = track.get("ALBUM_FALLBACK")
    alt_album_ids: List[str] = []
    if isinstance(album_fallback, dict) and album_fallback.get("data"):
        for album_entry in album_fallback["data"]:
            alb_id = album_entry.get("ALB_ID")
            if alb_id:
                alt_album_ids.append(str(alb_id))
    elif isinstance(album_fallback, list):
        # The ISRC field from pageTrack is sometimes a flat list
        for entry in album_fallback:
            alb_id = entry.get("ALB_ID") if isinstance(entry, dict) else None
            if alb_id:
                alt_album_ids.append(str(alb_id))

    if alt_album_ids and isrc:
        print(f"Scanning {len(alt_album_ids)} alternative album(s) for ISRC match …")
        for alb_id in alt_album_ids:
            try:
                album_page = dz.get_album_page(alb_id)
                songs = album_page.get("SONGS", {}).get("data", [])
                for song in songs:
                    if song.get("ISRC") == isrc:
                        sid = int(song["SNG_ID"])
                        if sid not in alternatives:
                            alternatives[sid] = f"ISRC match in album {alb_id}"
            except Exception as exc:
                print(f"  ⚠  Could not fetch album {alb_id}: {exc}")

    # -- Public API ISRC lookup --
    if isrc:
        try:
            pub_track = dz.get_track_by_isrc(isrc)
            if pub_track and pub_track.get("id"):
                pid = int(pub_track["id"])
                if pid not in alternatives:
                    alternatives[pid] = "public API (ISRC lookup)"
        except Exception:
            pass

    # -- Print results ------------------------------------------------------
    print(f"\n{'─' * 60}")
    print(f"Found {len(alternatives)} track ID(s):\n")
    for tid, source in alternatives.items():
        marker = " ← requested" if tid == requested_id else ""
        print(f"  {tid:>12}  ({source}){marker}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    try:
        track_id = int(sys.argv[1])
    except ValueError:
        print(f"Error: '{sys.argv[1]}' is not a valid track ID.", file=sys.stderr)
        sys.exit(1)

    arl = ARL
    if not arl:
        print(
            "Error: No ARL token found.\n"
            "Set the DEEZER_ARL environment variable or edit the ARL variable "
            "at the top of this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    dz = DeezerSession(arl)
    find_alternative_track_ids(dz, track_id)


if __name__ == "__main__":
    main()
