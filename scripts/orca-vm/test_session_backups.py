"""Verify recoverable SQLite checkpoints and replay-safe transcript replication."""

import hashlib
import importlib.util
import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SessionBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.agent = load_module("backup_test_agent", "session-backup-agent.py")
        self.agent.HOME = self.root / "home"
        self.agent.DATA = self.agent.HOME / ".local/share/opencode"
        self.agent.BACKUPS = self.root / "backups"
        self.agent.STAGING = self.root / "staging"
        self.agent.DATA.mkdir(parents=True)
        self.database = sqlite3.connect(self.agent.DATA / "opencode.db")
        self.database.execute("pragma journal_mode=wal")
        self.database.executescript(
            "create table session_v2(id text,title text,directory text,time_created int);"
            "create table session_message(id text,session_id text,type text,seq int,"
            "time_created int,data text);"
            "create table event(id text,aggregate_id text,data text);"
            "create table credential(value text);"
        )
        self.database.execute(
            "insert into session_v2 values(?,?,?,?)", ("ses_test", "Test", "/work/test", 1)
        )
        self.database.execute(
            "insert into session_message values(?,?,?,?,?,?)",
            ("msg_test", "ses_test", "user", 1, 1, json.dumps({"text": "Recovery test"})),
        )
        self.database.execute(
            "insert into credential values(?)", ("DO_NOT_EXPORT_THIS_CREDENTIAL",)
        )
        self.database.commit()

    def tearDown(self):
        self.database.close()
        self.temporary.cleanup()

    def test_wal_checkpoint_has_transcript_and_excludes_credentials(self):
        result = self.agent.checkpoint(include_repositories=False)
        archive = Path(result["archive"])
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), result["sha256"])
        self.assertEqual(
            archive.read_bytes(), (self.agent.STAGING / "checkpoint.tar.gz").read_bytes()
        )
        with tarfile.open(archive) as checkpoint:
            transcript = checkpoint.extractfile("sessions/ses_test/transcript.md").read().decode()
            self.assertIn("Recovery test", transcript)
            restored = self.root / "restored.db"
            restored.write_bytes(checkpoint.extractfile("opencode-history.db").read())
        with sqlite3.connect(restored) as connection:
            self.assertEqual(connection.execute("pragma integrity_check").fetchone()[0], "ok")
            self.assertEqual(
                connection.execute("select count(*) from session_message").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute(
                    "select count(*) from sqlite_master where name='credential'"
                ).fetchone()[0],
                0,
            )
        self.assertNotIn(b"DO_NOT_EXPORT_THIS_CREDENTIAL", restored.read_bytes())

    def test_replay_is_deduplicated_and_message_updates_are_preserved(self):
        receiver = load_module("backup_test_receiver", "protect-sessions.py")
        local = self.root / "local"
        local.mkdir()
        frame = {
            "capturedAt": "test",
            "records": self.agent.records(self.database),
            "logs": [{"path": "opencode-log/test.log", "offset": 0, "base64": "dGVzdAo="}],
        }
        receiver.receive_frame(local, frame)
        receiver.receive_frame(local, frame)
        self.assertEqual(len((local / "changes.jsonl").read_text().splitlines()), 2)
        self.assertEqual((local / "logs/opencode-log/test.log").read_text(), "test\n")
        self.database.execute(
            "update session_message set data=?", (json.dumps({"text": "Updated transcript"}),)
        )
        self.database.commit()
        frame["records"] = self.agent.records(self.database)
        receiver.receive_frame(local, frame)
        self.assertIn("Updated transcript", (local / "sessions/ses_test/transcript.md").read_text())

    def test_remote_data_cannot_write_outside_local_backup_directory(self):
        receiver = load_module("backup_test_untrusted_receiver", "protect-sessions.py")
        local = self.root / "local"
        local.mkdir()
        records = self.agent.records(self.database)
        records[0]["sessionId"] = "../../escape"
        records[0]["key"] = "../../escape"
        with self.assertRaisesRegex(ValueError, "Unsafe session identifier"):
            receiver.receive_frame(local, {"capturedAt": "test", "records": records, "logs": []})
        with self.assertRaisesRegex(ValueError, "Unsafe log path"):
            receiver.receive_frame(
                local,
                {
                    "capturedAt": "test",
                    "records": [],
                    "logs": [
                        {"path": "../../escape", "offset": 0, "base64": "dGVzdAo="},
                    ],
                },
            )
        self.assertFalse((self.root / "escape").exists())

    def test_docker_shell_preserves_home_tools_and_repairs_legacy_profile(self):
        shell = load_module("backup_test_docker_shell", "configure-docker-shell.py")
        home = self.root / "shell-home"
        home.mkdir()
        (home / ".profile").write_text('export CUSTOM_SETTING="keep"\n')
        shell.configure(home)
        self.assertFalse((home / ".bash_profile").exists())
        self.assertIn('export CUSTOM_SETTING="keep"', (home / ".profile").read_text())
        profile = home / ".bash_profile"
        profile.write_text(shell.LEGACY_LITERAL + "\n# Mercor GitHub credential environment\n")
        shell.configure(home)
        repaired = profile.read_text()
        self.assertNotIn(shell.LEGACY_LITERAL, repaired)
        self.assertIn('. "$HOME/.profile"', repaired)
        self.assertIn("$HOME/.local/bin:$HOME/bin:$PATH", repaired)
        shell.configure(home)
        self.assertEqual(profile.read_text(), repaired)


if __name__ == "__main__":
    unittest.main()
