"""Schema-level tests for the Phase 1 models."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    Job,
    Library,
    LibraryAccess,
    LibraryPath,
    RefreshToken,
    Setting,
    User,
)


async def test_user_defaults_and_relationships(db_session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    db_session.add(user)
    await db_session.flush()

    assert user.id
    assert user.role == "user"
    assert user.disabled is False
    assert user.allow_transcode is True
    assert user.token_version == 0
    assert user.created_at.tzinfo is not None


async def test_library_paths_and_access_cascade(db_session: AsyncSession) -> None:
    user = User(username="bob", password_hash="hash")
    library = Library(name="Movies", type="movie")
    library.paths.append(LibraryPath(path="/media/movies"))
    library.access.append(LibraryAccess(user=user))
    db_session.add(library)
    await db_session.commit()

    loaded = await db_session.scalar(select(Library).where(Library.name == "Movies"))
    assert loaded is not None
    assert [path.path for path in loaded.paths] == ["/media/movies"]
    assert loaded.access[0].user.username == "bob"

    await db_session.delete(loaded)
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(LibraryPath)) == 0
    assert await db_session.scalar(select(func.count()).select_from(LibraryAccess)) == 0
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1


async def test_refresh_token_and_utc_timestamps(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    user = User(username="carol", password_hash="hash")
    db_session.add(user)
    await db_session.flush()
    token = RefreshToken(
        user_id=user.id,
        family_id="family",
        token_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(token)
    await db_session.commit()
    db_session.expunge_all()

    loaded = await db_session.scalar(select(RefreshToken))
    assert loaded is not None
    assert loaded.expires_at.tzinfo is not None


async def test_job_defaults(db_session: AsyncSession) -> None:
    job = Job(type="noop", payload={"value": 1})
    db_session.add(job)
    await db_session.flush()

    assert job.status == "pending"
    assert job.progress == 0.0
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.cancel_requested is False
    assert job.run_after.tzinfo is not None


async def test_setting_and_audit_round_trip(db_session: AsyncSession) -> None:
    db_session.add(Setting(key="scan.interval", value="6"))
    db_session.add(AuditLog(action="library.create", target_type="library", details={"name": "TV"}))
    await db_session.commit()

    setting = await db_session.get(Setting, "scan.interval")
    audit = await db_session.scalar(select(AuditLog))
    assert setting is not None and setting.value == "6"
    assert audit is not None and audit.details == {"name": "TV"}
    assert audit.created_at.tzinfo is not None
