"""Hearth command-line interface.

The API and worker share one image and one settings source; only the command
differs (``hearth api`` vs ``hearth worker``).
"""

import asyncio
import logging
import signal
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.db.migrate import run_migrations, run_migrations_async
from app.db.session import build_engine, build_sessionmaker
from app.services.backup import create_backup, restore_backup
from app.services.jobs.worker import JobWorker
from app.services.scanner.scheduler import ScanScheduler
from app.services.scanner.watcher import LibraryWatcher

logger = logging.getLogger("hearth.cli")

app = typer.Typer(
    name="hearth",
    help="Self-hosted personal media server.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def api(
    host: Annotated[
        str | None,
        typer.Option(help="Bind address; defaults to HEARTH_HOST."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(help="Bind port; defaults to HEARTH_PORT."),
    ] = None,
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Reload on source changes (development only)."),
    ] = False,
) -> None:
    """Run the HTTP API with uvicorn."""
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(
        "starting api",
        extra={
            "host": host or settings.host,
            "port": port or settings.port,
            "env": settings.env,
            "reload": reload,
        },
    )

    uvicorn.run(
        "app.main:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
        # Keep our JSON logging configuration; the request middleware logs
        # every request, so uvicorn's access log would duplicate it.
        log_config=None,
        access_log=False,
    )


async def _run_worker(settings: Settings) -> None:
    """Run the durable job worker, scan scheduler and filesystem watcher."""
    await run_migrations_async(settings)
    engine = build_engine(settings)
    try:
        sessionmaker = build_sessionmaker(engine)
        worker = JobWorker(settings, sessionmaker)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        tasks = [asyncio.create_task(worker.run(stop), name="job-worker")]
        if settings.scan_scheduler_enabled:
            tasks.append(
                asyncio.create_task(
                    ScanScheduler(settings, sessionmaker).run(stop), name="scan-scheduler"
                )
            )
        if settings.scan_watch_enabled:
            tasks.append(
                asyncio.create_task(
                    LibraryWatcher(settings, sessionmaker).run(stop), name="library-watcher"
                )
            )

        logger.info(
            "worker started",
            extra={
                "env": settings.env,
                "scheduler": settings.scan_scheduler_enabled,
                "watcher": settings.scan_watch_enabled,
            },
        )
        try:
            await asyncio.gather(*tasks)
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await engine.dispose()
    logger.info("worker stopped")


@app.command()
def worker() -> None:
    """Run the background job worker."""
    settings = get_settings()
    setup_logging(settings.log_level)
    asyncio.run(_run_worker(settings))


@app.command()
def migrate() -> None:
    """Apply database migrations and exit."""
    settings = get_settings()
    setup_logging(settings.log_level)
    run_migrations(settings)
    logger.info("migrations applied", extra={"db_path": str(settings.db_path)})


@app.command()
def create_admin(
    username: Annotated[
        str | None,
        typer.Option(help="Admin username; prompted in Phase 1."),
    ] = None,
) -> None:
    """Create the first administrator (implemented in Phase 1)."""
    logger.info(
        "create-admin is not implemented until Phase 1",
        extra={"username": username},
    )
    typer.echo("create-admin is not implemented until Phase 1.", err=True)
    raise typer.Exit(code=1)


@app.command()
def backup(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Destination path for backup tar.gz archive."),
    ] = None,
) -> None:
    """Create a consistent online SQLite backup and metadata archive."""
    settings = get_settings()
    setup_logging(settings.log_level)
    target = create_backup(settings, destination_path=output)
    typer.echo(f"Backup created successfully: {target}")


@app.command()
def restore(
    input_file: Annotated[
        Path,
        typer.Option(
            "--input",
            "-i",
            help="Path to backup tar.gz archive to restore from.",
            exists=True,
        ),
    ],
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force restore even if schema version differs."),
    ] = False,
) -> None:
    """Restore database from a backup tar.gz archive."""
    settings = get_settings()
    setup_logging(settings.log_level)
    metadata = restore_backup(input_file, settings, force=force)
    schema = metadata.get("schema_version", "unknown")
    typer.echo(f"Database successfully restored from {input_file} (schema: {schema})")
