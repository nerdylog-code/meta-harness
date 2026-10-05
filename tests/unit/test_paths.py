"""Layout resolution tests — including the regression that encodes v1's D1 defect."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from metaharness import paths  # noqa: E402

ENV = paths.DATA_DIR_ENV


class TestDataRoot(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get(ENV)
        os.environ.pop(ENV, None)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = self._saved

    def test_explicit_argument_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ[ENV] = str(Path(tmp) / "ignored")
            resolved = paths.data_root(Path(tmp) / "explicit")
            self.assertEqual(resolved, (Path(tmp) / "explicit").resolve())

    def test_env_override_is_honoured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ[ENV] = tmp
            self.assertEqual(paths.data_root(), Path(tmp).resolve())

    def test_default_is_platformdirs_not_the_repo(self) -> None:
        """D1 regression: the data root must never be derived from __file__.

        v1 resolved built-in assets with Path(__file__).parents[N], which was
        right in a checkout and wrong in every installation. If this assertion
        ever fails, the layout has started depending on source position again.
        """
        import platformdirs

        expected = Path(platformdirs.user_data_dir(paths.APP_NAME, appauthor=paths.APP_AUTHOR)).resolve()
        self.assertEqual(paths.data_root(), expected)
        repo = paths.find_repo_root()
        if repo is not None:
            self.assertFalse(
                str(expected).startswith(str(repo)),
                f"data root {expected} must not live inside the source checkout {repo}",
            )


class TestLayout(unittest.TestCase):
    def test_ensure_layout_creates_all_subdirs_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = paths.ensure_layout(tmp)
            self.assertEqual(root, Path(tmp).resolve())
            for name in paths.SUBDIRS:
                self.assertTrue((root / name).is_dir(), f"missing subdir {name}")
            again = paths.ensure_layout(tmp)  # must not raise
            self.assertEqual(again, root)

    def test_subdir_rejects_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                paths.subdir("not-a-real-subdir", tmp)
            self.assertEqual(paths.subdir("artifacts", tmp), Path(tmp).resolve() / "artifacts")


class TestRepoRoot(unittest.TestCase):
    def test_finds_this_checkout(self) -> None:
        found = paths.find_repo_root()
        self.assertIsNotNone(found, "expected to detect the source checkout")
        self.assertTrue((found / "pyproject.toml").is_file())

    def test_returns_none_outside_a_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deep = Path(tmp) / "a" / "b" / "c"
            deep.mkdir(parents=True)
            self.assertIsNone(paths.find_repo_root(deep))


if __name__ == "__main__":
    unittest.main()
