"""Skip symlink scenarios only when Windows refuses to create the test link."""

from pathlib import Path

import pytest


@pytest.fixture
def symlinks(tmp_path: Path):
    target = tmp_path / "symlink-probe-target"
    link = tmp_path / "symlink-probe"
    target.touch()
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlinks require Developer Mode or administrator privileges")
        raise
    finally:
        link.unlink(missing_ok=True)
        target.unlink()
