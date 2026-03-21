"""Tests for persistence repositories."""

from bot.repositories import (
    AuthorizedUsersRepository,
    DownloadHistoryRepository,
    DownloadRecord,
)


def test_authorized_users_repository_roundtrip(tmp_path):
    repository = AuthorizedUsersRepository(str(tmp_path / "authorized_users.json"))

    repository.save({123, 456})

    assert repository.load() == {123, 456}


def test_download_history_repository_append_and_stats(tmp_path):
    repository = DownloadHistoryRepository(
        str(tmp_path / "download_history.json"),
        max_entries=10,
    )

    repository.append(
        DownloadRecord(
            timestamp="2026-01-01T10:00:00",
            user_id=123,
            title="Example",
            url="https://youtube.com/watch?v=test",
            format="audio_mp3",
            file_size_mb=12.34,
            status="success",
        )
    )
    repository.append(
        DownloadRecord(
            timestamp="2026-01-02T10:00:00",
            user_id=999,
            title="Broken",
            url="https://youtube.com/watch?v=fail",
            format="video_best",
            status="failure",
            error_message="network timeout",
        )
    )

    history = repository.load()
    stats = repository.stats()

    assert len(history) == 2
    assert stats["total_downloads"] == 2
    assert stats["success_count"] == 1
    assert stats["failure_count"] == 1
    assert stats["format_counts"] == {"audio_mp3": 1, "video_best": 1}
