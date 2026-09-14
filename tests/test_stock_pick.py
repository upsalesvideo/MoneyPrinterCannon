import json

import pytest

from moneyprintercannon import stock
from moneyprintercannon.schema import Scene


class FakeResp:
    def __init__(self, payload, status=200, headers=None):
        self._p = payload
        self.status_code = status
        self.text = json.dumps(payload)
        self.headers = headers or {}

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def pexels_payload():
    def vid(vid_id, dur, files):
        return {"id": vid_id, "duration": dur, "url": f"https://pexels.com/v/{vid_id}", "user": {"name": "Ann"},
                "video_files": [{"link": f"https://cdn/{vid_id}-{w}x{h}.mp4?x=1", "width": w, "height": h, "file_type": "video/mp4"} for w, h in files]}
    return {"videos": [
        vid(1, 12, [(720, 1280), (1080, 1920), (2160, 3840)]),   # portrait, has 1080 and 4K → pick 1080
        vid(2, 4, [(1080, 1920)]),                                 # too short for 6 s
        vid(3, 30, [(1920, 1080)]),                                # landscape → wrong orientation
    ]}


def test_pexels_pick_smallest_hd_and_orientation(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None, **kw):
        calls.append((url, params, headers))
        assert headers["Authorization"] == "k"
        return FakeResp(pexels_payload())

    monkeypatch.setattr(stock.requests, "get", fake_get)
    items = stock.search_videos("sleeping cat", "9:16")
    assert calls[0][1]["orientation"] == "portrait"
    assert [i.id for i in items] == ["1", "2", "3"]
    assert (items[0].width, items[0].height) == (1080, 1920)  # smallest ≥1080, not 4K
    assert items[2].width == 1920  # landscape falls back to largest available
    assert items[0].credit == "Pexels / Ann"
    # cached: second call must not hit the network
    stock.search_videos("sleeping cat", "9:16")
    assert len(calls) == 1


def test_pick_for_scene_prefers_long_enough_and_skips_used(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(stock.requests, "get", lambda *a, **k: FakeResp(pexels_payload()))
    scene = Scene(index=1, narration="x", visual_prompt="y", search_terms=["sleeping cat", "cat"])
    used: set[str] = set()
    first = stock.pick_for_scene(scene, "9:16", need_seconds=6, used_urls=used)
    assert first is not None and first.id == "1"  # 12 s ≥ 6 s
    second = stock.pick_for_scene(scene, "9:16", need_seconds=6, used_urls=used)
    assert second is not None and second.id != "1"
    third = stock.pick_for_scene(scene, "9:16", need_seconds=6, used_urls=used)
    fourth = stock.pick_for_scene(scene, "9:16", need_seconds=6, used_urls=used)
    assert third is not None and fourth is None  # all three used across both terms (same cached result)


def test_no_keys_raise_stock_unavailable(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    assert not stock.stock_available()
    with pytest.raises(stock.StockUnavailable):
        stock.search_videos("cat", "9:16")


def test_pixabay_parsing(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "p")
    payload = {"hits": [{"id": 7, "duration": 20, "pageURL": "https://pixabay.com/v/7", "user": "Bob",
                         "videos": {"large": {"url": "https://cdn/7l.mp4", "width": 3840, "height": 2160},
                                    "medium": {"url": "https://cdn/7m.mp4", "width": 1920, "height": 1080},
                                    "small": {"url": "https://cdn/7s.mp4", "width": 1280, "height": 720}}}]}
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None, **kw):
        seen.update(params)
        return FakeResp(payload)

    monkeypatch.setattr(stock.requests, "get", fake_get)
    items = stock.search_videos("city", "16:9", provider="pixabay")
    assert seen["key"] == "p" and seen["min_width"] == 1280
    assert items[0].url.endswith("7m.mp4") and items[0].credit == "Pixabay / Bob"
