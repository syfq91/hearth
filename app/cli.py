"""Hearth command-line interface.

The API and worker share one image and one settings source; only the command
differs (``hearth api`` vs ``hearth worker``).
"""

import asyncio
import logging
import signal
from typing import Annotated

import typer
import uvicorn

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.db.migrate import run_migrations, run_migrations_async
from app.db.session import build_engine, build_sessionmaker
from app.services.jobs.worker import JobWorker

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
    """Run the durable job worker until SIGINT/SIGTERM."""
    await run_migrations_async(settings)
    engine = build_engine(settings)
    try:
        worker = JobWorker(settings, build_sessionmaker(engine))
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        logger.info("worker started", extra={"env": settings.env})
        await worker.run(stop)
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
