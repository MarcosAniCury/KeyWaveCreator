from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken
from keywave_creator.infrastructure.spotify import (
    SPOTIFY_REDIRECT_URI,
    HttpResponse,
    SpotifyPkceSession,
    SpotifyWebApiPlaylistResolver,
    _code_challenge,
    _validate_playlist_url,
)


class _Session:
    def __init__(self) -> None:
        self.invalidated = False

    def access_token(self, client_id: str, _cancellation: CancellationToken) -> str:
        assert client_id == "clientid1234567890"
        return "test-token"

    def invalidate(self) -> None:
        self.invalidated = True


class _Transport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = responses
        self.urls: list[str] = []
        self.authorization_headers: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout_seconds: float = 30.0,
    ) -> HttpResponse:
        del body, timeout_seconds
        assert method == "GET"
        self.urls.append(url)
        self.authorization_headers.append((headers or {}).get("Authorization", ""))
        return self._responses.pop(0)


def _response(payload: object, status: int = 200) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps(payload).encode("utf-8"))


def test_pkce_challenge_matches_rfc_7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

    assert _code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_spotify_pkce_uses_the_registered_fixed_redirect_uri() -> None:
    assert SPOTIFY_REDIRECT_URI == "http://127.0.0.1:9657/callback"


def test_spotify_auth_explains_when_fixed_callback_port_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_addresses: list[object] = []

    def raise_port_busy(address: object, _handler: object) -> None:
        requested_addresses.append(address)
        raise OSError(10048, "address already in use")

    monkeypatch.setattr(
        "keywave_creator.infrastructure.spotify.HTTPServer",
        raise_port_busy,
    )
    session = SpotifyPkceSession(browser_opener=lambda _url: True)

    with pytest.raises(CreatorError) as raised:
        session.access_token("clientid1234567890", CancellationToken())

    assert raised.value.code is CreatorErrorCode.SPOTIFY_AUTH_REQUIRED
    assert "9657" in raised.value.public_message
    assert requested_addresses == [("127.0.0.1", 9657)]


def test_spotify_playlist_url_requires_the_canonical_playlist_shape() -> None:
    assert (
        _validate_playlist_url("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        == "37i9dQZF1DXcBWIGoYBM5M"
    )
    with pytest.raises(CreatorError) as raised:
        _validate_playlist_url("https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl")
    assert raised.value.code is CreatorErrorCode.INVALID_SOURCE


def test_spotify_resolver_pages_tracks_and_skips_local_or_episode_items() -> None:
    first_page = {
        "total": 3,
        "items": [
            {
                "item": {
                    "type": "track",
                    "name": "First song",
                    "duration_ms": 181_000,
                    "artists": [{"name": "First artist"}],
                }
            },
            {
                "item": {
                    "type": "track",
                    "name": "Local song",
                    "is_local": True,
                    "artists": [{"name": "Artist"}],
                }
            },
        ],
    }
    second_page = {
        "total": 3,
        "items": [
            {
                "item": {
                    "type": "track",
                    "name": "Second song",
                    "duration_ms": 200_000,
                    "artists": [{"name": "Artist A"}, {"name": "Artist B"}],
                }
            }
        ],
    }
    transport = _Transport([_response(first_page), _response(second_page)])
    resolver = SpotifyWebApiPlaylistResolver(session=_Session(), transport=transport)

    tracks = resolver.resolve(
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        "clientid1234567890",
        CancellationToken(),
    )

    assert [(track.title, track.artist, track.duration_ms) for track in tracks] == [
        ("First song", "First artist", 181_000),
        ("Second song", "Artist A, Artist B", 200_000),
    ]
    assert "offset=0" in transport.urls[0]
    assert "offset=2" in transport.urls[1]
    assert transport.authorization_headers == ["Bearer test-token", "Bearer test-token"]


def test_spotify_resolver_explains_owned_or_collaborative_playlist_restriction() -> None:
    resolver = SpotifyWebApiPlaylistResolver(
        session=_Session(),
        transport=_Transport([_response({}, status=403)]),
    )

    with pytest.raises(CreatorError) as raised:
        resolver.resolve(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
            "clientid1234567890",
            CancellationToken(),
        )

    assert raised.value.code is CreatorErrorCode.SPOTIFY_ACCESS_DENIED
    assert "own or collaborate" in raised.value.public_message
