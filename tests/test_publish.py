"""Publishing via Composio — offline tests with a fake client."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from moneyprintercannon import publish


class FakeTools:
    def __init__(self, log):
        self.log = log

    def execute(self, slug, arguments, user_id=None, **kw):
        self.log.append((slug, arguments, user_id))
        if slug == "YOUTUBE_UPLOAD_VIDEO":
            return {"successful": True, "data": {"id": "abc123"}}
        if slug == "INSTAGRAM_GET_USER_INFO":
            return {"successful": True, "data": {"id": "1789"}}
        if slug == "INSTAGRAM_POST_IG_USER_MEDIA":
            return {"successful": True, "data": {"id": "container9"}}
        if slug == "INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH":
            return {"successful": True, "data": {"id": "media77", "permalink": "https://www.instagram.com/reel/x/"}}
        if slug == "TIKTOK_UPLOAD_VIDEO":
            return SimpleNamespace(successful=True, data={"publish_id": "p1"}, error=None)
        if slug == "LINKEDIN_UPLOAD_VIDEO":
            return {"successful": True, "data": {"video_urn": "urn:li:video:1"}}
        if slug == "LINKEDIN_CREATE_VIDEO_POST":
            return {"successful": True, "data": {"id": "urn:li:share:2"}}
        return {"successful": False, "error": f"no such tool {slug}"}


class FakeConnected:
    def __init__(self, active):
        self.active = active

    def list(self, **kw):
        return SimpleNamespace(items=[SimpleNamespace(toolkit=SimpleNamespace(slug=s)) for s in self.active])


class FakeToolkits:
    def authorize(self, *, user_id, toolkit):
        return SimpleNamespace(redirect_url=f"https://connect.composio.dev/link/{toolkit}", wait_for_connection=lambda *a, **k: None)


class FakeComposio:
    def __init__(self, active):
        self.log = []
        self.tools = FakeTools(self.log)
        self.connected_accounts = FakeConnected(active)
        self.toolkits = FakeToolkits()


@pytest.fixture
def task_dir(tmp_path):
    d = tmp_path / "20260914-000000-abcdef"
    d.mkdir()
    (d / "final.mp4").write_bytes(b"\x00" * 10)
    (d / "cover.jpg").write_bytes(b"\xff")
    (d / "script.json").write_text(json.dumps({
        "topic": "Cats", "language": "en", "title": "Why cats sleep 16 hours",
        "scenes": [{"index": 1, "narration": "Cats sleep.", "visual_prompt": "cat"}],
        "social": {"title": "Cat sleep secrets", "caption": "Cats sleep a lot.", "hashtags": ["cats", "#pets"]},
    }), encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "ak_test")
    monkeypatch.setenv("COMPOSIO_USER_ID", "u1")
    yield
    publish.set_client(None)


def test_not_configured(monkeypatch, task_dir):
    monkeypatch.delenv("COMPOSIO_API_KEY")
    publish.set_client(None)
    assert publish.is_configured() is False
    assert publish.connected_platforms() == {p: False for p in publish.PLATFORMS}
    res = publish.publish_task(task_dir, ["youtube"])
    assert res[0].ok is False and "COMPOSIO_API_KEY" in res[0].error


def test_publish_all_platforms(task_dir):
    fake = FakeComposio(active=["youtube", "instagram", "tiktok", "linkedin"])
    publish.set_client(fake)
    res = publish.publish_task(task_dir, ["youtube", "instagram", "tiktok", "linkedin"], privacy="public")
    by = {r.platform: r for r in res}
    assert by["youtube"].ok and by["youtube"].url == "https://youtu.be/abc123"
    assert by["instagram"].ok and by["instagram"].id == "media77"
    assert by["tiktok"].ok and by["tiktok"].id == "p1"
    assert by["linkedin"].ok and by["linkedin"].url.endswith("urn:li:share:2")
    slugs = [s for s, _, _ in fake.log]
    assert slugs == ["YOUTUBE_UPLOAD_VIDEO", "INSTAGRAM_GET_USER_INFO", "INSTAGRAM_POST_IG_USER_MEDIA",
                     "INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH", "TIKTOK_UPLOAD_VIDEO", "LINKEDIN_UPLOAD_VIDEO", "LINKEDIN_CREATE_VIDEO_POST"]
    yt_args = fake.log[0][1]
    assert yt_args["videoFilePath"].endswith("final.mp4") and yt_args["privacyStatus"] == "public"
    assert yt_args["tags"] == ["cats", "pets"] and "#cats #pets" in yt_args["description"]
    ig_args = fake.log[2][1]
    assert ig_args["ig_user_id"] == "1789" and ig_args["media_type"] == "REELS"
    tt_args = fake.log[4][1]
    assert tt_args["privacy_level"] == "PUBLIC_TO_EVERYONE" and tt_args["is_aigc"] is True
    saved = publish.load_results(task_dir)
    assert len(saved) == 4 and all(r["ok"] for r in saved)


def test_not_connected_returns_link(task_dir):
    publish.set_client(FakeComposio(active=["youtube"]))
    res = publish.publish_task(task_dir, ["tiktok", "youtube"], privacy="private")
    tt, yt = res
    assert tt.ok is False and tt.url == "https://connect.composio.dev/link/tiktok" and "not connected" in tt.error
    assert yt.ok is True


def test_unknown_platform(task_dir):
    publish.set_client(FakeComposio(active=[]))
    (r,) = publish.publish_task(task_dir, ["vk"])
    assert r.ok is False and "unknown platform" in r.error


def test_copy_overrides(task_dir):
    c = publish.copy_from_task(task_dir, title="T", caption="C", hashtags=["a b", "#c"])
    assert c.title == "T" and c.hashtag_line == "#ab #c"
    assert c.caption_with_tags(7) == "C\n\n#ab"
