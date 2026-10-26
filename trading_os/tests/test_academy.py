"""Trading Academy (v1.6): YouTube link parsing, the library API, progress, private notes, ordering, backups."""

import json

import pytest

from trading_os.services import academy

from .browser import browser_page  # noqa: F401 - pytest fixture
from .conftest import update_settings

VID = "dQw4w9WgXcQ"
PL = "PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"

LINKS = {
    # pasted link -> (kind, video_id, playlist_id, start_sec)
    f"https://www.youtube.com/watch?v={VID}": ("video", VID, "", 0),
    f"https://youtube.com/watch?v={VID}&t=1m30s": ("video", VID, "", 90),
    f"https://m.youtube.com/watch?feature=share&v={VID}&t=95": ("video", VID, "", 95),
    f"https://www.youtube.com/watch?v={VID}&start=12": ("video", VID, "", 12),
    f"https://youtu.be/{VID}": ("video", VID, "", 0),
    f"https://youtu.be/{VID}?si=abc&t=1h2m3s": ("video", VID, "", 3723),
    f"https://www.youtube.com/shorts/{VID}": ("video", VID, "", 0),
    f"https://www.youtube.com/live/{VID}?feature=share": ("video", VID, "", 0),
    f"https://www.youtube.com/embed/{VID}?start=30": ("video", VID, "", 30),
    f"https://www.youtube-nocookie.com/embed/{VID}": ("video", VID, "", 0),
    f"https://music.youtube.com/watch?v={VID}": ("video", VID, "", 0),
    f"youtube.com/watch?v={VID}": ("video", VID, "", 0),
    f"http://youtu.be/{VID}": ("video", VID, "", 0),
    f"  https://www.youtube.com/watch?v={VID}  ": ("video", VID, "", 0),
    f"https://www.youtube.com/watch?v={VID}&list={PL}&index=3": ("video", VID, PL, 0),
    f"https://www.youtube.com/playlist?list={PL}": ("playlist", "", PL, 0),
    f"https://www.youtube.com/embed/videoseries?list={PL}": ("playlist", "", PL, 0),
    f"https://m.youtube.com/playlist?list={PL}&feature=share": ("playlist", "", PL, 0),
}

REFUSED = {
    "": "Paste a YouTube link",
    "https://vimeo.com/76979871": "Only YouTube links",
    f"https://youtube.com.evil.example/watch?v={VID}": "Only YouTube links",
    f"https://user@youtube.com/watch?v={VID}": "Only YouTube links",
    f"javascript:alert('{VID}')": "does not look like",
    f"ftp://youtube.com/watch?v={VID}": "Only YouTube links",
    "https://www.youtube.com/watch?v=short": "incomplete",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ<script>": "does not look like",
    "https://youtu.be/": "Paste the link of a YouTube video or playlist",
    "https://www.youtube.com/@SomeChannel": "Paste the link of a YouTube video or playlist",
    "https://www.youtube.com/results?search_query=trading": "Paste the link of a YouTube video or playlist",
    "https://www.youtube.com/playlist?list=WL": "private lists",
    "https://www.youtube.com/playlist?list=a": "incomplete",
    "https://www.youtube.com/playlist?list=" + "x" * 65: "incomplete",
    "https://www.youtube.com/watch?v=" + VID + " extra": "does not look like",
    "https://www.youtube.com/watch?v=" + "a" * 600: "does not look like",
}


@pytest.mark.parametrize("raw", list(LINKS))
def test_youtube_links_are_understood(raw):
    link = academy.parse_youtube(raw)
    assert (link.kind, link.video_id, link.playlist_id, link.start_sec) == LINKS[raw]


@pytest.mark.parametrize("raw", list(REFUSED))
def test_other_links_are_refused(raw):
    with pytest.raises(academy.AcademyError) as error:
        academy.parse_youtube(raw)
    assert REFUSED[raw] in str(error.value)


def test_players_use_the_privacy_enhanced_domain():
    video = academy.parse_youtube(f"https://www.youtube.com/watch?v={VID}&list={PL}&t=90")
    assert academy.embed_url(video) == f"https://www.youtube-nocookie.com/embed/{VID}?rel=0&list={PL}&start=90"
    assert academy.watch_url(video) == f"https://www.youtube.com/watch?v={VID}&list={PL}&t=90s"
    playlist = academy.parse_youtube(f"https://www.youtube.com/playlist?list={PL}")
    assert academy.embed_url(playlist) == f"https://www.youtube-nocookie.com/embed/videoseries?list={PL}&rel=0"
    assert academy.watch_url(playlist) == f"https://www.youtube.com/playlist?list={PL}"
    assert academy.thumbnail_url(video) == f"https://i.ytimg.com/vi/{VID}/mqdefault.jpg"
    assert academy.thumbnail_url(playlist, "https://i.ytimg.com/vi/abc/hqdefault.jpg").startswith("https://i.ytimg")
    assert academy.thumbnail_url(playlist, "https://evil.example/x.jpg") == ""


def test_very_long_start_times_are_capped():
    link = academy.parse_youtube(f"https://youtu.be/{VID}?t=99999999")
    assert link.start_sec == academy.MAX_START_SEC


# --------------------------------------------------------------------------------- API


def add(client, url=f"https://youtu.be/{VID}", topic="psychology", **extra):
    response = client.post("/api/academy/videos", json={"url": url, "topic": topic, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_an_empty_academy_lists_the_four_topics_with_youtube_searches(client):
    data = client.get("/api/academy").json()
    assert [t["key"] for t in data["topics"]] == ["psychology", "risk", "strategies", "discipline"]
    assert [t["label"] for t in data["topics"]] == ["Psychology", "Risk Management", "Strategies", "Discipline"]
    assert data["videos"] == [] and data["progress"] == {"total": 0, "completed": 0, "pct": 0.0}
    assert all(t["search_url"].startswith("https://www.youtube.com/results?search_query=") for t in data["topics"])


def test_adding_a_video_with_a_title_given_by_the_trader(client):
    video = add(client, f"https://www.youtube.com/watch?v={VID}&t=45", "risk", title="Position sizing", channel="Me")
    assert video["title"] == "Position sizing" and video["channel"] == "Me" and video["topic"] == "risk"
    assert video["kind"] == "video" and video["start_sec"] == 45 and video["completed"] is False
    assert video["embed_url"] == f"https://www.youtube-nocookie.com/embed/{VID}?rel=0&start=45"
    assert video["notes"] == "" and video["sort_order"] == 0
    assert client.get("/api/academy").json()["videos"] == [video]


def test_titles_are_looked_up_on_youtube_when_left_blank(client, monkeypatch):
    seen = []

    def fake_oembed(url):
        seen.append(url)
        return {
            "title": "  The   risk\nlesson ",
            "author_name": "Coach",
            "thumbnail_url": "https://i.ytimg.com/vi/x/hq.jpg",
        }

    monkeypatch.setattr(academy, "fetch_oembed", fake_oembed)
    video = add(client)
    assert video["title"] == "The risk lesson" and video["channel"] == "Coach"
    assert seen == [f"https://www.youtube.com/watch?v={VID}"]
    playlist = add(client, f"https://www.youtube.com/playlist?list={PL}", "strategies")
    assert playlist["thumbnail_url"] == "https://i.ytimg.com/vi/x/hq.jpg"


def test_offline_the_item_gets_a_generic_title(client):
    assert add(client)["title"] == "YouTube video"
    assert add(client, f"https://www.youtube.com/playlist?list={PL}")["title"] == "YouTube playlist"
    update_settings(client, language="it")
    other = "https://youtu.be/aaaaaaaaaaa"
    assert add(client, other)["title"] == "Video di YouTube"


def test_odd_oembed_answers_are_ignored(client, monkeypatch):
    monkeypatch.setattr(academy, "fetch_oembed", lambda url: {"title": 42, "author_name": ["x"], "thumbnail_url": 3})
    video = add(client, f"https://www.youtube.com/playlist?list={PL}")
    assert video["title"] == "YouTube playlist" and video["channel"] == "" and video["thumbnail_url"] == ""


def test_preview_shows_the_link_before_it_is_saved(client, monkeypatch):
    monkeypatch.setattr(academy, "fetch_oembed", lambda url: {"title": "Discipline 101", "author_name": "A"})
    preview = client.post("/api/academy/preview", json={"url": f"youtu.be/{VID}?t=10"}).json()
    assert preview["kind"] == "video" and preview["title"] == "Discipline 101" and preview["start_sec"] == 10
    assert preview["duplicate"] is None and client.get("/api/academy").json()["videos"] == []
    add(client)
    again = client.post("/api/academy/preview", json={"url": f"https://www.youtube.com/watch?v={VID}"}).json()
    assert again["duplicate"]["title"] == "Discipline 101"
    refused = client.post("/api/academy/preview", json={"url": "https://vimeo.com/1"})
    assert refused.status_code == 422 and "Only YouTube" in refused.json()["detail"]


def test_the_same_video_cannot_be_added_twice(client):
    add(client, title="First")
    twice = client.post("/api/academy/videos", json={"url": f"https://www.youtube.com/watch?v={VID}", "topic": "risk"})
    assert twice.status_code == 409 and "First (Psychology)" in twice.json()["detail"]
    # The same video inside a playlist, or the playlist itself, are other items.
    add(client, f"https://www.youtube.com/watch?v={VID}&list={PL}", "risk")
    add(client, f"https://www.youtube.com/playlist?list={PL}", "risk")


def test_unknown_topics_and_bad_links_are_refused(client):
    assert (
        client.post("/api/academy/videos", json={"url": f"https://youtu.be/{VID}", "topic": "crypto"}).status_code
        == 422
    )
    refused = client.post("/api/academy/videos", json={"url": "https://example.com/v", "topic": "risk"})
    assert refused.status_code == 422 and "Only YouTube" in refused.json()["detail"]
    too_long = {"url": f"https://youtu.be/{VID}", "topic": "risk", "title": "x" * 201}
    assert client.post("/api/academy/videos", json=too_long).status_code == 422


def test_editing_moves_a_video_to_another_topic_and_link(client):
    video = add(client, title="Old title")
    add(client, "https://youtu.be/bbbbbbbbbbb", "risk", title="Risk one")
    edited = client.put(
        f"/api/academy/videos/{video['id']}",
        json={"url": "https://youtu.be/ccccccccccc?t=5", "topic": "risk", "title": "", "description": "Why"},
    ).json()
    assert edited["topic"] == "risk" and edited["sort_order"] == 1 and edited["video_id"] == "ccccccccccc"
    assert edited["title"] == "YouTube video"  # another video, offline: not the old video's title
    assert edited["description"] == "Why" and edited["start_sec"] == 5
    clash = client.put(
        f"/api/academy/videos/{video['id']}", json={"url": "https://youtu.be/bbbbbbbbbbb", "topic": "risk"}
    )
    assert clash.status_code == 409
    assert (
        client.put("/api/academy/videos/999", json={"url": f"https://youtu.be/{VID}", "topic": "risk"}).status_code
        == 404
    )


def test_a_new_link_without_a_typed_title_gets_its_own_title(client, monkeypatch):
    video = add(client, title="Old lesson", channel="Old channel")
    monkeypatch.setattr(academy, "fetch_oembed", lambda url: {"title": "New lesson", "author_name": "New channel"})
    edited = client.put(
        f"/api/academy/videos/{video['id']}", json={"url": "https://youtu.be/ccccccccccc", "topic": "psychology"}
    )
    assert edited.json()["title"] == "New lesson" and edited.json()["channel"] == "New channel"
    kept = client.put(
        f"/api/academy/videos/{video['id']}", json={"url": "https://youtu.be/ccccccccccc", "topic": "risk"}
    )
    assert kept.json()["title"] == "New lesson"  # same link: the title stays


def test_progress_counts_completed_videos_per_topic(client):
    first = add(client)
    add(client, "https://youtu.be/bbbbbbbbbbb")
    add(client, "https://youtu.be/ccccccccccc", "discipline")
    done = client.put(f"/api/academy/videos/{first['id']}/progress", json={"completed": True}).json()
    assert done["completed"] is True and done["completed_at"]
    again = client.put(f"/api/academy/videos/{first['id']}/progress", json={"completed": True}).json()
    assert again["completed_at"] == done["completed_at"]  # marking twice keeps the first date
    data = client.get("/api/academy").json()
    psychology = next(t for t in data["topics"] if t["key"] == "psychology")
    assert (psychology["total"], psychology["completed"], psychology["pct"]) == (2, 1, 50.0)
    assert data["progress"] == {"total": 3, "completed": 1, "pct": 33.3}
    undone = client.put(f"/api/academy/videos/{first['id']}/progress", json={"completed": False}).json()
    assert undone["completed"] is False and undone["completed_at"] is None


def test_private_notes_are_saved_per_video(client):
    first = add(client)
    second = add(client, "https://youtu.be/bbbbbbbbbbb")
    notes = "Wait for the close.\nNever move the stop."
    assert client.put(f"/api/academy/videos/{first['id']}/notes", json={"notes": notes}).json()["notes"] == notes
    videos = {v["id"]: v for v in client.get("/api/academy").json()["videos"]}
    assert videos[first["id"]]["notes"] == notes and videos[second["id"]]["notes"] == ""
    too_long = {"notes": "x" * (academy.NOTES_MAX + 1)}
    assert client.put(f"/api/academy/videos/{first['id']}/notes", json=too_long).status_code == 422
    assert client.put("/api/academy/videos/999/notes", json={"notes": ""}).status_code == 404


def test_videos_are_reordered_within_their_topic(client):
    a, b, c = (add(client, f"https://youtu.be/{x * 11}") for x in "abc")
    other = add(client, "https://youtu.be/ddddddddddd", "risk")
    data = client.put("/api/academy/order", json={"topic": "psychology", "ids": [c["id"], a["id"], b["id"]]}).json()
    assert [v["id"] for v in data["videos"] if v["topic"] == "psychology"] == [c["id"], a["id"], b["id"]]
    for ids in ([c["id"], a["id"]], [a["id"], a["id"], b["id"]], [a["id"], b["id"], c["id"], other["id"]]):
        assert client.put("/api/academy/order", json={"topic": "psychology", "ids": ids}).status_code == 422


def test_deleting_a_video(client):
    video = add(client)
    assert client.delete(f"/api/academy/videos/{video['id']}").status_code == 204
    assert client.get("/api/academy").json()["videos"] == []
    assert client.delete(f"/api/academy/videos/{video['id']}").status_code == 404
    assert add(client)["id"] > video["id"]  # ids are never reused


def test_topics_follow_the_language(client):
    update_settings(client, language="it")
    labels = [t["label"] for t in client.get("/api/academy").json()["topics"]]
    assert labels == ["Psicologia", "Gestione del rischio", "Strategie", "Disciplina"]
    refused = client.post("/api/academy/videos", json={"url": "https://vimeo.com/1", "topic": "risk"})
    assert refused.json()["detail"] == "Puoi aggiungere solo link di YouTube (youtube.com o youtu.be)"


def test_the_academy_and_its_notes_are_in_backups(client):
    from trading_os.services.backup import SCHEMA_VERSION

    video = add(client, title="Keep me")
    client.put(f"/api/academy/videos/{video['id']}/notes", json={"notes": "my note"})
    client.put(f"/api/academy/videos/{video['id']}/progress", json={"completed": True})
    backup = client.get("/api/export/backup.json").json()
    assert backup["schema_version"] == SCHEMA_VERSION
    assert backup["tables"]["academy_videos"][0]["notes"] == "my note"
    client.delete(f"/api/academy/videos/{video['id']}")
    restored = client.post("/api/import", files={"file": ("b.json", json.dumps(backup), "application/json")})
    assert restored.status_code == 200, restored.text
    [back] = client.get("/api/academy").json()["videos"]
    assert back["title"] == "Keep me" and back["notes"] == "my note" and back["completed"] is True


def test_a_backup_from_before_the_academy_restores_with_an_empty_academy(client):
    add(client)
    backup = client.get("/api/export/backup.json").json()
    backup["schema_version"] = 5
    del backup["tables"]["academy_videos"]
    del backup["tables"]["learner_sessions"]
    restored = client.post("/api/import", files={"file": ("b.json", json.dumps(backup), "application/json")})
    assert restored.status_code == 200, restored.text
    assert client.get("/api/academy").json()["videos"] == []


# ---------------------------------------------------------------------------------- browser


def test_the_academy_page_plays_completes_and_keeps_notes(browser_page):  # noqa: F811
    base, page = browser_page
    for vid, title in ((VID, "Stop revenge trading"), ("aaaaaaaaaaa", "Fear and greed")):
        body = {"url": f"https://youtu.be/{vid}", "topic": "psychology", "title": title, "channel": "Coach"}
        assert page.request.post(f"{base}/api/academy/videos", data=body).status == 201
    page.goto(f"{base}/#/academy?topic=psychology")
    frame = page.wait_for_selector(".ac-stage iframe")
    assert frame.get_attribute("src") == f"https://www.youtube-nocookie.com/embed/{VID}?rel=0"
    assert page.inner_text(".ac-stage-title") == "Stop revenge trading"

    page.evaluate("window.player = document.querySelector('.ac-stage iframe')")
    page.click(".ac-stage-actions button[aria-pressed='false']")
    page.wait_for_selector(".ac-stage-actions button[aria-pressed='true']")
    assert page.evaluate("document.querySelector('.ac-stage iframe') === window.player")  # the video keeps playing
    assert "1 of 2 lessons completed" in page.inner_text(".ac-summary")

    page.fill(".ac-notes", "Wait for the close.")
    page.wait_for_function("document.querySelector('.ac-notes-status').textContent === 'Saved'")
    saved = page.request.get(f"{base}/api/academy").json()["videos"]
    assert [v["notes"] for v in saved] == ["Wait for the close.", ""]

    page.click("text=Next lesson")
    page.wait_for_function("document.querySelector('.ac-stage-title').textContent === 'Fear and greed'")
    assert page.url.endswith(f"video={saved[1]['id']}")
    assert page.input_value(".ac-notes") == ""
