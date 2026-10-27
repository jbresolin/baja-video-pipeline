"""Logging: one rotating file, human-readable lines, the job id on every line."""

import logging
import logging.handlers
import threading

import pytest

from worker.logging_setup import configure_logging, job_context


@pytest.fixture
def log_file(tmp_path):
    # configure_logging replaces the root logger's handlers; remember
    # pytest's own ones and put them back afterwards.
    root = logging.getLogger()
    original_handlers, original_level = list(root.handlers), root.level
    path = configure_logging(tmp_path / "logs")
    yield path
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if handler not in original_handlers:
            handler.close()
    for handler in original_handlers:
        root.addHandler(handler)
    root.setLevel(original_level)


def test_lines_carry_the_job_id_or_a_dash(log_file):
    log = logging.getLogger("worker.test")
    log.info("not about a job")
    with job_context("job-123"):
        log.info("about a job")
        logging.getLogger("some.other.module").warning("also about it")

    lines = log_file.read_text().splitlines()
    assert "job=- " in lines[0] and "not about a job" in lines[0]
    assert "job=job-123 " in lines[1] and "INFO" in lines[1]
    # Another module's log call inside the block gets the id too.
    assert "job=job-123 " in lines[2] and "WARNING" in lines[2]


def test_each_thread_has_its_own_job(log_file):
    # Two stage threads working on different jobs must not mix up ids.
    log = logging.getLogger("worker.test")

    def work(job_id):
        with job_context(job_id):
            log.info("working")

    threads = [threading.Thread(target=work, args=(f"job-{n}",)) for n in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = log_file.read_text().splitlines()
    assert sorted(line.split("job=")[1].split()[0] for line in lines) == [
        f"job-{n}" for n in range(5)
    ]


def test_explicit_job_id_wins(log_file):
    logging.getLogger("worker.test").info("claimed", extra={"job_id": "job-9"})
    assert "job=job-9 " in log_file.read_text()


def test_the_file_rotates(log_file):
    file_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    ]
    # Exactly one log file, and it rotates (size limit + kept backups).
    assert len(file_handlers) == 1
    assert file_handlers[0].maxBytes > 0 and file_handlers[0].backupCount > 0
