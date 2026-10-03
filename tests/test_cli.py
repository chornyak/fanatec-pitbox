"""The command line (Steam launch options), run through the real launcher against a fake wheel base."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fakesys  # noqa: E402


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pitbox-cli-"))
        self.fake = fakesys.FakeBase(self.tmp / "sys")
        (self.tmp / "config").mkdir()
        (self.tmp / "config" / "state.json").write_text(json.dumps({"aliases": {"4": "iRacing"}}))
        self.env = dict(os.environ, FANATEC_PITBOX_SYSFS=str(self.tmp / "sys"),
                        FANATEC_PITBOX_CONFIG=str(self.tmp / "config"))

    def run_cli(self, *args, cwd=None):
        return subprocess.run([str(ROOT / "fanatec-pitbox"), *args], env=self.env, cwd=cwd or self.tmp,
                              capture_output=True, text=True, timeout=30)

    def test_switches_setup_then_starts_the_game_in_its_own_folder(self):
        game_dir = self.tmp / "game"
        game_dir.mkdir()
        r = self.run_cli("--setup", "3", "sh", "-c", "echo started in $(pwd)", cwd=game_dir)
        self.assertEqual(self.fake.read("SLOT"), 3)
        self.assertIn(f"started in {game_dir}", r.stdout)
        self.assertIn("SETUP 3 is active", r.stderr)

    def test_setup_by_name(self):
        self.run_cli("--setup", "iracing")
        self.assertEqual(self.fake.read("SLOT"), 4)

    def test_problems_never_block_the_game(self):
        r = self.run_cli("--setup", "9", "sh", "-c", "echo game ran")
        self.assertIn("unknown setup", r.stderr)
        self.assertIn("game ran", r.stdout)
        self.assertEqual(self.fake.read("SLOT"), 2)
        (self.fake.dev / "advanced_mode").write_text("0\n")
        r = self.run_cli("--setup", "3", "sh", "-c", "echo game ran")
        self.assertIn("Standard mode", r.stderr)
        self.assertIn("game ran", r.stdout)
        self.assertEqual(self.fake.read("SLOT"), 2)

    def test_no_wheel_base(self):
        import shutil
        shutil.rmtree(self.fake.dev)
        r = self.run_cli("--setup", "3", "sh", "-c", "echo game ran")
        self.assertIn("no Fanatec wheel base found", r.stderr)
        self.assertIn("game ran", r.stdout)

    def test_list(self):
        r = self.run_cli("--list")
        self.assertIn("* 2", r.stdout)
        self.assertIn("  4  iRacing", r.stdout)


if __name__ == "__main__":
    unittest.main()
