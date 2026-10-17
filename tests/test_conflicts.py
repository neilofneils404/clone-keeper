"""Resolve overlapping changes one conflict at a time instead of a whole file."""

import unittest

from clone_keeper.core import File, KeeperError
from clone_keeper.plans import conflict_blocks, describe_blocks, resolve_block
from tests.test_workflow import WorkflowFixture

MERGED = (b"top\n<<<<<<< Your clone\nmine\n||||||| Recorded baseline\nold\n=======\ntheirs\n>>>>>>> Current upstream\n"
          b"middle\n<<<<<<< Your clone\na\n||||||| Recorded baseline\nb\n=======\nc\n>>>>>>> Current upstream\nend\n")


class BlockTests(unittest.TestCase):
    def test_parses_diff3_blocks(self):
        parts, count = conflict_blocks(MERGED)
        self.assertEqual(count, 2)
        self.assertEqual(parts[1], {"local": b"mine\n", "base": b"old\n", "upstream": b"theirs\n"})
        blocks = describe_blocks(File(MERGED, 0o644))
        self.assertEqual([block["line"] for block in blocks], [2, 10])
        self.assertEqual(blocks[1]["upstream"], "c\n")

    def test_resolves_one_block_and_keeps_the_rest(self):
        once = resolve_block(MERGED, 1, "both")
        self.assertIn(b"middle\na\nc\nend\n", once)
        self.assertEqual(conflict_blocks(once)[1], 1)
        self.assertEqual(resolve_block(once, 0, "base"), b"top\nold\nmiddle\na\nc\nend\n")
        with self.assertRaises(KeeperError):
            resolve_block(once, 1, "local")

    def test_malformed_block_stays_text(self):
        broken = b"<<<<<<< Your clone\nmine\n=======\n"
        self.assertEqual(conflict_blocks(broken), ([broken], 0))


class ConflictResolutionTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.track()
        # Both sides change the greeting and the farewell differently.
        self.replace(self.clone, "Original greeting", "Local greeting")
        self.replace(self.clone, "Original farewell", "Local farewell")
        self.replace(self.source, "Original greeting", "Upstream greeting")
        self.replace(self.source, "Original farewell", "Upstream farewell")

    def resolve(self, plan, number, take, success=True):
        return self.cli("resolve", plan["planId"], "--path", "BarWidget.qml", "--conflict", number,
                        "--take", take, success=success, readonly=True)

    def test_each_conflict_is_chosen_separately_then_applied(self):
        plan = self.prepare()
        [conflict] = plan["conflicts"]
        self.assertEqual([block["local"].strip() for block in conflict["blocks"]],
                         ['property string greeting: "Local greeting"', 'property string farewell: "Local farewell"'])
        plan = self.resolve(plan, 0, "upstream")
        self.assertFalse(plan["conflicts"][0]["resolved"])
        self.assertEqual(len(plan["conflicts"][0]["blocks"]), 1)
        self.assertTrue(plan["hasConflicts"])
        plan = self.resolve(plan, 0, "local")
        self.assertTrue(plan["conflicts"][0]["resolved"])
        self.assertEqual(plan["conflicts"][0]["resolution"], "conflicts")
        self.assertFalse(plan["hasConflicts"])
        self.apply(plan)
        text = (self.clone / "BarWidget.qml").read_text()
        self.assertIn("Upstream greeting", text)
        self.assertIn("Local farewell", text)

    def test_both_keeps_local_then_upstream_lines(self):
        plan = self.resolve(self.prepare(), 1, "both")
        text = (self.state / "plans" / plan["planId"] / "candidate" / "BarWidget.qml").read_text()
        self.assertIn('"Local farewell"\n    property string farewell: "Upstream farewell"', text)

    def test_invalid_choices_are_refused(self):
        plan = self.prepare()
        self.resolve(plan, 5, "local", success=False)
        self.resolve(plan, 0, "delete", success=False)
        self.cli("resolve", plan["planId"], "--path", "BarWidget.qml", "--take", "both", success=False, readonly=True)


if __name__ == "__main__":
    unittest.main()
