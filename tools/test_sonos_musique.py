"""Offline tests for plugins/sonos_musique.py — fake HA + fake iTunes/Spotify.

Run: venv/bin/python tools/test_sonos_musique.py
"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "merlin_plugins.sonos_musique", REPO / "plugins" / "sonos_musique.py"
)
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)

SONOS_IDS = ["media_player.salon", "media_player.cuisine"]

ITUNES_ALBUMS = [
    {"collectionName": "Discovery", "artistName": "Daft Punk",
     "collectionViewUrl": "https://music.apple.com/fr/album/discovery/697194953"},
    {"collectionName": "Random Access Memories", "artistName": "Daft Punk",
     "collectionViewUrl": "https://music.apple.com/fr/album/ram/617154241"},
]
ITUNES_SONGS = [
    {"trackName": "One More Time", "artistName": "Daft Punk",
     "trackViewUrl": "https://music.apple.com/fr/album/one-more-time/697194953?i=1"},
]
SPOTIFY_SEARCH = {
    "albums": {"items": [{"name": "Random Access Memories",
                          "artists": [{"name": "Daft Punk"}],
                          "external_urls": {"spotify": "https://open.spotify.com/album/RAM"}}]},
    "tracks": {"items": []},
    "playlists": {"items": [{"name": "Comptines pour enfants",
                             "external_urls": {"spotify": "https://open.spotify.com/playlist/KIDS"}}]},
}


class FakeHA:
    def __init__(self):
        self.calls = []

    def request(self, method, path, payload=None):
        if path == "/api/template":
            return json.dumps(SONOS_IDS)
        if path == "/api/states":
            return [
                {"entity_id": "media_player.salon", "state": "idle",
                 "attributes": {"friendly_name": "Salon", "volume_level": 0.2}},
                {"entity_id": "media_player.cuisine", "state": "idle",
                 "attributes": {"friendly_name": "Cuisine", "volume_level": 0.1}},
            ]
        if path.startswith("/api/services/media_player/"):
            self.calls.append((path.rsplit("/", 1)[1], payload))
            return []
        raise AssertionError(f"unexpected path {path}")


def fake_http(itunes_albums=None, itunes_songs=None, spotify=None):
    def dispatch(url, data=None, headers=None):
        if url.startswith(sm._ITUNES):
            if "entity=album" in url:
                return {"results": itunes_albums if itunes_albums is not None else ITUNES_ALBUMS}
            return {"results": itunes_songs if itunes_songs is not None else ITUNES_SONGS}
        if url == sm._SPOTIFY_TOKEN_URL:
            return {"access_token": "tok", "expires_in": 3600}
        if url.startswith(sm._SPOTIFY_API + "/search"):
            return spotify if spotify is not None else SPOTIFY_SEARCH
        raise AssertionError(f"unexpected url {url}")
    return dispatch


class FakeLLM:
    def __init__(self):
        self.frames = []

    async def push_frame(self, frame):
        self.frames.append(frame)


class FakeParams:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.llm = FakeLLM()
        self.results = []

    async def result_callback(self, result):
        self.results.append(result)


def run(fake_ha, http=None, aliases=None, default_room=None, **arguments):
    sm.common._entity_cache = None
    sm._spotify_token_cache = None
    sm.common.ha_request = fake_ha.request
    sm._http_json = http or fake_http()
    tmp = Path(tempfile.mkdtemp())
    sm.ALIAS_PATH = tmp / "sonos-aliases.json"
    if aliases is not None:
        sm.ALIAS_PATH.write_text(json.dumps(aliases))
    os.environ.pop("MERLIN_SONOS_DEFAULT_ROOM", None)
    os.environ.pop("MERLIN_SPOTIFY_ID", None)
    os.environ.pop("MERLIN_SPOTIFY_SECRET", None)
    if default_room:
        os.environ["MERLIN_SONOS_DEFAULT_ROOM"] = default_room
    params = FakeParams(**arguments)
    asyncio.run(sm.handler(params))
    assert len(params.results) == 1, "result_callback must fire exactly once"
    return params.results[0]


def play_call(fake_ha):
    plays = [c for c in fake_ha.calls if c[0] == "play_media"]
    assert len(plays) <= 1
    return plays[0][1] if plays else None


def test_alias():
    ha = FakeHA()
    r = run(ha, aliases={"comptines du soir": "https://open.spotify.com/playlist/DODO"},
            recherche="les comptines du soir", piece="cuisine")
    assert r == {"ok": "je lance comptines du soir dans Cuisine"}, r
    assert play_call(ha) == {"entity_id": "media_player.cuisine",
                             "media_content_type": "music",
                             "media_content_id": "https://open.spotify.com/playlist/DODO"}
    print("ok: alias fuzzy-matched and played")


def test_apple_album_and_titre():
    ha = FakeHA()
    r = run(ha, recherche="Discovery de Daft Punk", type="album", piece="salon")
    assert "l'album Discovery de Daft Punk" in r["ok"] and r["service"] == "Apple Music", r
    assert play_call(ha)["media_content_id"].endswith("/discovery/697194953")
    ha = FakeHA()
    r = run(ha, recherche="One More Time", type="titre", piece="salon")
    assert "One More Time de Daft Punk" in r["ok"], r
    print("ok: Apple Music album + titre resolution")


def test_apple_artiste():
    ha = FakeHA()
    r = run(ha, recherche="Daft Punk", type="artiste", piece="cuisine")
    assert "l'album" in r["ok"] and "Daft Punk" in r["ok"], r
    assert play_call(ha)["media_content_id"].startswith("https://music.apple.com/")
    print("ok: artiste -> son album le plus en vue, annoncé")


def test_low_confidence_refuses():
    ha = FakeHA()
    junk = [{"collectionName": "Zumba Fitness Hits", "artistName": "Various",
             "collectionViewUrl": "https://music.apple.com/x"}]
    r = run(ha, http=fake_http(itunes_albums=junk, itunes_songs=[]),
            recherche="le dernier album de Christine and the Queens", piece="salon")
    assert "error" in r and "candidats" in r["error"], r
    assert play_call(ha) is None, "must NOT play on a doubtful match"
    print("ok: doubtful match -> asks, does not play")


def test_playlist_needs_alias():
    ha = FakeHA()
    r = run(ha, recherche="ma playlist jogging", type="playlist", piece="salon")
    assert "error" in r and "alias" in r["error"], r
    assert play_call(ha) is None
    print("ok: unknown playlist -> no improvisation")


def test_spotify_explicit():
    ha = FakeHA()
    os.environ["MERLIN_SPOTIFY_ID"] = "id"
    os.environ["MERLIN_SPOTIFY_SECRET"] = "secret"
    try:
        sm.common._entity_cache = None
        sm._spotify_token_cache = None
        sm.common.ha_request = ha.request
        sm._http_json = fake_http()
        sm.ALIAS_PATH = Path(tempfile.mkdtemp()) / "none.json"
        params = FakeParams(recherche="Random Access Memories", type="album",
                            piece="salon", service="spotify")
        asyncio.run(sm.handler(params))
        r = params.results[0]
        assert r["service"] == "Spotify", r
        assert play_call(ha)["media_content_id"] == "https://open.spotify.com/album/RAM"
    finally:
        os.environ.pop("MERLIN_SPOTIFY_ID", None)
        os.environ.pop("MERLIN_SPOTIFY_SECRET", None)
    print("ok: explicit Spotify search and playback")


def test_room_selection():
    ha = FakeHA()
    r = run(ha, recherche="Discovery Daft Punk", type="album")  # no room, 2 speakers
    assert "error" in r and "précise la pièce" in r["error"], r
    ha = FakeHA()
    r = run(ha, recherche="Discovery Daft Punk", type="album", default_room="salon")
    assert r["ok"].endswith("dans Salon"), r
    print("ok: room required unless default; default room used")


def test_ha_down_single_result():
    class DownHA:
        def request(self, *a, **k):
            raise RuntimeError("Home Assistant injoignable (http://x): refused")
    r = run(DownHA(), recherche="Discovery", piece="salon")
    assert "error" in r and "injoignable" in r["error"], r
    print("ok: HA down -> single error result")


if __name__ == "__main__":
    test_alias()
    test_apple_album_and_titre()
    test_apple_artiste()
    test_low_confidence_refuses()
    test_playlist_needs_alias()
    test_spotify_explicit()
    test_room_selection()
    test_ha_down_single_result()
    print("all sonos_musique tests passed")
