"""Generic CRUD router used for the simple trading-plan entities.

This module intentionally does not use ``from __future__ import annotations``:
FastAPI must see the real schema classes captured by the factory closure.
"""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .deps import DbDep, get_or_404


def _commit(db: Session, label: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"A {label} with that name already exists") from exc


def crud_router(
    model,
    schema_in,
    schema_out,
    *,
    path: str,
    label: str,
    order_by,
    on_update: Callable | None = None,
    on_delete: Callable | None = None,
) -> APIRouter:
    """Build list/create/update/delete endpoints under ``/api/{path}``.

    ``on_update(db, obj, before)`` runs after an update is applied (``before`` holds the
    previous values) and ``on_delete(db, obj, force)`` before a delete; it may raise an
    HTTPException (e.g. 409 when the item is still in use and ``force`` is not set).
    """
    router = APIRouter(prefix=f"/api/{path}", tags=["plan"])

    @router.get("", response_model=list[schema_out])
    def list_items(db: DbDep):
        return db.scalars(select(model).order_by(*order_by)).all()

    @router.post("", response_model=schema_out, status_code=201)
    def create_item(payload: schema_in, db: DbDep):
        obj = model(**payload.model_dump())
        db.add(obj)
        _commit(db, label)
        return obj

    @router.put("/{item_id}", response_model=schema_out)
    def update_item(item_id: int, payload: schema_in, db: DbDep):
        obj = get_or_404(db, model, item_id)
        values = payload.model_dump()
        before = {key: getattr(obj, key) for key in values}
        for key, value in values.items():
            setattr(obj, key, value)
        if on_update is not None:
            on_update(db, obj, before)
        _commit(db, label)
        return obj

    @router.delete("/{item_id}", status_code=204)
    def delete_item(item_id: int, db: DbDep, force: bool = Query(False)):
        obj = get_or_404(db, model, item_id)
        if on_delete is not None:
            on_delete(db, obj, force)
        db.delete(obj)
        db.commit()
        return Response(status_code=204)

    return router
