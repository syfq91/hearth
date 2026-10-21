"""Wire metadata providers, the matcher and the artwork pipeline together.

Keeping construction in one place means the scan job handler, the manual match
endpoint and any future caller all get the same matcher, including the image
pipeline as its artwork callback.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.images.pipeline import ImagePipeline
from app.services.metadata.matching import MetadataMatcher


def build_metadata_matcher(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> MetadataMatcher:
    """Build a matcher whose artwork callback is the shared image pipeline."""
    artwork = ImagePipeline(settings, sessionmaker)
    return MetadataMatcher(settings, sessionmaker, artwork=artwork)
