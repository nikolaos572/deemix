#!/usr/bin/env python3
"""
Standalone Script to Download Artist Information from Spotify

This script downloads complete artist metadata (albums and tracks) from Spotify
and saves it to a JSON file using the same format as Zotify.

Usage:
    python download_artist_info.py <artist_url_or_txt_file>
    
Examples:
    python download_artist_info.py https://open.spotify.com/artist/6vbY3hOaCAhC7VjucswgdS
    python download_artist_info.py artists.txt
"""

import base64
import datetime
import json
import os
import re
import signal
import sys
import time
import random
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

# ============================================================================
# GLOBAL STATE
# ============================================================================

# Flag to track graceful shutdown
_shutdown_requested = False

def signal_handler(signum, frame):
    """Handle CTRL+C gracefully"""
    global _shutdown_requested
    if not _shutdown_requested:
        print("\n\nShutdown requested. Finishing current operation and exiting cleanly...")
        _shutdown_requested = True
    else:
        print("\nForce shutdown requested. Exiting immediately.")
        sys.exit(1)

# Register signal handler for CTRL+C
signal.signal(signal.SIGINT, signal_handler)

# ============================================================================
# CONFIGURATION - Modify these variables as needed
# ============================================================================

# API Keys File: Path to JSON file containing Spotify API credentials
# The file should be in the same directory as this script by default
# Format of api_keys.json:
# [
#     {
#         "name": "SpotUSA1",
#         "client": "your_client_id",
#         "secret": "your_client_secret",
#         "rate_limited": false,
#         "available_after": 0
#     }
# ]
API_KEYS_FILE = "api_keys.json"

# Spotify Market: Set to country code (e.g., "US", "GR", "GB") or leave empty for GLOBAL
SPOTIFY_MARKET = "GR"

# Output Directory: Where to save the JSON files
OUTPUT_DIRECTORY = "./fetched_artist_discography/Artist_Info_115_GR_V2"

# API Configuration
API_RATE_LIMIT_BUFFER = 300  # seconds to add to rate limit wait time
OFFICIAL_API_REQUEST_DELAY = 1  # seconds to wait between API requests
TOKEN_EXPIRATION_BUFFER = 3300  # seconds (55 minutes) - refresh token before expiration
KEY_SWITCH_COOLDOWN = 2.0  # seconds to wait after switching API keys

# Spotify API batch limits
MAX_ALBUMS_PER_BATCH = 20
MAX_TRACKS_PER_BATCH = 50

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# ============================================================================
# SPOTIFY API CONSTANTS
# ============================================================================

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
ARTIST_URL_BASE = 'https://api.spotify.com/v1/artists'
ALBUM_URL_BASE = 'https://api.spotify.com/v1/albums'
TRACKS_URL_BASE = 'https://api.spotify.com/v1/tracks'

# JSON field names
ID = 'id'
NAME = 'name'
ITEMS = 'items'
ARTISTS = 'artists'
ALBUMS = 'albums'
TRACKS = 'tracks'
DISC_NUMBER = 'disc_number'

# ============================================================================
# TOKEN MANAGER
# ============================================================================

class TokenManager:
    """
    Manages Spotify API tokens with automatic rotation and rate limit handling.
    Based on Zotify's implementation.
    """
    
    def __init__(self, api_keys: List[Dict[str, Any]]):
        """
        Initialize the token manager
        
        Args:
            api_keys: List of API key dictionaries with 'name', 'client', and 'secret'
        """
        self._api_keys = []
        for key in api_keys:
            self._api_keys.append({
                "name": key.get("name", "Unknown"),
                "client": key["client"],
                "secret": key["secret"],
                "rate_limited": False,
                "available_after": 0
            })
        
        self._current_key_idx = 0
        self._token = None
        self._token_timestamp = 0
        
        if not self._api_keys:
            raise ValueError("No API keys provided")
        
        print(f"Token Manager initialized with {len(self._api_keys)} API key(s)")
    
    def _get_current_key(self) -> Optional[Dict[str, Any]]:
        """Get the current API key dictionary"""
        if not self._api_keys or not (0 <= self._current_key_idx < len(self._api_keys)):
            return None
        return self._api_keys[self._current_key_idx]
    
    def _clear_cached_token(self, old_key_idx: Optional[int] = None, new_key_idx: Optional[int] = None) -> None:
        """Clear the cached access token to force a refresh"""
        self._token = None
        self._token_timestamp = 0
    
    def _apply_key_switch_cooldown(self) -> None:
        """Apply cooldown delay after switching API keys"""
        if KEY_SWITCH_COOLDOWN > 0:
            time.sleep(KEY_SWITCH_COOLDOWN)
    
    def _find_available_key(self) -> bool:
        """
        Find an available (not rate limited) API key and switch to it
        
        Returns:
            bool: True if an available key was found, False otherwise
        """
        if not self._api_keys:
            return False
        
        current_time = time.time()
        old_key_idx = self._current_key_idx
        
        # First, look for keys that are immediately available
        for idx, key in enumerate(self._api_keys):
            if not key.get("rate_limited", False):
                if idx != self._current_key_idx:
                    self._current_key_idx = idx
                    self._clear_cached_token(old_key_idx, idx)
                    print(f"Switched to API key: {key['name']}")
                    self._apply_key_switch_cooldown()
                return True
            # Check if wait time has elapsed for rate limited keys
            elif key.get("available_after", 0) > 0 and current_time >= key["available_after"]:
                key["rate_limited"] = False
                key["available_after"] = 0
                if idx != self._current_key_idx:
                    self._current_key_idx = idx
                    self._clear_cached_token(old_key_idx, idx)
                    print(f"Key {key['name']} is now available again. Switched to it.")
                    self._apply_key_switch_cooldown()
                return True
        
        # If no keys are immediately available, find the one that will be available soonest
        min_wait_time = float('inf')
        next_key_idx = -1
        
        for idx, key in enumerate(self._api_keys):
            if key.get("rate_limited", False) and key.get("available_after", 0) > 0:
                if key["available_after"] < min_wait_time:
                    min_wait_time = key["available_after"]
                    next_key_idx = idx
        
        # If we found a key that will be available, wait for it
        if next_key_idx != -1:
            wait_seconds = min_wait_time - current_time
            next_key = self._api_keys[next_key_idx]
            available_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(min_wait_time))
            
            print(f"All API keys are rate limited. Waiting {wait_seconds:.1f} seconds until "
                  f"key '{next_key['name']}' becomes available at {available_time}.")
            
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            
            # Mark as available and switch to it
            next_key["rate_limited"] = False
            next_key["available_after"] = 0
            old_key_idx = self._current_key_idx
            self._current_key_idx = next_key_idx
            self._clear_cached_token(old_key_idx, next_key_idx)
            
            print(f"Continuing with key: {next_key['name']}")
            self._apply_key_switch_cooldown()
            return True
        
        return False
    
    def _mark_current_key_rate_limited(self, retry_after: Optional[int] = None) -> None:
        """
        Mark the current key as rate limited
        
        Args:
            retry_after: Retry-After header value in seconds (if provided by API)
        """
        current_key = self._get_current_key()
        if not current_key:
            return
        
        current_key["rate_limited"] = True
        current_time = time.time()
        
        if retry_after:
            available_after = current_time + retry_after + API_RATE_LIMIT_BUFFER
            available_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(available_after))
            print(f"API key '{current_key['name']}' rate limited. Server says retry after "
                  f"{retry_after}s. Adding {API_RATE_LIMIT_BUFFER}s buffer. "
                  f"Key will be available at {available_time}")
        else:
            default_wait = 3600 + API_RATE_LIMIT_BUFFER
            available_after = current_time + default_wait
            available_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(available_after))
            print(f"API key '{current_key['name']}' rate limited. No retry time specified. "
                  f"Setting key to be available after {default_wait}s (~{default_wait/3600:.1f} hours) at {available_time}")
        
        current_key["available_after"] = available_after
    
    def _get_spotify_token(self, client_id: str, client_secret: str) -> Optional[str]:
        """
        Get a new access token from Spotify API using client credentials flow
        
        Args:
            client_id: Spotify API client ID
            client_secret: Spotify API client secret
            
        Returns:
            Access token string if successful, None otherwise
        """
        if not client_id or not client_secret:
            print("Error: Invalid API credentials")
            return None
        
        auth_string = f"{client_id}:{client_secret}"
        auth_bytes = auth_string.encode("utf-8")
        auth_base64 = base64.b64encode(auth_bytes).decode("utf-8")
        
        headers = {
            "Authorization": f"Basic {auth_base64}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "grant_type": "client_credentials"
        }
        
        try:
            response = requests.post(
                SPOTIFY_TOKEN_URL,
                headers=headers,
                data=data,
                timeout=10
            )
            
            if response.status_code != 200:
                error_info = ""
                try:
                    error_data = response.json()
                    error_info = f", Error: {error_data.get('error', 'Unknown')}, " \
                                f"Description: {error_data.get('error_description', 'No details')}"
                except:
                    pass
                
                print(f"Authentication failed with status code {response.status_code}{error_info}")
                return None
            
            token_info = response.json()
            token = token_info.get("access_token")
            
            if token:
                self._token = token
                self._token_timestamp = time.time()
                current_key = self._get_current_key()
                if current_key:
                    print(f"Token obtained for API key '{current_key['name']}'")
            
            return token
            
        except requests.exceptions.RequestException as e:
            print(f"Network error during authentication: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error during authentication: {e}")
            return None
    
    def get_token(self) -> Optional[str]:
        """
        Get a valid access token, refreshing if necessary
        
        Returns:
            Valid access token string if available, None otherwise
        """
        if not self._api_keys:
            return None
        
        current_time = time.time()
        token_age = current_time - self._token_timestamp
        
        current_key = self._get_current_key()
        if not current_key:
            print("Error: No valid API key available")
            return None
        
        if self._token and token_age < TOKEN_EXPIRATION_BUFFER:
            return self._token
        
        # Token expired or doesn't exist, get a new one
        return self._get_spotify_token(current_key["client"], current_key["secret"])
    
    def make_request(
        self,
        url: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        _keys_tried_mask: Optional[List[bool]] = None,
        _skip_delay: bool = False
    ) -> Optional[requests.Response]:
        """
        Make an API request with automatic token refresh and key rotation
        
        Args:
            url: API endpoint URL
            method: HTTP method (GET, POST, etc.)
            params: Query parameters
            data: Request body data
            _keys_tried_mask: Internal tracking of attempted keys
            _skip_delay: Internal flag to skip API delay on recursive calls
            
        Returns:
            Response object if successful, None otherwise
        """
        if not self._api_keys:
            print("Error: No API keys loaded")
            return None
        
        # Initialize tracking mask on first call
        if _keys_tried_mask is None:
            _keys_tried_mask = [False] * len(self._api_keys)
        
        # Mark current key as tried
        _keys_tried_mask[self._current_key_idx] = True
        
        current_key = self._get_current_key()
        if not current_key:
            print("Error: Could not retrieve current key info")
            return None
        
        # Get or refresh token
        token = self.get_token()
        if not token:
            print(f"Failed to get token for key '{current_key['name']}'. Trying next key...")
            if self._find_available_key():
                if _keys_tried_mask[self._current_key_idx]:
                    print("Error: Cycled through all keys, all failed")
                    return None
                return self.make_request(url, method, params, data, _keys_tried_mask, _skip_delay=True)
            else:
                return None
        
        # Prepare headers
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Add configurable delay before API request (skip on recursive retry calls)
        if not _skip_delay and OFFICIAL_API_REQUEST_DELAY > 0:
            time.sleep(OFFICIAL_API_REQUEST_DELAY)
        
        # Try the request with retries
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                if method.upper() == "GET":
                    response = requests.get(url, headers=headers, params=params, timeout=10)
                elif method.upper() == "POST":
                    response = requests.post(url, headers=headers, params=params, json=data, timeout=10)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                # Handle 429 Rate Limited
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    try:
                        retry_after_seconds = int(retry_after) if retry_after else None
                    except ValueError:
                        retry_after_seconds = None
                    
                    print(f"Rate limited (429) on key '{current_key['name']}'")
                    self._mark_current_key_rate_limited(retry_after_seconds)
                    
                    # Try to switch to next available key
                    if self._find_available_key():
                        new_key = self._get_current_key()
                        if _keys_tried_mask[self._current_key_idx]:
                            print("Error: All API keys have been rate limited")
                            return None
                        # Retry with new key
                        return self.make_request(url, method, params, data, _keys_tried_mask, _skip_delay=True)
                    else:
                        print("Error: No available API keys remaining")
                        return None
                
                # Handle 401 Unauthorized (token expired or invalid)
                if response.status_code == 401:
                    print(f"Received 401 Unauthorized for key '{current_key['name']}'. Refreshing token...")
                    # Force token refresh
                    self._clear_cached_token()
                    token = self.get_token()
                    if not token:
                        print(f"Failed to refresh token for key '{current_key['name']}'. Trying next key...")
                        if self._find_available_key():
                            if _keys_tried_mask[self._current_key_idx]:
                                return None
                            return self.make_request(url, method, params, data, _keys_tried_mask, _skip_delay=True)
                        else:
                            return None
                    headers["Authorization"] = f"Bearer {token}"
                    continue  # Retry immediately with new token
                
                # Handle 403 Forbidden
                if response.status_code == 403:
                    print(f"Received 403 Forbidden for key '{current_key['name']}' on URL: {url}")
                    if self._find_available_key():
                        if _keys_tried_mask[self._current_key_idx]:
                            return None
                        return self.make_request(url, method, params, data, _keys_tried_mask, _skip_delay=True)
                    else:
                        return None
                
                # Handle 404 Not Found
                if response.status_code == 404:
                    print(f"Resource not found (404): {url}")
                    return None
                
                # Check for other HTTP errors
                response.raise_for_status()
                
                # Success!
                return response
                
            except requests.exceptions.HTTPError as e:
                print(f"HTTP error {e.response.status_code} on key '{current_key['name']}' for {url}: {e}")
                retry_count += 1
                
            except requests.exceptions.RequestException as e:
                print(f"Request exception on key '{current_key['name']}' for {url}: {e}")
                retry_count += 1
            
            except Exception as e:
                print(f"Unexpected error during request: {e}")
                retry_count += 1
            
            # If we've exhausted retries for this key, try the next one
            if retry_count >= MAX_RETRIES:
                print(f"Max retries reached for key '{current_key['name']}'. Trying next key...")
                if self._find_available_key():
                    if _keys_tried_mask[self._current_key_idx]:
                        print("Error: All keys have been tried")
                        return None
                    return self.make_request(url, method, params, data, _keys_tried_mask, _skip_delay=True)
                else:
                    return None
            
            # Wait before retrying with same key
            delay = RETRY_DELAY + random.uniform(0.1, 1.0)
            time.sleep(delay)
        
        return None

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def load_api_keys_from_file(keys_file: str) -> List[Dict[str, Any]]:
    """
    Load API keys from a JSON file
    
    Args:
        keys_file: Path to the JSON file containing API keys
        
    Returns:
        List of API key dictionaries
        
    Raises:
        FileNotFoundError: If the file doesn't exist
        json.JSONDecodeError: If the file is not valid JSON
        ValueError: If the file format is incorrect
    """
    # Get the directory where the script is located
    script_dir = Path(__file__).parent.resolve()
    keys_path = script_dir / keys_file
    
    # Check if file exists
    if not keys_path.is_file():
        raise FileNotFoundError(f"API keys file not found: {keys_path}")
    
    # Load and parse JSON
    try:
        with open(keys_path, 'r', encoding='utf-8') as f:
            keys_data = json.load(f)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON in {keys_path}: {e.msg}", e.doc, e.pos)
    
    # Validate format
    if not isinstance(keys_data, list):
        raise ValueError(f"API keys file must contain a JSON array, got {type(keys_data).__name__}")
    
    if not keys_data:
        raise ValueError("API keys file is empty")
    
    # Validate each key entry
    valid_keys = []
    for idx, key_entry in enumerate(keys_data):
        if not isinstance(key_entry, dict):
            print(f"Warning: Entry {idx+1} in API keys file is not a dictionary, skipping")
            continue
        
        name = key_entry.get("name", f"Key {idx+1}")
        client = key_entry.get("client")
        secret = key_entry.get("secret")
        
        if not client or not isinstance(client, str):
            print(f"Warning: Entry {idx+1} (Name: {name}) is missing or has invalid 'client' ID, skipping")
            continue
        
        if not secret or not isinstance(secret, str):
            print(f"Warning: Entry {idx+1} (Name: {name}) is missing or has invalid 'secret', skipping")
            continue
        
        # Add the key (only include name, client, secret - ignore rate_limited and available_after from file)
        valid_keys.append({
            "name": name,
            "client": client,
            "secret": secret
        })
    
    if not valid_keys:
        raise ValueError("No valid API keys found in file")
    
    return valid_keys

def parse_artist_url(url: str) -> Optional[str]:
    """
    Extract artist ID from Spotify URL
    
    Args:
        url: Spotify artist URL
        
    Returns:
        Artist ID if valid, None otherwise
    """
    # Match patterns like:
    # https://open.spotify.com/artist/6vbY3hOaCAhC7VjucswgdS
    # spotify:artist:6vbY3hOaCAhC7VjucswgdS
    patterns = [
        r'artist[/:]([a-zA-Z0-9]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

def build_json_filename(artist_id: str) -> str:
    """
    Build the JSON filename based on artist ID, market configuration, and timestamp
    
    Args:
        artist_id: Spotify artist ID
        
    Returns:
        JSON filename with unix timestamp (e.g., "6vbY3hOaCAhC7VjucswgdS-GR-1704408973.json")
    """
    unix_timestamp = int(time.time())
    
    if SPOTIFY_MARKET:
        return f"{artist_id}-{SPOTIFY_MARKET}-{unix_timestamp}.json"
    else:
        return f"{artist_id}-GLOBAL-{unix_timestamp}.json"

# ============================================================================
# SPOTIFY API FUNCTIONS
# ============================================================================

def get_artist_profile(token_manager: TokenManager, artist_id: str) -> Optional[Dict[str, Any]]:
    """
    Get standard artist profile information (Get Artist endpoint)
    Includes images, genres, popularity, followers, etc.
    
    Args:
        token_manager: TokenManager instance
        artist_id: Spotify artist ID
        
    Returns:
        Dict with artist profile data, or None if failed
    """
    # Use the base artist URL (which maps to /v1/artists) and append the ID
    url = f'{ARTIST_URL_BASE}/{artist_id}'
    
    response = token_manager.make_request(url)
    
    if not response:
        print(f"Warning: Failed to fetch artist profile info for {artist_id}")
        return None
        
    return response.json()

def get_artist_albums(token_manager: TokenManager, artist_id: str) -> List[str]:
    """
    Get all album IDs for an artist
    
    Args:
        token_manager: TokenManager instance
        artist_id: Spotify artist ID
        
    Returns:
        List of album IDs
    """
    params = {'include_groups': 'album,single'}
    
    # Add market parameter if configured
    if SPOTIFY_MARKET:
        params['market'] = SPOTIFY_MARKET
    
    response = token_manager.make_request(
        f'{ARTIST_URL_BASE}/{artist_id}/albums',
        params=params
    )
    
    if not response:
        raise RuntimeError("Failed to fetch artist albums")
    
    resp = response.json()
    album_ids = [item[ID] for item in resp[ITEMS]]
    
    # Handle pagination
    while resp.get('next'):
        response = token_manager.make_request(resp['next'])
        if not response:
            break
        resp = response.json()
        album_ids.extend([item[ID] for item in resp[ITEMS]])
    
    return album_ids

def get_several_albums(token_manager: TokenManager, album_ids: List[str]) -> Dict[str, Any]:
    """
    Fetch metadata for multiple albums in a single API call
    Spotify API supports up to 20 albums per request
    
    Args:
        token_manager: TokenManager instance
        album_ids: List of album IDs (up to 20)
        
    Returns:
        dict: Mapping of album_id -> album_data
    """
    if not album_ids:
        return {}
    
    if len(album_ids) > MAX_ALBUMS_PER_BATCH:
        raise ValueError(f"get_several_albums supports maximum {MAX_ALBUMS_PER_BATCH} albums per call, but {len(album_ids)} were provided")
    
    ids_param = ','.join(album_ids)
    params = {'ids': ids_param}
    
    # Add market parameter if configured
    if SPOTIFY_MARKET:
        params['market'] = SPOTIFY_MARKET
    
    response = token_manager.make_request(f'{ALBUM_URL_BASE}', params=params)
    
    if not response:
        print(f"Failed to fetch albums")
        return {}
    
    resp = response.json()
    
    # Build a dictionary for easy lookup
    album_dict = {}
    if ALBUMS in resp:
        for album in resp[ALBUMS]:
            if album is not None:  # Spotify returns None for invalid/unavailable albums
                album_dict[album[ID]] = album
    
    return album_dict

def get_several_tracks_raw(token_manager: TokenManager, track_ids: List[str]) -> Dict[str, Any]:
    """
    Fetch raw metadata for multiple tracks in a single API call (for JSON storage)
    Spotify API supports up to 50 tracks per request
    
    Args:
        token_manager: TokenManager instance
        track_ids: List of track IDs (up to 50)
        
    Returns:
        dict: Mapping of track_id -> raw track data (dict)
    """
    if not track_ids:
        return {}
    
    if len(track_ids) > MAX_TRACKS_PER_BATCH:
        raise ValueError(f"get_several_tracks_raw supports maximum {MAX_TRACKS_PER_BATCH} tracks per call, but {len(track_ids)} were provided")
    
    ids_param = ','.join(track_ids)
    params = {'ids': ids_param}
    
    # Add market parameter if configured
    if SPOTIFY_MARKET:
        params['market'] = SPOTIFY_MARKET
    
    response = token_manager.make_request(TRACKS_URL_BASE, params=params)
    
    if not response:
        print(f"Failed to fetch tracks for JSON storage")
        return {}
    
    info = response.json()
    
    if TRACKS not in info:
        print(f"Failed to fetch tracks for JSON storage")
        return {}
    
    # Build a dictionary for easy lookup
    track_dict = {}
    for track in info[TRACKS]:
        if track is not None:  # Spotify returns None for invalid/unavailable tracks
            track_dict[track[ID]] = track
    
    return track_dict

def save_artist_json(artist_id: str, album_metadata: dict, track_metadata: dict, album_ids: list, artist_profile: Optional[dict] = None) -> None:
    """
    Save all fetched artist data to a JSON file (matching Zotify format)
    
    Args:
        artist_id: Spotify artist ID
        album_metadata: Dictionary mapping album_id -> album data
        track_metadata: Dictionary mapping track_id -> track data
        album_ids: List of album IDs for the artist
        artist_profile: Optional dictionary containing official artist info (Get Artist endpoint)
    """
    # Create the directory if it doesn't exist
    json_dir = Path(OUTPUT_DIRECTORY).expanduser()
    json_dir.mkdir(parents=True, exist_ok=True)
    
    # Build the filename
    filename = build_json_filename(artist_id)
    file_path = json_dir / filename
    
    # Check if file exists
    file_exists = file_path.exists()
    
    # Prepare the JSON data structure
    json_data = {
        "artist_id": artist_id,
        "market": SPOTIFY_MARKET or "GLOBAL",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "official_getartist": artist_profile or {},  # Add the new section here
        "album_ids": album_ids,
        "albums": album_metadata,
        "tracks": track_metadata
    }
    
    # Write JSON file
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        if file_exists:
            print(f"Artist data updated in {file_path}")
        else:
            print(f"Artist data saved to {file_path}")
    except (OSError, IOError) as e:
        print(f"Failed to save JSON file: {e}")

# ============================================================================
# MAIN PROCESSING FUNCTION
# ============================================================================

def process_artist(token_manager: TokenManager, artist_url: str) -> None:
    """
    Process a single artist URL and download all information
    
    Args:
        token_manager: TokenManager instance
        artist_url: Spotify artist URL
    """
    global _shutdown_requested
    
    if _shutdown_requested:
        print("Skipping artist due to shutdown request")
        return
    
    artist_id = parse_artist_url(artist_url)
    
    if not artist_id:
        print(f"Error: Invalid Artist URL: {artist_url}")
        return
    
    print(f"\n{'='*60}")
    print(f"Processing Artist ID: {artist_id}")
    print(f"Market: {SPOTIFY_MARKET or 'GLOBAL'}")
    print(f"{'='*60}\n")
    
    try:
        # PHASE 0: Get Artist Profile (New)
        print("Phase 0: Fetching artist profile information...")
        artist_profile = get_artist_profile(token_manager, artist_id)
        if artist_profile:
            print(f"  Fetched profile for: {artist_profile.get('name', 'Unknown')}")
        else:
            print("  Warning: Could not fetch artist profile (or unavailable). Continuing...")
            artist_profile = {}
            
        print("")

        # PHASE 1: Get all album IDs
        print("Phase 1: Fetching album list...")
        albums = get_artist_albums(token_manager, artist_id)
        total_albums = len(albums)
        
        if not albums:
            print(f"No albums found for artist {artist_id}")
            return
        
        print(f"Found {total_albums} album(s) for artist ID {artist_id}\n")
        
        # PHASE 2: Batch fetch album metadata
        print(f"Phase 2: Fetching metadata for {total_albums} album(s) in batches...")
        album_metadata = {}
        
        for batch_start in range(0, total_albums, MAX_ALBUMS_PER_BATCH):
            batch_end = min(batch_start + MAX_ALBUMS_PER_BATCH, total_albums)
            batch_albums = albums[batch_start:batch_end]
            
            print(f"  Fetching album metadata batch {batch_start//MAX_ALBUMS_PER_BATCH + 1}/"
                  f"{(total_albums + MAX_ALBUMS_PER_BATCH - 1)//MAX_ALBUMS_PER_BATCH} "
                  f"({len(batch_albums)} albums)")
            
            batch_metadata = get_several_albums(token_manager, batch_albums)
            album_metadata.update(batch_metadata)
        
        print(f"Successfully fetched metadata for {len(album_metadata)} album(s)\n")
        
        # PHASE 3: Collect all track IDs and batch fetch track metadata
        print(f"Phase 3: Collecting track IDs from all albums...")
        all_track_ids = []
        
        for album_id in albums:
            album_data = album_metadata.get(album_id)
            if album_data is None:
                continue
            
            # Get track IDs from album metadata
            if TRACKS in album_data and ITEMS in album_data[TRACKS]:
                track_ids = [track[ID] for track in album_data[TRACKS][ITEMS] 
                           if track and ID in track]
                
                # Check if there are more tracks to fetch (pagination for albums with > 50 tracks)
                tracks_data = album_data[TRACKS]
                while tracks_data.get('next'):
                    print(f"  Fetching additional tracks for album {album_id} (track count: {len(track_ids)}+)")
                    response = token_manager.make_request(tracks_data['next'])
                    if not response:
                        break
                    tracks_data = response.json()
                    if ITEMS in tracks_data:
                        additional_track_ids = [track[ID] for track in tracks_data[ITEMS] 
                                              if track and ID in track]
                        track_ids.extend(additional_track_ids)
                
                all_track_ids.extend(track_ids)
        
        total_tracks = len(all_track_ids)
        unique_tracks = len(set(all_track_ids))
        print(f"Found {total_tracks} track(s) across all albums ({unique_tracks} unique)\n")
        
        # Batch fetch ALL track metadata (50 tracks per call)
        track_metadata_raw = {}
        
        if total_tracks > 0:
            print(f"Phase 4: Fetching metadata for {total_tracks} track(s) in batches...")
            
            # Get unique track IDs for fetching to avoid duplicate API calls
            unique_track_ids = list(dict.fromkeys(all_track_ids))  # Preserves order, removes duplicates
            
            for batch_start in range(0, len(unique_track_ids), MAX_TRACKS_PER_BATCH):
                batch_end = min(batch_start + MAX_TRACKS_PER_BATCH, len(unique_track_ids))
                batch_tracks = unique_track_ids[batch_start:batch_end]
                
                print(f"  Fetching track metadata batch {batch_start//MAX_TRACKS_PER_BATCH + 1}/"
                      f"{(len(unique_track_ids) + MAX_TRACKS_PER_BATCH - 1)//MAX_TRACKS_PER_BATCH} "
                      f"({len(batch_tracks)} tracks)")
                
                batch_track_metadata_raw = get_several_tracks_raw(token_manager, batch_tracks)
                track_metadata_raw.update(batch_track_metadata_raw)
            
            # Report results
            fetched_count = len(track_metadata_raw)
            unavailable_count = len(unique_track_ids) - fetched_count
            
            if unavailable_count > 0:
                fetched_track_ids = set(track_metadata_raw.keys())
                requested_track_ids = set(unique_track_ids)
                missing_track_ids = requested_track_ids - fetched_track_ids
                
                print(f"Successfully fetched metadata for {fetched_count}/{len(unique_track_ids)} track(s) "
                      f"({unavailable_count} unavailable)")
                print(f"Unavailable track IDs (region-locked or removed): {', '.join(sorted(missing_track_ids))}\n")
            else:
                print(f"Successfully fetched metadata for {fetched_count} track(s)")
                print(f"Unavailable track IDs (region-locked or removed): None\n")
        
        # PHASE 5: Save all fetched data to JSON
        print("Phase 5: Saving data to JSON file...")
        save_artist_json(artist_id, album_metadata, track_metadata_raw, albums, artist_profile)
        
        print(f"\n{'='*60}")
        print(f"Successfully processed artist {artist_id}")
        print(f"{'='*60}\n")
        
    except Exception as e:
        print(f"Error processing artist {artist_id}: {e}")
        import traceback
        traceback.print_exc()

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point"""
    global _shutdown_requested
    
    if len(sys.argv) < 2:
        print("Error: No Spotify artist URL or file provided")
        print("Usage: python download_artist_info.py <artist_url_or_txt_file>")
        print("\nExamples:")
        print("  Single artist:")
        print("    python download_artist_info.py https://open.spotify.com/artist/6vbY3hOaCAhC7VjucswgdS")
        print("\n  Multiple artists from file:")
        print("    python download_artist_info.py artists.txt")
        print("\n  Text file format (one artist link per line, # for comments):")
        print("    # This is a comment")
        print("    https://open.spotify.com/artist/6vbY3hOaCAhC7VjucswgdS")
        print("    https://open.spotify.com/artist/1234567890abcdef")
        sys.exit(1)
    
    # Load API keys from file
    try:
        api_keys = load_api_keys_from_file(API_KEYS_FILE)
        print(f"Loaded {len(api_keys)} API key(s) from {API_KEYS_FILE}")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print(f"Please create an {API_KEYS_FILE} file in the same directory as this script")
        print("Example format:")
        print('[')
        print('    {')
        print('        "name": "SpotUSA1",')
        print('        "client": "your_client_id",')
        print('        "secret": "your_client_secret",')
        print('        "rate_limited": false,')
        print('        "available_after": 0')
        print('    }')
        print(']')
        sys.exit(1)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error loading API keys: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error loading API keys: {e}")
        sys.exit(1)
    
    # Initialize token manager
    try:
        token_manager = TokenManager(api_keys)
    except Exception as e:
        print(f"Error initializing token manager: {e}")
        sys.exit(1)
    
    # Process input (either URLs or txt file)
    input_arg = sys.argv[1]
    
    # Check if input is a file
    if os.path.isfile(input_arg):
        # Read artist links from file
        try:
            print(f"Reading artist links from file: {input_arg}\n")
            
            # Read file and track all lines for progress reporting
            with open(input_arg, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
            
            total_lines = len(all_lines)
            artist_links = []
            
            # Process each line
            for line_num, line in enumerate(all_lines, start=1):
                original_line = line
                line = line.strip()
                
                # Handle empty lines
                if not line:
                    continue
                
                # Handle comments
                if line.startswith('#'):
                    print(f"Skipping Line {line_num}/{total_lines} (comment): {original_line.rstrip()}")
                    continue
                
                # Valid artist link
                artist_links.append(line)
            
            if not artist_links:
                print("Error: No artist links found in file (all lines are empty or comments)")
                sys.exit(1)
            
            print(f"\nFound {len(artist_links)} artist link(s) to process\n")
            
            # Process each artist
            processed_count = 0
            for idx, url in enumerate(artist_links, start=1):
                if _shutdown_requested:
                    print(f"\nShutdown requested. Processed {processed_count}/{len(artist_links)} artists.")
                    break
                
                print(f"Processing artist {idx}/{len(artist_links)}")
                process_artist(token_manager, url)
                processed_count += 1
                
            if not _shutdown_requested:
                print(f"\nCompleted processing {len(artist_links)} artist(s) from file")
            
        except FileNotFoundError:
            print(f"Error: File not found: {input_arg}")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading file: {e}")
            sys.exit(1)
    else:
        # Process as direct URL(s)
        artist_urls = sys.argv[1:]
        for url in artist_urls:
            if _shutdown_requested:
                print("\nShutdown requested. Exiting.")
                break
            process_artist(token_manager, url)

if __name__ == "__main__":
    main()
