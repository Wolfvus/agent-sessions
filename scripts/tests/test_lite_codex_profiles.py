import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "lite_codex_profiles.py"
spec = importlib.util.spec_from_file_location("lite_codex_profiles", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class LiteCodexProfilesTests(unittest.TestCase):
    def make_rollout(self, home: Path, kind: str, name: str, payload: str) -> Path:
        path = home / kind / "2026" / "09" / "15" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        return path

    def test_rebuild_creates_symlinks_and_does_not_modify_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            work = base / "homes" / "work"
            personal = base / "homes" / "personal"
            work_file = self.make_rollout(
                work,
                "sessions",
                "rollout-2026-09-15T10-00-00-work.jsonl",
                '{"source":"work"}\n',
            )
            archived_file = self.make_rollout(
                personal,
                "archived_sessions",
                "rollout-2026-09-15T11-00-00-personal.jsonl",
                '{"source":"personal"}\n',
            )
            before_work = work_file.read_bytes()
            before_archived = archived_file.read_bytes()

            root = base / "generated" / "federation"
            module.rebuild(
                [
                    module.Profile("work", work.resolve()),
                    module.Profile("personal", personal.resolve()),
                ],
                root,
            )

            linked_work = root / "sessions" / "2026" / "09" / "15" / work_file.name
            linked_archived = root / "archived_sessions" / "2026" / "09" / "15" / archived_file.name
            self.assertTrue(linked_work.is_symlink())
            self.assertTrue(linked_archived.is_symlink())
            self.assertEqual(linked_work.resolve(), work_file.resolve())
            self.assertEqual(linked_archived.resolve(), archived_file.resolve())
            self.assertEqual(work_file.read_bytes(), before_work)
            self.assertEqual(archived_file.read_bytes(), before_archived)

            manifest = json.loads((root / module.MANIFEST).read_text(encoding="utf-8"))
            self.assertEqual({row["profile"] for row in manifest["files"]}, {"work", "personal"})

    def test_rebuild_never_follows_existing_output_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "homes" / "work"
            self.make_rollout(
                home,
                "sessions",
                "rollout-2026-09-15T12-00-00-safe.jsonl",
                '{}\n',
            )

            protected = base / "protected"
            protected.mkdir()
            marker = protected / "keep-me.txt"
            marker.write_text("do not delete", encoding="utf-8")

            root = base / "generated" / "federation"
            root.parent.mkdir(parents=True)
            root.symlink_to(protected, target_is_directory=True)

            module.rebuild([module.Profile("work", home.resolve())], root)

            self.assertTrue(marker.exists(), "rebuild followed the output symlink and deleted its target")
            self.assertFalse(root.is_symlink())
            self.assertTrue((root / "sessions").is_dir())

    def test_colliding_relative_paths_keep_both_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            work = base / "homes" / "work"
            geo = base / "homes" / "geo"
            filename = "rollout-2026-09-15T13-00-00-shared.jsonl"
            first = self.make_rollout(work, "sessions", filename, '{"profile":"work"}\n')
            second = self.make_rollout(geo, "sessions", filename, '{"profile":"geo"}\n')
            root = base / "generated" / "federation"

            module.rebuild(
                [module.Profile("work", work.resolve()), module.Profile("geo", geo.resolve())],
                root,
            )

            day = root / "sessions" / "2026" / "09" / "15"
            links = sorted(day.glob("rollout-*.jsonl"))
            self.assertEqual(len(links), 2)
            self.assertEqual({link.resolve() for link in links}, {first.resolve(), second.resolve()})
            self.assertTrue(any("--geo.jsonl" in link.name for link in links))

    def test_load_profiles_rejects_duplicate_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "profiles.json"
            config.write_text(
                json.dumps(
                    [
                        {"name": "Work", "home": "~/.codex-work"},
                        {"name": "work", "home": "~/.codex-other"},
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate profile name"):
                module.load_profiles(config)


if __name__ == "__main__":
    unittest.main()
