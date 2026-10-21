"""Administrator metadata search and manual matching endpoints."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.deps import RequireAdmin, SessionmakerDep, SettingsDep
from app.core.errors import BadGatewayError, NotFoundError, ValidationError
from app.db.models import MediaItem
from app.db.session import SessionDep
from app.services.audit import record_audit
from app.services.metadata.base import ProviderError, SearchResult
from app.services.metadata.factory import build_metadata_matcher
from app.services.metadata.matching import MetadataMatcher

router = APIRouter(tags=["metadata"])


def build_matcher(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> MetadataMatcher:
    """Construct the matcher for a request; overridable in tests."""
    return build_metadata_matcher(settings, sessionmaker)


class SearchResultResponse(BaseModel):
    """One candidate returned to an administrator."""

    model_config = ConfigDict(from_attributes=True)

    provider: str
    provider_id: str
    title: str
    year: int | None = None
    overview: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    popularity: float = 0.0


class MatchRequest(BaseModel):
    """A specific candidate chosen by an administrator."""

    provider: str = Field(min_length=1, max_length=32)
    provider_id: str = Field(min_length=1, max_length=128)


class MatchResponse(BaseModel):
    """The result of a manual match."""

    item_id: str
    state: str
    provider: str | None = None
    provider_id: str | None = None
    score: float | None = None
    error: str | None = None


@router.get("/metadata/search")
async def search_metadata(
    actor: RequireAdmin,
    settings: SettingsDep,
    sessionmaker: SessionmakerDep,
    query: Annotated[str, Query(min_length=1, max_length=200)],
    year: Annotated[int | None, Query(ge=1800, le=2100)] = None,
    provider: Annotated[str | None, Query(max_length=32)] = None,
) -> list[SearchResultResponse]:
    """Search configured metadata providers for a manual match."""

    matcher = build_matcher(settings, sessionmaker)
    try:
        if provider is not None and provider not in {p.name for p in matcher.providers}:
            raise ValidationError(
                f"Unknown metadata provider {provider!r}", code="unknown_provider"
            )
        try:
            results = await matcher.search(query, year)
        except ProviderError as exc:
            raise BadGatewayError(str(exc), code="metadata_provider_error") from None
    finally:
        await matcher.close()

    if provider is not None:
        results = [result for result in results if result.provider == provider]
    return [_search_response(result) for result in results]


@router.post("/items/{item_id}/match")
async def manual_match(
    item_id: str,
    payload: MatchRequest,
    actor: RequireAdmin,
    settings: SettingsDep,
    sessionmaker: SessionmakerDep,
    session: SessionDep,
) -> MatchResponse:
    """Attach a specific provider candidate to an item, bypassing scoring."""

    item = await session.get(MediaItem, item_id)
    if item is None:
        raise NotFoundError("Item not found", code="item_not_found")

    matcher = build_matcher(settings, sessionmaker)
    try:
        try:
            result = await matcher.manual_match(item_id, payload.provider, payload.provider_id)
        except ProviderError as exc:
            raise BadGatewayError(str(exc), code="metadata_provider_error") from None
    finally:
        await matcher.close()

    if result.error == "Item not found":
        raise NotFoundError("Item not found", code="item_not_found")

    record_audit(
        session,
        actor_id=actor.id,
        action="item.match",
        target_type="item",
        target_id=item_id,
        details={
            "provider": payload.provider,
            "provider_id": payload.provider_id,
            "state": result.state,
        },
    )
    await session.commit()
    return MatchResponse(
        item_id=result.item_id,
        state=result.state,
        provider=result.provider,
        provider_id=result.provider_id,
        score=result.score,
        error=result.error,
    )


def _search_response(result: SearchResult) -> SearchResultResponse:
    return SearchResultResponse(
        provider=result.provider,
        provider_id=result.provider_id,
        title=result.title,
        year=result.year,
        overview=result.overview,
        poster_url=result.poster_url,
        backdrop_url=result.backdrop_url,
        popularity=result.popularity,
    )
