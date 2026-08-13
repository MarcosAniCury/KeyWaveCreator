"""Spotify PKCE authorization and bounded playlist metadata access."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Final, Protocol
from urllib.parse import urlparse

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken, PlaylistTrack
from keywave_creator.application.source_expansion import validate_spotify_client_id

SPOTIFY_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9]{22}$")
MAX_PLAYLIST_ITEMS = 200
MAX_HTTP_RESPONSE_BYTES = 2 * 1024 * 1024
AUTHORIZATION_TIMEOUT_SECONDS = 180.0
REQUEST_TIMEOUT_SECONDS = 30.0
TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
SPOTIFY_CALLBACK_HOST: Final[str] = "127.0.0.1"
SPOTIFY_CALLBACK_PORT: Final[int] = 9657
SPOTIFY_CALLBACK_PATH: Final[str] = "/callback"
SPOTIFY_REDIRECT_URI: Final[str] = (
    f"http://{SPOTIFY_CALLBACK_HOST}:{SPOTIFY_CALLBACK_PORT}{SPOTIFY_CALLBACK_PATH}"
)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Bounded HTTP response used by the Spotify adapter and its tests."""

    status: int
    body: bytes


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> HttpResponse: ...


class SpotifyAccessTokenProvider(Protocol):
    def access_token(self, client_id: str, cancellation: CancellationToken) -> str: ...

    def invalidate(self) -> None: ...


class UrlLibHttpTransport:
    """Perform TLS-verified, bounded Spotify requests with the standard library."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> HttpResponse:
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers or {}),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
                status = response.status
        except urllib.error.HTTPError as error:
            payload = error.read(MAX_HTTP_RESPONSE_BYTES + 1)
            status = error.code
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise CreatorError(
                CreatorErrorCode.SOURCE_LOOKUP_FAILED,
                "Spotify could not be reached. Check your connection and try again.",
                diagnostic=str(error),
            ) from error
        if len(payload) > MAX_HTTP_RESPONSE_BYTES:
            raise CreatorError(
                CreatorErrorCode.SOURCE_LOOKUP_FAILED,
                "Spotify returned more playlist data than KeyWave can safely process.",
            )
        return HttpResponse(status=status, body=payload)


@dataclass(frozen=True, slots=True)
class _AccessToken:
    value: str
    expires_at_monotonic: float


@dataclass(slots=True)
class _AuthorizationResult:
    code: str = ""
    state: str = ""
    error: str = ""


class _LoopbackCallbackHandler(BaseHTTPRequestHandler):
    result: _AuthorizationResult

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urlparse(self.path).query)
        self.result.code = _first_query_value(query, "code")
        self.result.state = _first_query_value(query, "state")
        self.result.error = _first_query_value(query, "error")
        message = (
            b"<html><body><h1>KeyWave connected</h1>"
            b"<p>You can close this tab and return to KeyWave Creator.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(message)))
        self.end_headers()
        self.wfile.write(message)

    def log_message(self, _format: str, *args: object) -> None:
        del args


BrowserOpener = Callable[[str], bool]


class SpotifyPkceSession:
    """Own one in-memory Spotify token and authorize through a loopback callback."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        browser_opener: BrowserOpener | None = None,
    ) -> None:
        self._transport = transport or UrlLibHttpTransport()
        self._browser_opener = browser_opener or webbrowser.open_new_tab
        self._token: _AccessToken | None = None
        self._client_id = ""

    def access_token(self, client_id: str, cancellation: CancellationToken) -> str:
        normalized_client_id = validate_spotify_client_id(client_id)
        token = self._token
        if (
            token is not None
            and self._client_id == normalized_client_id
            and token.expires_at_monotonic - time.monotonic() > TOKEN_EXPIRY_MARGIN_SECONDS
        ):
            return token.value
        token = self._authorize(normalized_client_id, cancellation)
        self._client_id = normalized_client_id
        self._token = token
        return token.value

    def invalidate(self) -> None:
        """Forget an expired or rejected token without persisting Spotify credentials."""
        self._token = None

    def _authorize(self, client_id: str, cancellation: CancellationToken) -> _AccessToken:
        verifier = _new_code_verifier()
        state = secrets.token_urlsafe(24)
        result = _AuthorizationResult()
        handler = type(
            "KeyWaveSpotifyCallback",
            (_LoopbackCallbackHandler,),
            {"result": result},
        )
        try:
            server = HTTPServer(
                (SPOTIFY_CALLBACK_HOST, SPOTIFY_CALLBACK_PORT),
                handler,
            )
        except OSError as error:
            raise CreatorError(
                CreatorErrorCode.SPOTIFY_AUTH_REQUIRED,
                "Spotify sign-in cannot start because local port 9657 is unavailable. "
                "Close the app using it and try again.",
                diagnostic=f"Spotify callback bind failed: {error}",
            ) from error
        server.timeout = 0.25
        redirect_uri = SPOTIFY_REDIRECT_URI
        parameters = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": "playlist-read-private playlist-read-collaborative",
                "code_challenge_method": "S256",
                "code_challenge": _code_challenge(verifier),
                "state": state,
            }
        )
        authorization_url = f"https://accounts.spotify.com/authorize?{parameters}"
        try:
            if not self._browser_opener(authorization_url):
                raise CreatorError(
                    CreatorErrorCode.SPOTIFY_AUTH_REQUIRED,
                    "The Spotify sign-in page could not be opened.",
                )
            deadline = time.monotonic() + AUTHORIZATION_TIMEOUT_SECONDS
            while not result.code and not result.error:
                cancellation.raise_if_cancelled()
                if time.monotonic() >= deadline:
                    raise CreatorError(
                        CreatorErrorCode.SPOTIFY_AUTH_REQUIRED,
                        "Spotify sign-in timed out. Add the playlist again to retry.",
                    )
                server.handle_request()
        finally:
            server.server_close()

        if result.error:
            raise CreatorError(
                CreatorErrorCode.SPOTIFY_ACCESS_DENIED,
                "Spotify access was not granted. Add the playlist again to retry.",
                diagnostic=result.error,
            )
        if not secrets.compare_digest(result.state, state):
            raise CreatorError(
                CreatorErrorCode.SPOTIFY_ACCESS_DENIED,
                "Spotify returned an invalid authorization response.",
            )
        response = self._transport.request(
            "POST",
            "https://accounts.spotify.com/api/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urllib.parse.urlencode(
                {
                    "client_id": client_id,
                    "grant_type": "authorization_code",
                    "code": result.code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                }
            ).encode("ascii"),
        )
        if response.status != 200:
            raise CreatorError(
                CreatorErrorCode.SPOTIFY_ACCESS_DENIED,
                "Spotify sign-in could not be completed. Check the Client ID and redirect URI.",
                diagnostic=f"token endpoint returned HTTP {response.status}",
            )
        parsed = _decode_object(response.body, "Spotify returned an invalid access token.")
        access_token = parsed.get("access_token")
        expires_in = parsed.get("expires_in")
        if not isinstance(access_token, str) or not access_token:
            raise CreatorError(
                CreatorErrorCode.SPOTIFY_ACCESS_DENIED,
                "Spotify returned an invalid access token.",
            )
        lifetime_seconds = expires_in if type(expires_in) is int and expires_in > 0 else 3_600
        return _AccessToken(
            value=access_token,
            expires_at_monotonic=time.monotonic() + lifetime_seconds,
        )


class SpotifyWebApiPlaylistResolver:
    """Read the current user's owned or collaborative playlists through Spotify."""

    def __init__(
        self,
        *,
        session: SpotifyAccessTokenProvider | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        self._transport = transport or UrlLibHttpTransport()
        self._session = session or SpotifyPkceSession(transport=self._transport)

    def resolve(
        self,
        url: str,
        client_id: str,
        cancellation: CancellationToken,
    ) -> tuple[PlaylistTrack, ...]:
        playlist_id = _validate_playlist_url(url)
        token = self._session.access_token(client_id, cancellation)
        tracks: list[PlaylistTrack] = []
        offset = 0
        while True:
            cancellation.raise_if_cancelled()
            query = urllib.parse.urlencode({"limit": 50, "offset": offset})
            response = self._transport.request(
                "GET",
                f"https://api.spotify.com/v1/playlists/{playlist_id}/items?{query}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status == 401:
                self._session.invalidate()
                raise CreatorError(
                    CreatorErrorCode.SPOTIFY_AUTH_REQUIRED,
                    "Spotify authorization expired. Add the playlist again to reconnect.",
                )
            if response.status in {403, 404}:
                raise CreatorError(
                    CreatorErrorCode.SPOTIFY_ACCESS_DENIED,
                    "Spotify only exposes playlists you own or collaborate on to this app.",
                )
            if response.status == 429:
                raise CreatorError(
                    CreatorErrorCode.SOURCE_LOOKUP_FAILED,
                    "Spotify is rate-limiting playlist access. Wait a moment and try again.",
                )
            if response.status != 200:
                raise CreatorError(
                    CreatorErrorCode.SOURCE_LOOKUP_FAILED,
                    "The Spotify playlist could not be read.",
                    diagnostic=f"playlist endpoint returned HTTP {response.status}",
                )
            page = _decode_object(response.body, "Spotify returned invalid playlist data.")
            raw_total = page.get("total")
            if type(raw_total) is int and raw_total > MAX_PLAYLIST_ITEMS:
                raise CreatorError(
                    CreatorErrorCode.INVALID_SOURCE,
                    f"Playlists can contain at most {MAX_PLAYLIST_ITEMS} tracks.",
                )
            raw_items = page.get("items")
            if not isinstance(raw_items, list):
                raise CreatorError(
                    CreatorErrorCode.SOURCE_LOOKUP_FAILED,
                    "Spotify returned invalid playlist data.",
                )
            for raw_item in raw_items:
                track = _playlist_track(raw_item)
                if track is not None:
                    tracks.append(track)
                    if len(tracks) > MAX_PLAYLIST_ITEMS:
                        raise CreatorError(
                            CreatorErrorCode.INVALID_SOURCE,
                            f"Playlists can contain at most {MAX_PLAYLIST_ITEMS} tracks.",
                        )
            offset += len(raw_items)
            if offset > MAX_PLAYLIST_ITEMS:
                raise CreatorError(
                    CreatorErrorCode.INVALID_SOURCE,
                    f"Playlists can contain at most {MAX_PLAYLIST_ITEMS} tracks.",
                )
            if not raw_items or (type(raw_total) is int and offset >= raw_total):
                break
        if not tracks:
            raise CreatorError(
                CreatorErrorCode.SOURCE_LOOKUP_FAILED,
                "The Spotify playlist has no playable music tracks.",
            )
        return tuple(tracks)


def _validate_playlist_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError:
        port = -1
    parts = tuple(part for part in parsed.path.split("/") if part)
    if (
        parsed.scheme != "https"
        or host not in {"open.spotify.com", "www.open.spotify.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or len(parts) != 2
        or parts[0].casefold() != "playlist"
        or not SPOTIFY_PLAYLIST_ID.fullmatch(parts[1])
    ):
        raise CreatorError(
            CreatorErrorCode.INVALID_SOURCE,
            "The Spotify playlist URL is not valid.",
        )
    return parts[1]


def _new_code_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _decode_object(payload: bytes, public_message: str) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(payload.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("JSON root is not an object")
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise CreatorError(
            CreatorErrorCode.SOURCE_LOOKUP_FAILED,
            public_message,
            diagnostic=str(error),
        ) from error
    return parsed


def _playlist_track(raw_item: object) -> PlaylistTrack | None:
    if not isinstance(raw_item, dict):
        return None
    raw_track = raw_item.get("item")
    if not isinstance(raw_track, dict):
        raw_track = raw_item.get("track")
    if not isinstance(raw_track, dict):
        return None
    if raw_track.get("type") not in {None, "track"} or raw_track.get("is_local") is True:
        return None
    title = raw_track.get("name")
    duration_ms = raw_track.get("duration_ms")
    raw_artists = raw_track.get("artists")
    if not isinstance(title, str) or not isinstance(raw_artists, list):
        return None
    artists = tuple(
        name
        for artist in raw_artists
        if isinstance(artist, dict)
        and isinstance((name := artist.get("name")), str)
        and name.strip()
    )
    normalized_title = " ".join(title.split())[:200].rstrip()
    normalized_artist = ", ".join(" ".join(artist.split()) for artist in artists)[:200].rstrip()
    if not normalized_title or not normalized_artist:
        return None
    return PlaylistTrack(
        title=normalized_title,
        artist=normalized_artist,
        duration_ms=duration_ms if type(duration_ms) is int and duration_ms > 0 else None,
    )


def _first_query_value(values: Mapping[str, list[str]], key: str) -> str:
    options = values.get(key, [])
    return options[0] if options else ""
