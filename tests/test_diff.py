from __future__ import annotations

import unittest

from hunkmap.diff import parse_patch


class ParsePatchTest(unittest.TestCase):
    def test_text_hunks_become_distinct_units(self) -> None:
        patch = b"""diff --git a/a.txt b/a.txt
index 1111111..2222222 100644
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-before
+after
@@ -10 +10 @@
-same
+different
"""
        units = parse_patch(patch)
        self.assertEqual([unit.path for unit in units], ["a.txt", "a.txt"])
        self.assertEqual([unit.ordinal for unit in units], [0, 1])
        self.assertEqual(len({unit.id for unit in units}), 2)
        self.assertIn(b"+after", units[0].patch)
        self.assertNotIn(b"+different", units[0].patch)

    def test_hunkless_file_change_is_still_reviewable(self) -> None:
        patch = b"""diff --git a/tool b/tool
old mode 100644
new mode 100755
"""
        units = parse_patch(patch)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].kind, "file")
        self.assertEqual(units[0].path, "tool")


if __name__ == "__main__":
    unittest.main()
