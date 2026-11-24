"""Tests for parsing public provider URLs pasted into manual matching."""

from app.services.metadata.urls import parse_provider_url


def test_tmdb_movie_url_with_slug() -> None:
    parsed = parse_provider_url("https://www.themoviedb.org/movie/603-the-matrix")
    assert parsed is not None
    assert parsed.provider == "tmdb"
    assert parsed.provider_id == "603"
    assert parsed.media_type == "movie"
    assert parsed.season_number is None
    assert parsed.episode_number is None


def test_tmdb_series_season_and_episode_urls() -> None:
    series = parse_provider_url("https://www.themoviedb.org/tv/1396-breaking-bad")
    assert series is not None
    assert (series.provider, series.provider_id, series.media_type) == ("tmdb", "1396", "series")

    season = parse_provider_url("https://themoviedb.org/tv/1396/season/2")
    assert season is not None
    assert (season.provider_id, season.media_type, season.season_number) == ("1396", "season", 2)
    assert season.episode_number is None

    episode = parse_provider_url("https://www.themoviedb.org/tv/1396/season/1/episode/4")
    assert episode is not None
    assert (episode.provider_id, episode.media_type) == ("1396", "episode")
    assert (episode.season_number, episode.episode_number) == (1, 4)


def test_google_books_urls() -> None:
    parsed = parse_provider_url("https://books.google.com/books?id=B1R1CwAAQBAJ&hl=en")
    assert parsed is not None
    assert (parsed.provider, parsed.provider_id, parsed.media_type) == (
        "googlebooks",
        "B1R1CwAAQBAJ",
        "book",
    )

    edition = parse_provider_url("https://books.google.com/books/edition/Dune/B1R1CwAAQBAJ")
    assert edition is not None
    assert edition.provider_id == "B1R1CwAAQBAJ"

    play = parse_provider_url("https://play.google.com/store/books/details?id=B1R1CwAAQBAJ")
    assert play is not None
    assert play.provider_id == "B1R1CwAAQBAJ"


def test_google_books_country_domains() -> None:
    edition = parse_provider_url(
        "https://www.google.com.my/books/edition/The_Cursed_Sword_Master_s_Harem_Life_By_/"
        "EELNEQAAQBAJ"
    )
    assert edition is not None
    assert (edition.provider, edition.provider_id, edition.media_type) == (
        "googlebooks",
        "EELNEQAAQBAJ",
        "book",
    )

    for url in (
        "https://books.google.com.my/books?id=B1R1CwAAQBAJ&hl=en",
        "https://books.google.co.uk/books/edition/Dune/B1R1CwAAQBAJ",
        "https://play.google.com.my/store/books/details?id=B1R1CwAAQBAJ",
    ):
        parsed = parse_provider_url(url)
        assert parsed is not None, url
        assert parsed.provider_id == "B1R1CwAAQBAJ"


def test_openlibrary_urls() -> None:
    work = parse_provider_url("https://openlibrary.org/works/OL102749W/Dune")
    assert work is not None
    assert (work.provider, work.provider_id, work.media_type) == (
        "openlibrary",
        "/works/OL102749W",
        "book",
    )

    edition = parse_provider_url("https://openlibrary.org/books/OL123M")
    assert edition is not None
    assert edition.provider_id == "/books/OL123M"


def test_unsupported_or_malformed_urls_are_rejected() -> None:
    assert parse_provider_url("The Matrix") is None
    assert parse_provider_url("ftp://www.themoviedb.org/movie/603") is None
    assert parse_provider_url("https://example.com/movie/603") is None
    assert parse_provider_url("https://www.themoviedb.org/movie/not-an-id") is None
    assert parse_provider_url("https://www.themoviedb.org/tv/1396/season") is None
    assert parse_provider_url("https://books.google.com/books") is None
    assert parse_provider_url("https://books.google.com.my.evil.test/books?id=B1R1CwAAQBAJ") is None
    assert parse_provider_url("https://books.google.evil.com/books?id=B1R1CwAAQBAJ") is None
    assert parse_provider_url("https://openlibrary.org/") is None
