from __future__ import annotations

import os

import pytest

from backend.cookbook import config


def test_resolve_under_data_root_allows_child_path(isolated_home):
    result = config.resolve_under_data_root("downloads/model.bin")
    assert result == (config.CONFIG_DIR.resolve() / "downloads" / "model.bin")


def test_resolve_under_data_root_rejects_traversal_escape(isolated_home):
    with pytest.raises(ValueError):
        config.resolve_under_data_root("../../../../Temp/evil.bin")


def test_resolve_under_data_root_rejects_absolute_path(isolated_home):
    # Use an absolute path that is absolute on the *running* platform: a
    # Windows drive path ("C:/...") is not absolute on POSIX (it resolves to a
    # child of the data root), so pick per-OS to keep the escape genuinely absolute.
    abs_path = "C:/Windows/evil.dll" if os.name == "nt" else "/etc/evil.dll"
    with pytest.raises(ValueError):
        config.resolve_under_data_root(abs_path)
