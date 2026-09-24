"""Goal lifecycle checks against real disposable Git repositories."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_kit.adoption import BaselineComparison
from ai_kit.goals import (
    GoalError,
    complete_goal,
    create_goal,
    is_evidence_fresh,
    list_goals,
    load_goal,
    reopen_goal,
    set_goal_state,
    verify_goal,
)


class GoalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("commit", "--allow-empty", "-qm", "base")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()

    def profile(self, *modules: str) -> None:
        path = self.root / ".ai/profile.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        names = ", ".join(f'"{module}"' for module in modules)
        path.write_text(f'schema = 1\nbase = "software"\nmodules = [{names}]\n')
        self.git("add", ".ai/profile.toml")
        self.git("commit", "-qm", "profile")

    def goal(self, command: str = "true", checks: dict[str, str] | None = None) -> None:
        create_goal(
            self.root,
            "feature",
            "Feature",
            "User can use it",
            ["The feature works"],
            checks if checks is not None else {"check": command},
        )
        self.git("add", ".ai/goals/active/feature.toml")
        self.git("commit", "-qm", "acceptance contract")
        set_goal_state(self.root, "feature", "ACTIVE")
        set_goal_state(self.root, "feature", "IMPLEMENTED")

    def test_completion_requires_current_commit_and_moves_goal(self) -> None:
        self.goal()
        with self.assertRaisesRegex(GoalError, "no successful verification"):
            complete_goal(self.root, "feature")

        first = verify_goal(self.root, "feature")
        self.assertEqual((first.state, first.evidence.result), ("VERIFYING", "pass"))
        self.assertEqual(first.evidence.commit, self.git("rev-parse", "HEAD"))
        contract = self.root / ".ai/goals/active/feature.toml"
        original = contract.read_text()
        contract.write_text(original.replace("The feature works", "A different criterion"))
        with self.assertRaisesRegex(GoalError, "contract differs from HEAD"):
            complete_goal(self.root, "feature")
        contract.write_text(original)
        product = self.root / "uncommitted.py"
        product.write_text("print('uncommitted')\n")
        with self.assertRaisesRegex(GoalError, "uncommitted product"):
            complete_goal(self.root, "feature")
        product.unlink()
        self.git("commit", "--allow-empty", "-qm", "later code")
        with self.assertRaisesRegex(GoalError, "stale"):
            complete_goal(self.root, "feature")

        verify_goal(self.root, "feature")
        done = complete_goal(self.root, "feature")
        self.assertEqual(done.state, "DONE")
        self.assertEqual(load_goal(self.root, "feature"), done)
        self.assertEqual([goal.id for goal in list_goals(self.root)], ["feature"])
        self.assertFalse((self.root / ".ai/goals/active/feature.toml").exists())
        self.assertTrue((self.root / ".ai/goals/accepted/feature.toml").exists())

    def test_dirty_code_and_uncommitted_contract_cannot_be_verified(self) -> None:
        self.goal()
        product = self.root / "product.py"
        product.write_text("print('new code')\n")
        with self.assertRaisesRegex(GoalError, "uncommitted product"):
            verify_goal(self.root, "feature")
        self.git("add", "product.py")
        self.git("commit", "-qm", "implementation")

        contract = self.root / ".ai/goals/active/feature.toml"
        contract.write_text(
            contract.read_text().replace("The feature works", "The feature persists")
        )
        with self.assertRaisesRegex(GoalError, "contract differs from HEAD"):
            verify_goal(self.root, "feature")
        product.write_text("print('improved code')\n")
        self.git("add", ".ai/goals/active/feature.toml", "product.py")
        self.git("commit", "-qm", "revise contract and implementation")

        verified = verify_goal(self.root, "feature")
        self.assertEqual(verified.evidence.result, "pass")
        self.assertIn("changed together", verified.evidence.warnings[0])
        self.assertEqual(complete_goal(self.root, "feature").state, "DONE")

    def test_failed_and_unknown_checks_block_completion(self) -> None:
        self.goal("test ! -e .git/force-fail || exit 7")
        self.assertEqual(verify_goal(self.root, "feature").state, "VERIFYING")
        flag = self.root / ".git/force-fail"
        flag.touch()
        failed = verify_goal(self.root, "feature")
        self.assertEqual(
            (failed.state, failed.evidence.result, failed.evidence.checks[0].exit_code),
            ("ACTIVE", "fail", 7),
        )
        with self.assertRaises(GoalError):
            complete_goal(self.root, "feature")

        flag.unlink()
        set_goal_state(self.root, "feature", "IMPLEMENTED")
        self.assertEqual(verify_goal(self.root, "feature").state, "VERIFYING")

        real_run = subprocess.run

        def no_shell(command: object, *args: object, **kwargs: object) -> object:
            if isinstance(command, str):
                raise OSError("shell unavailable")
            return real_run(command, *args, **kwargs)

        with patch("ai_kit.goals.subprocess.run", side_effect=no_shell):
            unknown = verify_goal(self.root, "feature")
        self.assertEqual(
            (unknown.state, unknown.evidence.result, unknown.evidence.checks[0].status),
            ("BLOCKED", "unknown", "unknown"),
        )
        with self.assertRaises(GoalError):
            complete_goal(self.root, "feature")

    def test_completion_rechecks_mutable_runtime_evidence(self) -> None:
        self.goal("test -e .git/ready")
        ready = self.root / ".git/ready"
        ready.touch()
        self.assertEqual(verify_goal(self.root, "feature").evidence.result, "pass")
        ready.unlink()
        with self.assertRaisesRegex(GoalError, "completion recheck failed"):
            complete_goal(self.root, "feature")
        self.assertEqual(load_goal(self.root, "feature").state, "ACTIVE")

    def test_check_that_writes_uncommitted_code_cannot_produce_pass_evidence(self) -> None:
        self.goal("printf 'new code' > generated.py")
        with self.assertRaisesRegex(GoalError, "uncommitted product"):
            verify_goal(self.root, "feature")
        current = load_goal(self.root, "feature")
        self.assertEqual(current.state, "IMPLEMENTED")
        self.assertIsNone(current.evidence)

    def test_product_and_acceptance_assertions_changed_together_are_reported(self) -> None:
        self.goal()
        paths = (
            "app.py",
            "tests/test_app.py",
            "test_root.py",
            "web/panel.test.ts",
            "web/panel.spec.ts",
            "e2e/flow.ts",
        )
        for relative in paths:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("changed\n")
        self.git("add", *paths)
        self.git("commit", "-qm", "change product and acceptance assertions")
        warnings = verify_goal(self.root, "feature").evidence.warnings
        warning = next(item for item in warnings if "Product code and acceptance" in item)
        for relative in paths:
            self.assertIn(relative, warning)

    def test_strict_profile_requires_verify_check(self) -> None:
        self.profile("strict-verification")
        self.goal(checks={"acceptance": "true"})
        with self.assertRaisesRegex(GoalError, "verify"):
            verify_goal(self.root, "feature")
        contract = self.root / ".ai/goals/active/feature.toml"
        contract.write_text(contract.read_text() + '"verify" = "true"\n')
        self.git("add", ".ai/goals/active/feature.toml")
        self.git("commit", "-qm", "require project verification")
        self.assertEqual(verify_goal(self.root, "feature").evidence.result, "pass")

    def test_webapp_profile_requires_acceptance_and_runtime_error_checks(self) -> None:
        self.profile("webapp")
        self.goal(checks={"verify": "true"})
        with self.assertRaisesRegex(GoalError, "acceptance, runtime_errors"):
            verify_goal(self.root, "feature")
        contract = self.root / ".ai/goals/active/feature.toml"
        contract.write_text(
            contract.read_text() + '"acceptance" = "true"\n"runtime_errors" = "true"\n'
        )
        self.git("add", ".ai/goals/active/feature.toml")
        self.git("commit", "-qm", "require web acceptance and runtime checks")
        self.assertEqual(verify_goal(self.root, "feature").evidence.result, "pass")

    def test_done_freshness_allows_metadata_commits_and_reopen(self) -> None:
        self.goal()
        verify_goal(self.root, "feature")
        done = complete_goal(self.root, "feature")
        self.git("add", "-A", ".ai/goals")
        self.git("commit", "-qm", "accept goal")
        self.assertTrue(is_evidence_fresh(self.root, load_goal(self.root, "feature")))
        accepted = self.root / ".ai/goals/accepted/feature.toml"
        original = accepted.read_text()
        accepted.write_text(original.replace("The feature works", "A different criterion"))
        self.assertFalse(is_evidence_fresh(self.root, load_goal(self.root, "feature")))
        accepted.write_text(original)

        status = self.root / ".ai/current/STATUS.md"
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text("Goal accepted.\n")
        self.git("add", ".ai/current/STATUS.md")
        self.git("commit", "-qm", "update current status")
        self.assertTrue(is_evidence_fresh(self.root, load_goal(self.root, "feature")))

        product = self.root / "app.py"
        product.write_text("value = 42\n")
        assertion = self.root / "tests/test_app.py"
        assertion.parent.mkdir(parents=True, exist_ok=True)
        assertion.write_text("assert True\n")
        self.git("add", "app.py", "tests/test_app.py")
        self.git("commit", "-qm", "change product and assertion")
        self.assertFalse(is_evidence_fresh(self.root, load_goal(self.root, "feature")))
        reopened = reopen_goal(self.root, "feature")
        self.assertEqual(reopened.state, "IMPLEMENTED")
        self.assertEqual(reopened.evidence, done.evidence)
        self.git("add", "-A", ".ai/goals")
        self.git("commit", "-qm", "reopen goal")
        verified = verify_goal(self.root, "feature")
        self.assertEqual(verified.evidence.result, "pass")
        self.assertTrue(
            any(
                "Product code and acceptance assertions" in item
                for item in verified.evidence.warnings
            )
        )
        self.assertEqual(complete_goal(self.root, "feature").state, "DONE")
        self.git("add", "-A", ".ai/goals")
        self.git("commit", "-qm", "accept goal again")
        self.assertTrue(is_evidence_fresh(self.root, load_goal(self.root, "feature")))
        self.profile("learning")
        self.assertFalse(is_evidence_fresh(self.root, load_goal(self.root, "feature")))

    def test_baseline_regression_or_unknown_blocks_completion(self) -> None:
        self.goal()
        baseline = self.root / ".ai/baseline.json"
        baseline.write_text("{}\n")
        self.git("add", ".ai/baseline.json")
        self.git("commit", "-qm", "record baseline")
        verify_goal(self.root, "feature")

        with patch(
            "ai_kit.adoption.recheck_baseline",
            return_value=BaselineComparison("fail", ("unit: failed increased",), ()),
        ):
            with self.assertRaisesRegex(GoalError, "failed increased"):
                complete_goal(self.root, "feature")
        with patch(
            "ai_kit.adoption.recheck_baseline",
            return_value=BaselineComparison("unknown", (), ("unit: current result unknown",)),
        ):
            with self.assertRaisesRegex(GoalError, "current result unknown"):
                complete_goal(self.root, "feature")
        with patch(
            "ai_kit.adoption.recheck_baseline",
            return_value=BaselineComparison("fail", (), ()),
        ):
            self.assertEqual(complete_goal(self.root, "feature").state, "DONE")

    def test_verifying_and_done_require_complete_passing_evidence(self) -> None:
        self.goal(checks={"one": "true", "two": "true"})
        goal = self.root / ".ai/goals/active/feature.toml"
        goal.write_text(goal.read_text().replace('state = "IMPLEMENTED"', 'state = "VERIFYING"'))
        with self.assertRaisesRegex(GoalError, "requires passing evidence"):
            load_goal(self.root, "feature")
        goal.write_text(goal.read_text().replace('state = "VERIFYING"', 'state = "IMPLEMENTED"'))
        verify_goal(self.root, "feature")
        complete_goal(self.root, "feature")
        accepted = self.root / ".ai/goals/accepted/feature.toml"
        accepted.write_text(accepted.read_text().rsplit("\n[[evidence.results]]", 1)[0] + "\n")
        with self.assertRaisesRegex(GoalError, "requires passing evidence"):
            load_goal(self.root, "feature")

    def test_only_legal_state_transitions_are_available(self) -> None:
        create_goal(self.root, "feature", "Feature", "Intent", ["Criterion"], {"check": "true"})
        with self.assertRaisesRegex(GoalError, "must be IMPLEMENTED"):
            verify_goal(self.root, "feature")
        with self.assertRaisesRegex(GoalError, "illegal goal transition"):
            set_goal_state(self.root, "feature", "IMPLEMENTED")
        with self.assertRaisesRegex(GoalError, "illegal goal transition"):
            set_goal_state(self.root, "feature", "DONE")
        self.assertEqual(set_goal_state(self.root, "feature", "ACTIVE").state, "ACTIVE")
        with self.assertRaisesRegex(GoalError, "illegal goal transition"):
            set_goal_state(self.root, "feature", "VERIFYING")
        self.assertEqual(set_goal_state(self.root, "feature", "IMPLEMENTED").state, "IMPLEMENTED")
        with self.assertRaisesRegex(GoalError, "illegal goal transition"):
            set_goal_state(self.root, "feature", "DONE")

    def test_missing_acceptance_or_checks_is_invalid(self) -> None:
        with self.assertRaisesRegex(GoalError, "acceptance"):
            create_goal(self.root, "feature", "Feature", "Intent", [], {"check": "true"})
        with self.assertRaisesRegex(GoalError, "required check"):
            create_goal(self.root, "feature", "Feature", "Intent", ["Criterion"], {})
        with self.assertRaisesRegex(GoalError, "id must"):
            create_goal(
                self.root, "../escape", "Feature", "Intent", ["Criterion"], {"check": "true"}
            )
        with tempfile.TemporaryDirectory() as outside:
            (self.root / ".ai").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(GoalError, "symlink"):
                create_goal(
                    self.root, "feature", "Feature", "Intent", ["Criterion"], {"check": "true"}
                )
            self.assertEqual(list(Path(outside).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
