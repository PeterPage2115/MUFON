"""Test configuration for the scaffold's empty test suite.

pytest reports exit code 5 ("no tests collected") for an empty suite.
Treat the empty scaffold suite as success (exit 0) until real tests land
in later tasks; once tests exist the exit code is 0/1 as usual.
"""

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        # Direct assignment (instead of `raise pytest.exit.Exception(...)`,
        # which would also work but prints a noise line to stderr).
        session.exitstatus = pytest.ExitCode.OK
