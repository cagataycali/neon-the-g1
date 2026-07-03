"""🎵 use_spotify - Full Spotify control for Neon via Spotify Web API (spotipy).

Like use_aws wraps AWS and use_mac wraps macOS, this wraps the entire Spotify ecosystem:
playback control, search, playlists, library, queue, devices, artist/album browsing,
recommendations, and user profile.

Requires:
    pip install spotipy
    Environment variables:
        SPOTIFY_CLIENT_ID: Your Spotify app client ID
        SPOTIFY_CLIENT_SECRET: Your Spotify app client secret
        SPOTIFY_REDIRECT_URI: Redirect URI (default: http://127.0.0.1:8888/callback)

First run will open browser for OAuth authorization.
Token is cached in ~/.cache/spotipy for subsequent runs.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from strands import tool

logger = logging.getLogger(__name__)

# Required scopes for full control
_SCOPES = (
    "user-read-playback-state "
    "user-modify-playback-state "
    "user-read-currently-playing "
    "user-read-recently-played "
    "user-top-read "
    "user-library-read "
    "user-library-modify "
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-public "
    "playlist-modify-private "
    "user-follow-read "
    "user-follow-modify "
    "user-read-private "
    "user-read-email "
)

# Singleton client
_sp_client = None


def _get_client():
    """Get or create authenticated Spotify client."""
    global _sp_client
    if _sp_client is not None:
        return _sp_client

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        raise RuntimeError("spotipy not installed. Run: pip install spotipy")

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "https://localhost:8888/callback")

    if not client_id or not client_secret:
        raise RuntimeError(
            "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables.\n"
            "Create an app at https://developer.spotify.com/dashboard"
        )

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=_SCOPES,
        cache_path=os.path.expanduser("~/.cache/spotipy_neon_token"),
        open_browser=True,
    )

    _sp_client = spotipy.Spotify(auth_manager=auth_manager)
    return _sp_client


def _ok(text: str) -> Dict[str, Any]:
    return {"status": "success", "content": [{"text": text}]}


def _err(text: str) -> Dict[str, Any]:
    return {"status": "error", "content": [{"text": text}]}


def _format_track(track: dict, idx: int = None) -> str:
    """Format a track dict into a readable string."""
    name = track.get("name", "Unknown")
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    album = track.get("album", {}).get("name", "")
    duration_ms = track.get("duration_ms", 0)
    duration = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"
    uri = track.get("uri", "")
    prefix = f"{idx}. " if idx is not None else "• "
    album_part = f" — {album}" if album else ""
    return (
        f"  {prefix}**{name}** by {artists}{album_part} [{duration}]\n    URI: `{uri}`"
    )


def _format_artist(artist: dict, idx: int = None) -> str:
    name = artist.get("name", "Unknown")
    genres = ", ".join(artist.get("genres", [])[:3])
    followers = artist.get("followers", {}).get("total", 0)
    uri = artist.get("uri", "")
    prefix = f"{idx}. " if idx is not None else "• "
    genre_part = f" | {genres}" if genres else ""
    return (
        f"  {prefix}**{name}** ({followers:,} followers{genre_part})\n    URI: `{uri}`"
    )


def _format_album(album: dict, idx: int = None) -> str:
    name = album.get("name", "Unknown")
    artists = ", ".join(a["name"] for a in album.get("artists", []))
    year = album.get("release_date", "")[:4]
    total = album.get("total_tracks", 0)
    uri = album.get("uri", "")
    prefix = f"{idx}. " if idx is not None else "• "
    return (
        f"  {prefix}**{name}** by {artists} ({year}, {total} tracks)\n    URI: `{uri}`"
    )


def _format_playlist(pl: dict, idx: int = None) -> str:
    name = pl.get("name", "Unknown")
    owner = pl.get("owner", {}).get("display_name", "Unknown")
    total = pl.get("tracks", {}).get("total", 0)
    uri = pl.get("uri", "")
    prefix = f"{idx}. " if idx is not None else "• "
    return f"  {prefix}**{name}** by {owner} ({total} tracks)\n    URI: `{uri}`"


# =============================================================================
# Playback
# =============================================================================


def _now_playing() -> Dict:
    sp = _get_client()
    current = sp.current_playback()
    if not current or not current.get("item"):
        return _ok("⏸ Nothing currently playing.")

    track = current["item"]
    name = track["name"]
    artists = ", ".join(a["name"] for a in track["artists"])
    album = track["album"]["name"]
    progress_ms = current.get("progress_ms", 0)
    duration_ms = track.get("duration_ms", 0)
    progress = f"{progress_ms // 60000}:{(progress_ms % 60000) // 1000:02d}"
    duration = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"
    is_playing = current.get("is_playing", False)
    device = current.get("device", {}).get("name", "Unknown")
    shuffle = "ON" if current.get("shuffle_state") else "OFF"
    repeat = current.get("repeat_state", "off")
    volume = current.get("device", {}).get("volume_percent", "?")

    icon = "🎵" if is_playing else "⏸"
    return _ok(
        f"{icon} **{name}** by {artists}\n"
        f"  Album: {album}\n"
        f"  Progress: {progress} / {duration}\n"
        f"  Device: {device} | Volume: {volume}%\n"
        f"  Shuffle: {shuffle} | Repeat: {repeat}\n"
        f"  URI: `{track['uri']}`"
    )


def _play(
    uri: str = None, device_id: str = None, context_uri: str = None, offset: int = None
) -> Dict:
    sp = _get_client()
    kwargs = {}
    if device_id:
        kwargs["device_id"] = device_id

    if uri:
        # Single track or list of tracks
        uris = [u.strip() for u in uri.split(",")]
        kwargs["uris"] = uris
        if offset is not None:
            kwargs["offset"] = {"position": offset}
    elif context_uri:
        # Album, playlist, or artist context
        kwargs["context_uri"] = context_uri
        if offset is not None:
            kwargs["offset"] = {"position": offset}

    sp.start_playback(**kwargs)
    return _ok("▶️ Playing.")


def _pause() -> Dict:
    sp = _get_client()
    sp.pause_playback()
    return _ok("⏸ Paused.")


def _next_track() -> Dict:
    sp = _get_client()
    sp.next_track()
    return _ok("⏭ Next track.")


def _previous_track() -> Dict:
    sp = _get_client()
    sp.previous_track()
    return _ok("⏮ Previous track.")


def _seek(position_ms: int) -> Dict:
    sp = _get_client()
    sp.seek_track(position_ms)
    mins = position_ms // 60000
    secs = (position_ms % 60000) // 1000
    return _ok(f"⏩ Seeked to {mins}:{secs:02d}.")


def _set_volume(volume: int, device_id: str = None) -> Dict:
    sp = _get_client()
    sp.volume(volume, device_id=device_id)
    return _ok(f"🔊 Volume set to {volume}%.")


def _shuffle(state: bool) -> Dict:
    sp = _get_client()
    sp.shuffle(state)
    return _ok(f"🔀 Shuffle {'ON' if state else 'OFF'}.")


def _repeat(state: str) -> Dict:
    """state: 'track', 'context', or 'off'"""
    sp = _get_client()
    sp.repeat(state)
    return _ok(f"🔁 Repeat: {state}.")


def _transfer_playback(device_id: str) -> Dict:
    sp = _get_client()
    sp.transfer_playback(device_id, force_play=True)
    return _ok(f"📱 Playback transferred to device `{device_id}`.")


# =============================================================================
# Queue
# =============================================================================


def _add_to_queue(uri: str, device_id: str = None) -> Dict:
    sp = _get_client()
    uris = [u.strip() for u in uri.split(",")]
    for u in uris:
        sp.add_to_queue(u, device_id=device_id)
    return _ok(f"➕ Added {len(uris)} track(s) to queue.")


def _get_queue() -> Dict:
    sp = _get_client()
    queue = sp.queue()
    current = queue.get("currently_playing")
    upcoming = queue.get("queue", [])[:20]

    lines = []
    if current:
        lines.append("**Now Playing:**")
        lines.append(_format_track(current))
    lines.append(f"\n**Queue** ({len(upcoming)} upcoming):")
    for i, track in enumerate(upcoming, 1):
        lines.append(_format_track(track, idx=i))

    return _ok("\n".join(lines))


# =============================================================================
# Search
# =============================================================================


def _search(query: str, search_type: str = "track", limit: int = 10) -> Dict:
    sp = _get_client()
    results = sp.search(q=query, type=search_type, limit=limit)

    lines = [f"🔍 Search results for '{query}' ({search_type}):\n"]

    if search_type == "track":
        tracks = results.get("tracks", {}).get("items", [])
        for i, t in enumerate(tracks, 1):
            lines.append(_format_track(t, idx=i))
    elif search_type == "artist":
        artists = results.get("artists", {}).get("items", [])
        for i, a in enumerate(artists, 1):
            lines.append(_format_artist(a, idx=i))
    elif search_type == "album":
        albums = results.get("albums", {}).get("items", [])
        for i, a in enumerate(albums, 1):
            lines.append(_format_album(a, idx=i))
    elif search_type == "playlist":
        playlists = results.get("playlists", {}).get("items", [])
        for i, p in enumerate(playlists, 1):
            lines.append(_format_playlist(p, idx=i))

    if len(lines) == 1:
        return _ok(f"No results for '{query}'.")
    return _ok("\n".join(lines))


# =============================================================================
# Devices
# =============================================================================


def _list_devices() -> Dict:
    sp = _get_client()
    devices = sp.devices().get("devices", [])
    if not devices:
        return _ok("No active devices found. Open Spotify on a device.")

    lines = [f"📱 **{len(devices)} device(s)**:\n"]
    for d in devices:
        active = " 🟢 ACTIVE" if d.get("is_active") else ""
        vol = d.get("volume_percent", "?")
        lines.append(
            f"  • **{d['name']}** ({d['type']}){active}\n"
            f"    ID: `{d['id']}` | Volume: {vol}%"
        )
    return _ok("\n".join(lines))


# =============================================================================
# Playlists
# =============================================================================


def _my_playlists(limit: int = 20) -> Dict:
    sp = _get_client()
    results = sp.current_user_playlists(limit=limit)
    playlists = results.get("items", [])
    if not playlists:
        return _ok("No playlists found.")

    lines = [f"📋 **{len(playlists)} playlists**:\n"]
    for i, pl in enumerate(playlists, 1):
        lines.append(_format_playlist(pl, idx=i))
    return _ok("\n".join(lines))


def _playlist_tracks(playlist_id: str, limit: int = 50) -> Dict:
    sp = _get_client()
    results = sp.playlist_tracks(playlist_id, limit=limit)
    tracks = results.get("items", [])

    lines = [f"📋 **Playlist tracks** ({len(tracks)}):\n"]
    for i, item in enumerate(tracks, 1):
        t = item.get("track")
        if t:
            lines.append(_format_track(t, idx=i))
    return _ok("\n".join(lines))


def _create_playlist(name: str, description: str = "", public: bool = False) -> Dict:
    sp = _get_client()
    user = sp.current_user()["id"]
    pl = sp.user_playlist_create(user, name, public=public, description=description)
    return _ok(
        f"✅ Playlist **{name}** created!\n"
        f"  URI: `{pl['uri']}`\n"
        f"  URL: {pl['external_urls'].get('spotify', '')}"
    )


def _add_to_playlist(playlist_id: str, uris: str) -> Dict:
    sp = _get_client()
    uri_list = [u.strip() for u in uris.split(",")]
    sp.playlist_add_items(playlist_id, uri_list)
    return _ok(f"➕ Added {len(uri_list)} track(s) to playlist.")


def _remove_from_playlist(playlist_id: str, uris: str) -> Dict:
    sp = _get_client()
    uri_list = [u.strip() for u in uris.split(",")]
    sp.playlist_remove_all_occurrences_of_items(playlist_id, uri_list)
    return _ok(f"➖ Removed {len(uri_list)} track(s) from playlist.")


# =============================================================================
# Library (Liked Songs)
# =============================================================================


def _liked_songs(limit: int = 20) -> Dict:
    sp = _get_client()
    results = sp.current_user_saved_tracks(limit=limit)
    tracks = results.get("items", [])

    lines = [f"❤️ **Liked Songs** ({len(tracks)}):\n"]
    for i, item in enumerate(tracks, 1):
        t = item.get("track")
        if t:
            lines.append(_format_track(t, idx=i))
    return _ok("\n".join(lines))


def _save_tracks(uris: str) -> Dict:
    sp = _get_client()
    # Extract track IDs from URIs
    ids = []
    for u in uris.split(","):
        u = u.strip()
        if ":" in u:
            ids.append(u.split(":")[-1])
        else:
            ids.append(u)
    sp.current_user_saved_tracks_add(ids)
    return _ok(f"❤️ Saved {len(ids)} track(s) to library.")


def _remove_saved_tracks(uris: str) -> Dict:
    sp = _get_client()
    ids = []
    for u in uris.split(","):
        u = u.strip()
        if ":" in u:
            ids.append(u.split(":")[-1])
        else:
            ids.append(u)
    sp.current_user_saved_tracks_delete(ids)
    return _ok(f"💔 Removed {len(ids)} track(s) from library.")


# =============================================================================
# Browse / Discovery
# =============================================================================


def _recently_played(limit: int = 20) -> Dict:
    sp = _get_client()
    results = sp.current_user_recently_played(limit=limit)
    tracks = results.get("items", [])

    lines = [f"🕐 **Recently Played** ({len(tracks)}):\n"]
    for i, item in enumerate(tracks, 1):
        t = item.get("track")
        if t:
            lines.append(_format_track(t, idx=i))
    return _ok("\n".join(lines))


def _top_tracks(time_range: str = "medium_term", limit: int = 20) -> Dict:
    """time_range: short_term (4 weeks), medium_term (6 months), long_term (years)"""
    sp = _get_client()
    results = sp.current_user_top_tracks(time_range=time_range, limit=limit)
    tracks = results.get("items", [])

    range_labels = {
        "short_term": "last 4 weeks",
        "medium_term": "last 6 months",
        "long_term": "all time",
    }
    label = range_labels.get(time_range, time_range)
    lines = [f"🏆 **Top Tracks** ({label}, {len(tracks)}):\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(_format_track(t, idx=i))
    return _ok("\n".join(lines))


def _top_artists(time_range: str = "medium_term", limit: int = 20) -> Dict:
    sp = _get_client()
    results = sp.current_user_top_artists(time_range=time_range, limit=limit)
    artists = results.get("items", [])

    range_labels = {
        "short_term": "last 4 weeks",
        "medium_term": "last 6 months",
        "long_term": "all time",
    }
    label = range_labels.get(time_range, time_range)
    lines = [f"🏆 **Top Artists** ({label}, {len(artists)}):\n"]
    for i, a in enumerate(artists, 1):
        lines.append(_format_artist(a, idx=i))
    return _ok("\n".join(lines))


def _recommendations(
    seed_tracks: str = None,
    seed_artists: str = None,
    seed_genres: str = None,
    limit: int = 20,
) -> Dict:
    sp = _get_client()
    kwargs = {"limit": limit}

    if seed_tracks:
        ids = [
            u.strip().split(":")[-1] if ":" in u.strip() else u.strip()
            for u in seed_tracks.split(",")
        ]
        kwargs["seed_tracks"] = ids[:5]
    if seed_artists:
        ids = [
            u.strip().split(":")[-1] if ":" in u.strip() else u.strip()
            for u in seed_artists.split(",")
        ]
        kwargs["seed_artists"] = ids[:5]
    if seed_genres:
        kwargs["seed_genres"] = [g.strip() for g in seed_genres.split(",")][:5]

    if not any(k.startswith("seed_") for k in kwargs):
        return _err(
            "At least one seed (seed_tracks, seed_artists, or seed_genres) required."
        )

    results = sp.recommendations(**kwargs)
    tracks = results.get("tracks", [])

    lines = [f"✨ **Recommendations** ({len(tracks)}):\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(_format_track(t, idx=i))
    return _ok("\n".join(lines))


def _available_genres() -> Dict:
    sp = _get_client()
    genres = sp.recommendation_genre_seeds().get("genres", [])
    return _ok(f"🎸 **{len(genres)} available genres**:\n" + ", ".join(genres))


# =============================================================================
# Artist / Album details
# =============================================================================


def _artist_info(artist_id: str) -> Dict:
    sp = _get_client()
    # Extract ID from URI
    if ":" in artist_id:
        artist_id = artist_id.split(":")[-1]
    artist = sp.artist(artist_id)
    top_tracks = sp.artist_top_tracks(artist_id).get("tracks", [])[:5]
    albums = sp.artist_albums(artist_id, limit=10).get("items", [])

    lines = [
        f"🎤 **{artist['name']}**",
        f"  Genres: {', '.join(artist.get('genres', []))}",
        f"  Followers: {artist.get('followers', {}).get('total', 0):,}",
        f"  Popularity: {artist.get('popularity', 0)}/100",
        f"  URI: `{artist['uri']}`",
        f"\n  **Top Tracks:**",
    ]
    for i, t in enumerate(top_tracks, 1):
        lines.append(_format_track(t, idx=i))

    lines.append(f"\n  **Albums** ({len(albums)}):")
    for i, a in enumerate(albums, 1):
        lines.append(_format_album(a, idx=i))

    return _ok("\n".join(lines))


def _album_info(album_id: str) -> Dict:
    sp = _get_client()
    if ":" in album_id:
        album_id = album_id.split(":")[-1]
    album = sp.album(album_id)

    artists = ", ".join(a["name"] for a in album.get("artists", []))
    tracks = album.get("tracks", {}).get("items", [])

    lines = [
        f"💿 **{album['name']}** by {artists}",
        f"  Released: {album.get('release_date', 'Unknown')}",
        f"  Tracks: {album.get('total_tracks', 0)}",
        f"  URI: `{album['uri']}`",
        f"\n  **Tracks:**",
    ]
    for i, t in enumerate(tracks, 1):
        dur = t.get("duration_ms", 0)
        lines.append(f"  {i}. {t['name']} [{dur // 60000}:{(dur % 60000) // 1000:02d}]")

    return _ok("\n".join(lines))


# =============================================================================
# User / Following
# =============================================================================


def _user_profile() -> Dict:
    sp = _get_client()
    user = sp.current_user()
    return _ok(
        f"👤 **{user.get('display_name', 'Unknown')}**\n"
        f"  Email: {user.get('email', 'N/A')}\n"
        f"  Country: {user.get('country', 'N/A')}\n"
        f"  Product: {user.get('product', 'N/A')}\n"
        f"  Followers: {user.get('followers', {}).get('total', 0):,}\n"
        f"  URI: `{user.get('uri', '')}`"
    )


def _followed_artists(limit: int = 20) -> Dict:
    sp = _get_client()
    results = sp.current_user_followed_artists(limit=limit)
    artists = results.get("artists", {}).get("items", [])

    lines = [f"🎤 **Followed Artists** ({len(artists)}):\n"]
    for i, a in enumerate(artists, 1):
        lines.append(_format_artist(a, idx=i))
    return _ok("\n".join(lines))


def _follow_artist(artist_id: str) -> Dict:
    sp = _get_client()
    ids = [
        a.strip().split(":")[-1] if ":" in a.strip() else a.strip()
        for a in artist_id.split(",")
    ]
    sp.user_follow_artists(ids)
    return _ok(f"✅ Followed {len(ids)} artist(s).")


def _unfollow_artist(artist_id: str) -> Dict:
    sp = _get_client()
    ids = [
        a.strip().split(":")[-1] if ":" in a.strip() else a.strip()
        for a in artist_id.split(",")
    ]
    sp.user_unfollow_artists(ids)
    return _ok(f"❌ Unfollowed {len(ids)} artist(s).")


# =============================================================================
# The unified tool
# =============================================================================


@tool
def use_spotify(
    action: str,
    # Common
    query: str = None,
    uri: str = None,
    limit: int = 20,
    # Playback
    device_id: str = None,
    context_uri: str = None,
    position_ms: int = None,
    volume: int = None,
    state: str = None,
    shuffle_state: bool = None,
    offset: int = None,
    # Search
    search_type: str = "track",
    # Playlist
    playlist_id: str = None,
    name: str = None,
    description: str = None,
    public: bool = False,
    uris: str = None,
    # Discovery
    time_range: str = "medium_term",
    seed_tracks: str = None,
    seed_artists: str = None,
    seed_genres: str = None,
    # Artist / Album
    artist_id: str = None,
    album_id: str = None,
) -> Dict[str, Any]:
    """🎵 Full Spotify control — playback, search, playlists, queue, library, devices, discovery.

    One tool to control everything on Spotify. Like use_aws for AWS, but for music.

    Requires env vars: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
    Optional: SPOTIFY_REDIRECT_URI (default: http://127.0.0.1:8888/callback)

    Args:
        action: The action to perform. Format: "category.operation"

            **Playback:**
            - "now_playing" — What's currently playing
            - "play" — Start/resume playback (uri=track URIs, context_uri=album/playlist, device_id)
            - "pause" — Pause playback
            - "next" — Skip to next track
            - "previous" — Go to previous track
            - "seek" — Seek to position (position_ms)
            - "volume" — Set volume (volume=0-100, device_id)
            - "shuffle" — Toggle shuffle (shuffle_state=bool)
            - "repeat" — Set repeat mode (state="track"/"context"/"off")
            - "transfer" — Transfer playback to device (device_id)

            **Queue:**
            - "queue.add" — Add to queue (uri, device_id)
            - "queue.list" — Show current queue

            **Search:**
            - "search" — Search Spotify (query, search_type="track"/"artist"/"album"/"playlist", limit)

            **Devices:**
            - "devices" — List available devices

            **Playlists:**
            - "playlists" — List my playlists (limit)
            - "playlist.tracks" — Get playlist tracks (playlist_id, limit)
            - "playlist.create" — Create playlist (name, description, public)
            - "playlist.add" — Add tracks to playlist (playlist_id, uris)
            - "playlist.remove" — Remove tracks from playlist (playlist_id, uris)

            **Library:**
            - "liked" — List liked/saved songs (limit)
            - "like" — Save tracks to library (uris)
            - "unlike" — Remove tracks from library (uris)

            **Discovery:**
            - "recent" — Recently played tracks (limit)
            - "top_tracks" — Your top tracks (time_range, limit)
            - "top_artists" — Your top artists (time_range, limit)
            - "recommendations" — Get recommendations (seed_tracks, seed_artists, seed_genres, limit)
            - "genres" — List available genre seeds

            **Artist / Album:**
            - "artist" — Artist info + top tracks + albums (artist_id)
            - "album" — Album info + tracklist (album_id)

            **User:**
            - "profile" — Your Spotify profile
            - "following" — Followed artists (limit)
            - "follow" — Follow artist(s) (artist_id, comma-separated)
            - "unfollow" — Unfollow artist(s) (artist_id, comma-separated)

    Returns:
        Dict with status and content
    """
    try:
        # --- Playback ---
        if action == "now_playing":
            return _now_playing()
        elif action == "play":
            return _play(
                uri=uri, device_id=device_id, context_uri=context_uri, offset=offset
            )
        elif action == "pause":
            return _pause()
        elif action == "next":
            return _next_track()
        elif action == "previous":
            return _previous_track()
        elif action == "seek":
            if position_ms is None:
                return _err("position_ms required for seek")
            return _seek(position_ms)
        elif action == "volume":
            if volume is None:
                return _err("volume (0-100) required")
            return _set_volume(volume, device_id=device_id)
        elif action == "shuffle":
            if shuffle_state is None:
                return _err("shuffle_state (true/false) required")
            return _shuffle(shuffle_state)
        elif action == "repeat":
            if not state:
                return _err("state ('track', 'context', or 'off') required")
            return _repeat(state)
        elif action == "transfer":
            if not device_id:
                return _err("device_id required for transfer")
            return _transfer_playback(device_id)

        # --- Queue ---
        elif action == "queue.add":
            if not uri:
                return _err("uri required for queue.add")
            return _add_to_queue(uri, device_id=device_id)
        elif action == "queue.list":
            return _get_queue()

        # --- Search ---
        elif action == "search":
            if not query:
                return _err("query required for search")
            return _search(query, search_type=search_type, limit=limit)

        # --- Devices ---
        elif action == "devices":
            return _list_devices()

        # --- Playlists ---
        elif action == "playlists":
            return _my_playlists(limit=limit)
        elif action == "playlist.tracks":
            if not playlist_id:
                return _err("playlist_id required")
            return _playlist_tracks(playlist_id, limit=limit)
        elif action == "playlist.create":
            if not name:
                return _err("name required for playlist.create")
            return _create_playlist(name, description=description or "", public=public)
        elif action == "playlist.add":
            if not playlist_id or not uris:
                return _err("playlist_id and uris required")
            return _add_to_playlist(playlist_id, uris)
        elif action == "playlist.remove":
            if not playlist_id or not uris:
                return _err("playlist_id and uris required")
            return _remove_from_playlist(playlist_id, uris)

        # --- Library ---
        elif action == "liked":
            return _liked_songs(limit=limit)
        elif action == "like":
            if not uris:
                return _err("uris required for like")
            return _save_tracks(uris)
        elif action == "unlike":
            if not uris:
                return _err("uris required for unlike")
            return _remove_saved_tracks(uris)

        # --- Discovery ---
        elif action == "recent":
            return _recently_played(limit=limit)
        elif action == "top_tracks":
            return _top_tracks(time_range=time_range, limit=limit)
        elif action == "top_artists":
            return _top_artists(time_range=time_range, limit=limit)
        elif action == "recommendations":
            return _recommendations(
                seed_tracks=seed_tracks,
                seed_artists=seed_artists,
                seed_genres=seed_genres,
                limit=limit,
            )
        elif action == "genres":
            return _available_genres()

        # --- Artist / Album ---
        elif action == "artist":
            if not artist_id:
                return _err("artist_id required")
            return _artist_info(artist_id)
        elif action == "album":
            if not album_id:
                return _err("album_id required")
            return _album_info(album_id)

        # --- User ---
        elif action == "profile":
            return _user_profile()
        elif action == "following":
            return _followed_artists(limit=limit)
        elif action == "follow":
            if not artist_id:
                return _err("artist_id required for follow")
            return _follow_artist(artist_id)
        elif action == "unfollow":
            if not artist_id:
                return _err("artist_id required for unfollow")
            return _unfollow_artist(artist_id)

        else:
            return _err(
                f"Unknown action: {action}\n\n"
                "Valid actions:\n"
                "  Playback: now_playing, play, pause, next, previous, seek, volume, shuffle, repeat, transfer\n"
                "  Queue: queue.add, queue.list\n"
                "  Search: search\n"
                "  Devices: devices\n"
                "  Playlists: playlists, playlist.tracks, playlist.create, playlist.add, playlist.remove\n"
                "  Library: liked, like, unlike\n"
                "  Discovery: recent, top_tracks, top_artists, recommendations, genres\n"
                "  Artist/Album: artist, album\n"
                "  User: profile, following, follow, unfollow"
            )

    except Exception as e:
        error_msg = str(e)
        # Handle common Spotify API errors
        if "NO_ACTIVE_DEVICE" in error_msg or "No active device" in error_msg:
            return _err("No active Spotify device. Open Spotify on a device first.")
        elif "PREMIUM_REQUIRED" in error_msg:
            return _err("Spotify Premium required for this action.")
        elif "token" in error_msg.lower() and (
            "expired" in error_msg.lower() or "invalid" in error_msg.lower()
        ):
            # Reset client to force re-auth
            global _sp_client
            _sp_client = None
            return _err(f"Auth error (token reset, retry): {error_msg}")
        logger.error(f"use_spotify error: {e}")
        return _err(f"Spotify error: {error_msg}")