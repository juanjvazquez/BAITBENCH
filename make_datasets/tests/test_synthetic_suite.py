from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from make_datasets import synthetic_suite as suite


class SyntheticSuiteResumeTests(unittest.TestCase):
    def make_entry(self, root: Path, run_id: str, agent: str) -> dict[str, str]:
        return {
            "run_id": run_id,
            "agent": agent,
            "run_dir": str(root / "runs" / run_id),
        }

    def read_json_lines(self, path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_tmux_session_name_for_run_id_matches_launcher_script(self) -> None:
        run_id = "codex_gpt-5.4_regression_task_01_n100k_s10_validity_20260413_120000"
        self.assertEqual(
            suite.tmux_session_name_for_run_id(run_id),
            "codex_gpt_5_4_regression_task_01_n100k_s10_validity_20260413_120000",
        )

    def test_remote_run_states_uses_one_ssh_call_and_parses_json(self) -> None:
        payload = {"completed_run_ids": ["done-a"], "active_run_ids": ["live-b"]}
        completed_process = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

        with mock.patch.object(
            suite.subprocess, "run", return_value=completed_process
        ) as run_mock:
            completed, active = suite.remote_run_states()

        self.assertEqual(completed, {"done-a"})
        self.assertEqual(active, {"live-b"})
        run_mock.assert_called_once()
        self.assertEqual(
            run_mock.call_args.args[0],
            ["ssh", "-i", suite.VPS_KEY, suite.VPS_HOST, "bash", "-s", "--", suite.REMOTE_BASE],
        )
        self.assertIn("python3 - \"$REMOTE_BASE\"", run_mock.call_args.kwargs["input"])

    def test_launch_manifest_resume_skips_completed_and_active_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            manifest_path = root / "manifest.json"
            launch_log = root / "launch.log"
            status_path = root / "status.json"

            completed_entry = self.make_entry(root, "codex_done_20260413_120000", "codex")
            active_entry = self.make_entry(root, "claude_live_20260413_120100", "claude")
            remaining_entry = self.make_entry(
                root, "codex_remaining_20260413_120200", "codex"
            )
            manifest_path.write_text(
                json.dumps([completed_entry, active_entry, remaining_entry], indent=2)
                + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    suite,
                    "remote_run_states",
                    return_value=(
                        {completed_entry["run_id"]},
                        {active_entry["run_id"]},
                    ),
                ),
                mock.patch.object(suite, "remote_active_count", return_value=0),
                mock.patch.object(suite, "copy_run_to_vps") as copy_mock,
                mock.patch.object(suite, "start_run_on_vps") as start_mock,
                mock.patch.object(suite.typer, "echo") as echo_mock,
            ):
                suite.launch_manifest(
                    manifest_path=manifest_path,
                    max_codex=2,
                    max_claude=2,
                    poll_seconds=0.0,
                    launch_log=launch_log,
                    status_path=status_path,
                    resume=True,
                )

            copy_mock.assert_called_once_with(Path(remaining_entry["run_dir"]))
            start_mock.assert_called_once_with(remaining_entry["run_id"])
            echo_mock.assert_any_call(
                "Skipping 1 completed, 1 in-progress. Resuming with 1 remaining."
            )

            log_events = self.read_json_lines(launch_log)
            event_names = [str(event["event"]) for event in log_events]
            self.assertIn("manifest_start", event_names)
            self.assertIn("resume_summary", event_names)
            self.assertIn("skipped_completed", event_names)
            self.assertIn("skipped_active", event_names)
            self.assertIn("manifest_complete", event_names)

            statuses = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(
                statuses[completed_entry["run_id"]]["status"], "skipped_completed"
            )
            self.assertEqual(statuses[active_entry["run_id"]]["status"], "skipped_active")
            self.assertEqual(
                statuses[remaining_entry["run_id"]]["status"],
                "start_done",
            )

    def test_launch_manifest_without_resume_does_not_probe_remote_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            manifest_path = root / "manifest.json"
            launch_log = root / "launch.log"
            status_path = root / "status.json"

            entry = self.make_entry(root, "codex_full_replay_20260413_121000", "codex")
            manifest_path.write_text(
                json.dumps([entry], indent=2) + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    suite,
                    "remote_run_states",
                    side_effect=AssertionError("resume probe should not run"),
                ),
                mock.patch.object(suite, "remote_active_count", return_value=0),
                mock.patch.object(suite, "copy_run_to_vps") as copy_mock,
                mock.patch.object(suite, "start_run_on_vps") as start_mock,
            ):
                suite.launch_manifest(
                    manifest_path=manifest_path,
                    max_codex=1,
                    max_claude=1,
                    poll_seconds=0.0,
                    launch_log=launch_log,
                    status_path=status_path,
                    resume=False,
                )

            copy_mock.assert_called_once_with(Path(entry["run_dir"]))
            start_mock.assert_called_once_with(entry["run_id"])


if __name__ == "__main__":
    unittest.main()
