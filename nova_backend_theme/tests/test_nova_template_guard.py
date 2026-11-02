# Part of Nova. See LICENSE file for full copyright and licensing details.
#
# Template guard (per-commit, no Odoo boot): Owl's expression tokenizer
# rejects regex literals inside t-* attributes (production crash:
# "Tokenizer error: could not tokenize ... replace(/\./g ...)").
# This scans every t-attribute expression for /.../ literals. Logic and
# method calls stay in JS; templates keep tokenizable expressions only.
# Stdlib only. Run:
#   python tests/test_nova_template_guard.py
#

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_ROOT = os.path.dirname(HERE)

TEMPLATE_FILES = [
    os.path.join("static", "src", "home", "nova_home.xml"),
    os.path.join("static", "src", "core", "notifications", "nova_toast_history.xml"),
    os.path.join("views", "nova_home_views.xml"),
    os.path.join("views", "nova_theme_settings_views.xml"),
    os.path.join("portal", "nova_portal_templates.xml"),
]

# t-attributes whose values are Owl expressions (not plain strings).
EXPR_ATTRS = (
    "t-if", "t-elif", "t-else", "t-foreach", "t-as", "t-key", "t-out",
    "t-att-", "t-call",
)

# A /.../ literal with an escape or flag: unambiguously a regex, never
# division (template arithmetic with / is vanishingly rare; every hit
# below was a real regex when this guard was written).
REGEX_RE = re.compile(r"/(?:\\.|[^/\n\\])+?/[gimsuy]*")
ATTR_RE = re.compile(r"\b(t-(?:if|elif|else|foreach|as|key|out|call|att-[\w-]+))\s*=\s*\"([^\"]*)\"")


def expressions(path):
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    for match in ATTR_RE.finditer(text):
        yield match.group(1), match.group(2)


class TestNovaTemplateGuard(unittest.TestCase):
    def test_no_regex_literals_in_templates(self):
        offenders = []
        for rel in TEMPLATE_FILES:
            path = os.path.join(MODULE_ROOT, rel)
            if not os.path.isfile(path):
                continue
            for attr, expr in expressions(path):
                # Skip URLs and closing-tag slashes; only /.../ with
                # escapes counts, which division never has.
                if "\\" in expr and REGEX_RE.search(expr):
                    offenders.append("%s [%s]: %s" % (rel, attr, expr.strip()[:90]))
        if offenders:
            sys.stdout.write(
                "Regex literals in Owl expressions (tokenizer crash):\n"
                + "".join("  %s\n" % o for o in offenders)
            )
        self.assertEqual(offenders, [], "Move regexes into JS helpers.")

    def test_templates_parse_as_xml(self):
        import xml.dom.minidom

        for rel in TEMPLATE_FILES:
            path = os.path.join(MODULE_ROOT, rel)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    xml.dom.minidom.parseString(handle.read())
            except Exception as exc:
                self.fail("%s is not well-formed XML: %s" % (rel, exc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
