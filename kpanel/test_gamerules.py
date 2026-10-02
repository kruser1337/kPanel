"""Gamerule metadata as the panel uses it."""

import unittest

import gamerules as gr


class Defaults(unittest.TestCase):
    def test_every_boolean_default_is_true_or_false(self):
        """A server answers true/false; any other default marks the rule "changed" forever.

        The wiki lists locator_bar as bool-or-string, and its default cell
        ("true" plus "everyone") was scraped as "trueeveryone".
        """
        odd = {n: r.default for n, r in gr.RULES.items()
               if r.kind == "bool" and r.default not in ("true", "false")}
        self.assertEqual(odd, {})

    def test_locator_bar_defaults_to_true(self):
        self.assertEqual(gr.RULES["locator_bar"].default, "true")


if __name__ == "__main__":
    unittest.main()
