"""Query-count contract for catalog card loading."""

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.items import card_load_options, item_summary
from app.db.models import Episode, Library, LibraryPath, MediaItem, Movie, Series


async def test_card_load_options_do_not_cascade_through_series(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A page of cards pays a fixed number of queries, not one per episode.

    MediaItem declares its subtype relations lazy="selectin". Without an
    explicit cap, loading an episode card walks Series -> seasons -> episodes
    -> item and re-enters the full MediaItem graph for every row.
    """
    async with db_sessionmaker() as session:
        library = Library(
            name="Card load library", type="tv", paths=[LibraryPath(path="/media/cards")]
        )
        session.add(library)
        await session.flush()

        series = Series()
        session.add(
            MediaItem(
                library_id=library.id,
                type="series",
                title="A series",
                sort_title="a series",
                series=series,
            )
        )
        for number in range(30):
            session.add(
                MediaItem(
                    library_id=library.id,
                    type="episode",
                    title=f"Episode {number}",
                    sort_title=f"episode {number}",
                    episode=Episode(series=series, season_number=1, episode_number=number),
                )
            )
        for number in range(10):
            session.add(
                MediaItem(
                    library_id=library.id,
                    type="movie",
                    title=f"Movie {number}",
                    sort_title=f"movie {number}",
                    movie=Movie(),
                )
            )
        await session.commit()
        session.expunge_all()

        statements: list[str] = []

        def record(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        engine = session.bind
        assert engine is not None
        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            items = (await session.scalars(select(MediaItem).options(*card_load_options()))).all()
            summaries = [item_summary(item, None) for item in items]
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record)

        assert len(summaries) == 41
        assert len(statements) < 60
        episodes = [summary for summary in summaries if summary.type == "episode"]
        assert len(episodes) == 30
        assert episodes[0].series_title == "A series"
