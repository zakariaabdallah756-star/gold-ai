"""Trading Academy: a library of YouTube lessons by topic, with progress and private notes.

``parse_youtube`` turns any YouTube link the trader pastes (watch, youtu.be, shorts, live, embed, playlist,
with or without ``https://``) into ids; only YouTube hosts are accepted and ids are checked character by
character, so a stored item can only ever embed ``https://www.youtube-nocookie.com/embed/...``. The title is
looked up once with YouTube's oEmbed endpoint when the trader leaves it blank (best effort, short timeout:
offline the item gets a generic title). Notes never leave this computer.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AcademyVideo
from .i18n import N_, tr

TOPICS = ("psychology", "risk", "strategies", "discipline")
TOPIC_LABELS = {
    "psychology": N_("Psychology"),
    "risk": N_("Risk Management"),
    "strategies": N_("Strategies"),
    "discipline": N_("Discipline"),
}
# What to look for on YouTube when a topic is still empty (the trader picks the videos: none are suggested).
TOPIC_SEARCHES = {
    "psychology": N_("trading psychology"),
    "risk": N_("trading risk management position sizing"),
    "strategies": N_("trading strategy backtest"),
    "discipline": N_("trading discipline routine journal"),
}
KINDS = ("video", "playlist")
TITLE_MAX, CHANNEL_MAX, DESCRIPTION_MAX, NOTES_MAX, URL_MAX = 200, 120, 2000, 20000, 500
MAX_START_SEC = 24 * 3600

YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
PRIVATE_LISTS = frozenset({"WL", "LL"})  # Watch later / Liked videos: only their owner can play them
THUMBNAIL = re.compile(r"^https://i\d?\.ytimg\.com/[A-Za-z0-9_./-]+$")
OEMBED_URL = "https://www.youtube.com/oembed"
OEMBED_TIMEOUT_SEC = 4
OEMBED_MAX_BYTES = 64 * 1024


class AcademyError(ValueError):
    """A link or an item that cannot be saved (the message is shown to the trader)."""


@dataclass(frozen=True)
class YouTubeLink:
    kind: str  # video | playlist
    video_id: str = ""
    playlist_id: str = ""
    start_sec: int = 0


# ------------------------------------------------------------------------------ links


def _seconds(value: str) -> int:
    """'90', '90s', '1m30s', '1h2m3s' -> seconds (0 when unreadable)."""
    value = (value or "").strip().lower()
    if value.isdigit():
        return min(int(value), MAX_START_SEC)
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value)
    if not match or not any(match.groups()):
        return 0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return min(hours * 3600 + minutes * 60 + seconds, MAX_START_SEC)


def parse_youtube(raw: str) -> YouTubeLink:
    """The video or playlist a YouTube link points to. Raises :class:`AcademyError`."""
    text = (raw or "").strip()
    if not text:
        raise AcademyError(tr("Paste a YouTube link"))
    if len(text) > URL_MAX or re.search(r"[\s<>\"']", text):
        raise AcademyError(tr("That does not look like a YouTube link"))
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ("http", "https") or host not in YOUTUBE_HOSTS or parts.username:
        raise AcademyError(tr("Only YouTube links can be added (youtube.com or youtu.be)"))
    query = parse_qs(parts.query)

    def first(name: str) -> str:
        return (query.get(name) or [""])[0].strip()

    path = [part for part in parts.path.split("/") if part]
    video_id = ""
    if host == "youtu.be":
        video_id = path[0] if path else ""
    elif path and path[0] in ("embed", "shorts", "live", "v", "e"):
        video_id = path[1] if len(path) > 1 else ""
    elif path[:1] == ["watch"] or not path:
        video_id = first("v")
    if video_id == "videoseries":  # /embed/videoseries?list=... is a playlist player
        video_id = ""
    playlist_id = first("list")
    if playlist_id in PRIVATE_LISTS:
        raise AcademyError(tr("Watch later and Liked videos are private lists: share a public playlist instead"))
    if playlist_id and not PLAYLIST_ID.match(playlist_id):
        raise AcademyError(tr("That YouTube playlist link is incomplete"))
    if video_id:
        if not VIDEO_ID.match(video_id):
            raise AcademyError(tr("That YouTube video link is incomplete"))
        start = _seconds(first("t") or first("start"))
        return YouTubeLink("video", video_id=video_id, playlist_id=playlist_id, start_sec=start)
    if playlist_id:
        return YouTubeLink("playlist", playlist_id=playlist_id)
    raise AcademyError(tr("Paste the link of a YouTube video or playlist (not a channel or search page)"))


def watch_url(link: YouTubeLink) -> str:
    """The page on YouTube itself."""
    if link.kind == "playlist":
        return f"https://www.youtube.com/playlist?list={link.playlist_id}"
    query = {"v": link.video_id}
    if link.playlist_id:
        query["list"] = link.playlist_id
    if link.start_sec:
        query["t"] = f"{link.start_sec}s"
    return f"https://www.youtube.com/watch?{urlencode(query)}"


def embed_url(link: YouTubeLink) -> str:
    """The privacy-enhanced player (youtube-nocookie.com: no cookies until the trader presses play)."""
    if link.kind == "playlist":
        return f"https://www.youtube-nocookie.com/embed/videoseries?{urlencode({'list': link.playlist_id, 'rel': 0})}"
    query: dict[str, str | int] = {"rel": 0}
    if link.playlist_id:
        query["list"] = link.playlist_id
    if link.start_sec:
        query["start"] = link.start_sec
    return f"https://www.youtube-nocookie.com/embed/{link.video_id}?{urlencode(query)}"


def thumbnail_url(link: YouTubeLink, looked_up: str = "") -> str:
    if link.video_id:
        return f"https://i.ytimg.com/vi/{link.video_id}/mqdefault.jpg"
    return looked_up if THUMBNAIL.match(looked_up or "") else ""


def link_of(video: AcademyVideo) -> YouTubeLink:
    return YouTubeLink(video.kind, video.video_id, video.playlist_id, video.start_sec)


# ---------------------------------------------------------------------------- oEmbed


def fetch_oembed(url: str) -> dict | None:
    """YouTube's public oEmbed data (title, author_name, thumbnail_url) for a watch or playlist page.

    Best effort: None when offline, slow, private or unexpected. Tests replace this function.
    """
    request = urllib.request.Request(
        f"{OEMBED_URL}?url={quote(url, safe='')}&format=json", headers={"User-Agent": "TradingOS/1.6"}
    )
    try:
        with urllib.request.urlopen(request, timeout=OEMBED_TIMEOUT_SEC) as response:  # noqa: S310 - fixed host
            if response.status != 200:
                return None
            data = json.loads(response.read(OEMBED_MAX_BYTES + 1)[:OEMBED_MAX_BYTES].decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def lookup(link: YouTubeLink) -> dict:
    """{title, channel, thumbnail_url} from oEmbed; empty strings for what is unknown."""
    data = fetch_oembed(watch_url(link)) or {}

    def text(key: str, limit: int) -> str:
        value = data.get(key)
        return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""

    return {
        "title": text("title", TITLE_MAX),
        "channel": text("author_name", CHANNEL_MAX),
        "thumbnail_url": thumbnail_url(link, text("thumbnail_url", 300)),
    }


def default_title(link: YouTubeLink) -> str:
    return tr("YouTube playlist") if link.kind == "playlist" else tr("YouTube video")


# --------------------------------------------------------------------------- library


def topic_label(topic: str) -> str:
    return tr(TOPIC_LABELS.get(topic, topic))


def ordered(db: Session) -> list[AcademyVideo]:
    """Topic by topic (in ``TOPICS`` order), then in the trader's order within the topic."""
    rank = {topic: index for index, topic in enumerate(TOPICS)}
    videos = db.scalars(select(AcademyVideo))
    return sorted(videos, key=lambda v: (rank.get(v.topic, len(rank)), v.sort_order, v.id))


def find_duplicate(db: Session, link: YouTubeLink, exclude_id: int | None = None) -> AcademyVideo | None:
    query = select(AcademyVideo).where(
        AcademyVideo.kind == link.kind,
        AcademyVideo.video_id == link.video_id,
        AcademyVideo.playlist_id == link.playlist_id,
    )
    if exclude_id is not None:
        query = query.where(AcademyVideo.id != exclude_id)
    return db.scalar(query.limit(1))


def next_order(db: Session, topic: str) -> int:
    highest = db.scalar(select(func.max(AcademyVideo.sort_order)).where(AcademyVideo.topic == topic))
    return 0 if highest is None else highest + 1


def apply_link(video: AcademyVideo, link: YouTubeLink) -> None:
    video.kind, video.video_id, video.playlist_id, video.start_sec = (
        link.kind,
        link.video_id,
        link.playlist_id,
        link.start_sec,
    )


def set_completed(video: AcademyVideo, completed: bool, now: datetime) -> None:
    if completed and not video.completed:
        video.completed_at = now
    elif not completed:
        video.completed_at = None
    video.completed = completed


def video_out(video: AcademyVideo) -> dict:
    link = link_of(video)
    return {
        "id": video.id,
        "topic": video.topic,
        "kind": video.kind,
        "video_id": video.video_id,
        "playlist_id": video.playlist_id,
        "start_sec": video.start_sec,
        "title": video.title,
        "channel": video.channel,
        "description": video.description,
        "thumbnail_url": thumbnail_url(link, video.thumbnail_url),
        "notes": video.notes,
        "completed": video.completed,
        "completed_at": video.completed_at.isoformat() if video.completed_at else None,
        "sort_order": video.sort_order,
        "watch_url": watch_url(link),
        "embed_url": embed_url(link),
    }


def _progress(videos: list[AcademyVideo]) -> dict:
    total = len(videos)
    done = sum(1 for v in videos if v.completed)
    return {"total": total, "completed": done, "pct": round(done / total * 100, 1) if total else 0.0}


def library(db: Session) -> dict:
    videos = ordered(db)
    topics = []
    for key in TOPICS:
        items = [v for v in videos if v.topic == key]
        topics.append(
            {
                "key": key,
                "label": topic_label(key),
                "search_url": "https://www.youtube.com/results?" + urlencode({"search_query": tr(TOPIC_SEARCHES[key])}),
                **_progress(items),
            }
        )
    return {"topics": topics, "videos": [video_out(v) for v in videos], "progress": _progress(videos)}
