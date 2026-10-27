"""Placeholder test so the worker test folder is not empty.

The worker agent replaces this with real tests.
"""


def test_worker_package_can_be_imported():
    # Importing the package proves the workspace wiring (uv + pyproject) is
    # correct: if the package were not installed, this line would raise
    # ModuleNotFoundError and the test would fail.
    import worker

    # Every Python module has a __name__; checking it is a cheap way to make
    # the test assert something concrete about what was imported.
    assert worker.__name__ == "worker"
