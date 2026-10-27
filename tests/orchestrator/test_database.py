"""Tests for the SQLite layer (orchestrator.db.database) and the repositories.

They check the DB is opened in WAL mode, migrations run exactly once,
times are stored in a sortable UTC format, and a few repository rules
(version bumps, rolling stats, single-use enrollment tokens).
"""

from datetime import UTC, datetime, timedelta

import pytest

from orchestrator.db.database import (
    from_db_time,
    list_migrations,
    open_database,
    to_db_time,
)
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import workers as workers_repo
from shared.models import EnrollmentToken, MeasurementKind, WorkerCapabilities, WorkerRole

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def test_database_file_is_created_in_wal_mode(tmp_path):
    database = open_database(tmp_path)
    # docs/api.md §8.2: the DB lives at <BVP_DATA_DIR>/db/bvp.sqlite3.
    assert (tmp_path / "db" / "bvp.sqlite3").exists()
    # WAL lets readers work while one writer writes (design §13).
    assert database.journal_mode == "wal"
    database.close()


def test_migrations_run_once_and_record_the_schema_version(tmp_path):
    database = open_database(tmp_path)
    # Every bundled migration file has been applied...
    assert database.schema_version == len(list_migrations())
    # ...and applying again does nothing, because the version is remembered.
    assert database.apply_migrations() == []
    database.close()
    # Re-opening an existing DB keeps the data and runs nothing new.
    reopened = open_database(tmp_path)
    assert reopened.schema_version == len(list_migrations())
    reopened.close()


def test_migration_files_are_numbered_without_gaps():
    numbers = [number for number, _, _ in list_migrations()]
    assert numbers == list(range(1, len(numbers) + 1))


def test_times_round_trip_and_sort_as_text():
    # Stored times must sort correctly as plain strings (SQL compares them so).
    earlier, later = NOW, NOW + timedelta(microseconds=1)
    assert to_db_time(earlier) < to_db_time(later)
    assert from_db_time(to_db_time(NOW)) == NOW
    # A time without a timezone is ambiguous: refuse it loudly.
    with pytest.raises(ValueError):
        to_db_time(datetime(2026, 10, 25, 12, 0))


def insert_sample_job(connection, job_id="j1"):
    jobs_repo.insert_job(
        connection,
        {
            "id": job_id,
            "state": "received",
            "created_at": to_db_time(NOW),
            "updated_at": to_db_time(NOW),
            "message_id": f"msg-{job_id}",
            "received_as": "document",
            "sha256": "0" * 64,
            "size_bytes": 10,
            "raw_extension": ".mp4",
        },
    )


def test_update_job_bumps_the_version_unless_told_not_to(tmp_path):
    database = open_database(tmp_path)
    with database.transaction() as connection:
        insert_sample_job(connection)
        # A normal change: version 1 → 2.
        jobs_repo.update_job(connection, "j1", NOW, {"prova": "Enduro"})
        assert jobs_repo.get_job_row(connection, "j1")["version"] == 2
        # Bookkeeping (VPS dropped its copy): version stays at 2, so a
        # reviewer editing the job does not get a needless conflict.
        jobs_repo.update_job(connection, "j1", NOW, {"raw_on_vps": 0}, bump_version=False)
        assert jobs_repo.get_job_row(connection, "j1")["version"] == 2
    database.close()


def test_update_job_refuses_columns_that_are_not_on_the_list(tmp_path):
    # Column names go into the SQL text, so only a fixed list is allowed.
    database = open_database(tmp_path)
    with database.transaction() as connection, pytest.raises(ValueError):
        jobs_repo.update_job(connection, "j1", NOW, {"id": "other"})
    database.close()


def test_a_failed_transaction_leaves_nothing_behind(tmp_path):
    database = open_database(tmp_path)
    with pytest.raises(RuntimeError), database.transaction() as connection:
        insert_sample_job(connection)
        raise RuntimeError("something went wrong half-way")
    with database.transaction() as connection:
        assert jobs_repo.get_job_row(connection, "j1") is None
    database.close()


def test_rolling_stats_are_size_weighted_averages(tmp_path):
    database = open_database(tmp_path)
    with database.transaction() as connection:
        workers_repo.insert_worker(
            connection,
            worker_id="w1",
            name="pc",
            role=WorkerRole.WORKER,
            hostname=None,
            capabilities=WorkerCapabilities(has_nvenc=False, video_encoder="libx264"),
            bearer_token_sha256="t" * 64,
            storage_cap_bytes=0,
            now=NOW,
        )
        # No measurements yet: unknown, not zero (design §5, no calibration).
        assert workers_repo.rolling_stats(connection, "w1").download_mbps is None
        # 100 MB in 8 s and 100 MB in 32 s: 200 MB in 40 s = 40 Mbps overall.
        for seconds in (8, 32):
            workers_repo.insert_stat_sample(
                connection, "w1", MeasurementKind.DOWNLOAD, 100_000_000, seconds, NOW
            )
        # 1 GB processed in 120 s → 120 s per GB.
        workers_repo.insert_stat_sample(
            connection, "w1", MeasurementKind.PROCESS, 1_000_000_000, 120, NOW
        )
        stats = workers_repo.rolling_stats(connection, "w1")
    assert stats.download_mbps == pytest.approx(40.0)
    assert stats.process_s_per_gb == pytest.approx(120.0)
    assert stats.upload_mbps is None
    database.close()


def test_an_enrollment_token_can_be_spent_only_once(tmp_path):
    database = open_database(tmp_path)
    token = EnrollmentToken(
        token_sha256="h" * 64,
        volunteer_name="Bruno",
        role=WorkerRole.WORKER,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    with database.transaction() as connection:
        workers_repo.insert_enrollment_token(connection, token)
        # The first use wins; a second attempt (a double click) is refused.
        assert workers_repo.mark_enrollment_token_used(connection, "h" * 64, "w1", "k1", NOW)
        assert not workers_repo.mark_enrollment_token_used(connection, "h" * 64, "w2", "k2", NOW)
        stored = workers_repo.get_enrollment_token(connection, "h" * 64)
    assert stored.used_by_worker_id == "w1"
    database.close()
