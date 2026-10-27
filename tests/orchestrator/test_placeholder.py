"""Placeholder test so the orchestrator test folder is not empty.

The orchestrator agent replaces this with real tests.
"""


def test_orchestrator_package_can_be_imported():
    # Importing the package proves the workspace wiring (uv + pyproject) is
    # correct: if the package were not installed, this line would raise
    # ModuleNotFoundError and the test would fail.
    import orchestrator

    # Every Python module has a __name__; checking it is a cheap way to make
    # the test assert something concrete about what was imported.
    assert orchestrator.__name__ == "orchestrator"
