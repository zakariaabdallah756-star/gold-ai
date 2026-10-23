"""Trading Academy: YouTube lessons by topic, progress and private notes (v1.6)."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field, StringConstraints, field_validator

from ..models import AcademyVideo
from ..schemas import FiniteModel
from ..services import academy
from ..services.account import local_now
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/academy", tags=["academy"])

Topic = Literal["psychology", "risk", "strategies", "discipline"]
Url = Annotated[str, StringConstraints(max_length=academy.URL_MAX, strip_whitespace=True)]
Title = Annotated[str, StringConstraints(max_length=academy.TITLE_MAX, strip_whitespace=True)]
Channel = Annotated[str, StringConstraints(max_length=academy.CHANNEL_MAX, strip_whitespace=True)]
Description = Annotated[str, StringConstraints(max_length=academy.DESCRIPTION_MAX, strip_whitespace=True)]


def _link(value: str) -> academy.YouTubeLink:
    try:
        return academy.parse_youtube(value)
    except academy.AcademyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class PreviewIn(FiniteModel):
    url: Url


class VideoIn(FiniteModel):
    url: Url
    topic: Topic
    title: Title = ""
    channel: Channel = ""
    description: Description = ""

    @field_validator("title", "channel", "description")
    @classmethod
    def _single_spaces(cls, value: str) -> str:
        return value.replace("\r\n", "\n")


class ProgressIn(FiniteModel):
    completed: bool


class NotesIn(FiniteModel):
    notes: Annotated[str, StringConstraints(max_length=academy.NOTES_MAX)]


class OrderIn(FiniteModel):
    topic: Topic
    ids: list[int] = Field(max_length=1000)


def _conflict(existing: AcademyVideo) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=academy.tr(
            "This one is already in the Academy: {title} ({topic})",
            title=existing.title,
            topic=academy.topic_label(existing.topic),
        ),
    )


@router.get("")
def read_library(db: DbDep):
    """Every topic with its progress, every saved video or playlist (notes included: this is the owner's view)."""
    return academy.library(db)


@router.post("/preview")
def preview(payload: PreviewIn, db: DbDep):
    """What a pasted link is (video or playlist, title, channel) before it is saved."""
    link = _link(payload.url)
    found = academy.lookup(link)
    existing = academy.find_duplicate(db, link)
    return {
        "kind": link.kind,
        "video_id": link.video_id,
        "playlist_id": link.playlist_id,
        "start_sec": link.start_sec,
        "title": found["title"],
        "channel": found["channel"],
        "thumbnail_url": found["thumbnail_url"],
        "watch_url": academy.watch_url(link),
        "embed_url": academy.embed_url(link),
        "duplicate": academy.video_out(existing) if existing else None,
    }


@router.post("/videos", status_code=201)
def add_video(payload: VideoIn, db: DbDep):
    link = _link(payload.url)
    existing = academy.find_duplicate(db, link)
    if existing:
        raise _conflict(existing)
    found = academy.lookup(link) if not (payload.title and payload.channel) else {}
    video = AcademyVideo(
        topic=payload.topic,
        title=payload.title or found.get("title") or academy.default_title(link),
        channel=payload.channel or found.get("channel", ""),
        description=payload.description,
        thumbnail_url=found.get("thumbnail_url", ""),
        sort_order=academy.next_order(db, payload.topic),
    )
    academy.apply_link(video, link)
    db.add(video)
    db.commit()
    return academy.video_out(video)


@router.put("/videos/{video_id}")
def update_video(video_id: int, payload: VideoIn, db: DbDep):
    video = get_or_404(db, AcademyVideo, video_id)
    link = _link(payload.url)
    existing = academy.find_duplicate(db, link, exclude_id=video.id)
    if existing:
        raise _conflict(existing)
    moved = link != academy.link_of(video)
    if moved:
        academy.apply_link(video, link)
        video.thumbnail_url = academy.lookup(link)["thumbnail_url"] if link.kind == "playlist" else ""
    if payload.topic != video.topic:
        video.topic, video.sort_order = payload.topic, academy.next_order(db, payload.topic)
    video.title = payload.title or video.title
    video.channel = payload.channel
    video.description = payload.description
    db.commit()
    return academy.video_out(video)


@router.put("/videos/{video_id}/progress")
def set_progress(video_id: int, payload: ProgressIn, db: DbDep, settings: SettingsDep):
    video = get_or_404(db, AcademyVideo, video_id)
    academy.set_completed(video, payload.completed, local_now(settings))
    db.commit()
    return academy.video_out(video)


@router.put("/videos/{video_id}/notes")
def save_notes(video_id: int, payload: NotesIn, db: DbDep):
    video = get_or_404(db, AcademyVideo, video_id)
    video.notes = payload.notes
    db.commit()
    return {"id": video.id, "notes": video.notes}


@router.delete("/videos/{video_id}", status_code=204)
def delete_video(video_id: int, db: DbDep):
    db.delete(get_or_404(db, AcademyVideo, video_id))
    db.commit()
    return Response(status_code=204)


@router.put("/order")
def reorder(payload: OrderIn, db: DbDep):
    """New order of one topic's videos (every id of the topic, each once)."""
    videos = {v.id: v for v in academy.ordered(db) if v.topic == payload.topic}
    if sorted(payload.ids) != sorted(videos) or len(set(payload.ids)) != len(payload.ids):
        raise HTTPException(
            status_code=422,
            detail=academy.tr("Send every video of {topic} exactly once", topic=academy.topic_label(payload.topic)),
        )
    for order, video_id in enumerate(payload.ids):
        videos[video_id].sort_order = order
    db.commit()
    return academy.library(db)
