# Part of Nova. See LICENSE file for full copyright and licensing details.
#
# Accessibility guard (per-commit, no Odoo boot): ratchets focus rings,
# outline/box-shadow pairing, reduced-motion and forced-colors coverage
# across Nova's SCSS. New files must comply; existing gaps are baselined
# and fail only on regression. Stdlib only. Run:
#   python tests/test_nova_a11y_guard.py [--refresh]
#

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_ROOT = os.path.dirname(HERE)
SRC_ROOT = os.path.join(MODULE_ROOT, "static", "src")

BASE_FOCUS = os.path.join(HERE, "nova_a11y_focus_missing.txt")
BASE_MOTION = os.path.join(HERE, "nova_a11y_motion_missing.txt")
BASE_CONTRAST = os.path.join(HERE, "nova_a11y_contrast_missing.txt")
BASE_NAKED = os.path.join(HERE, "nova_a11y_naked_outlines.txt")

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"(?m)//.*$")
BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")

INTERACTIVE_RE = re.compile(
    r"\.(btn|button|o_nav_entry|dropdown-item|page-link)\b"
    r"|(?<!-)\bbutton\b|\binput\b|\bselect\b|\btextarea\b"
)
SEVERITY_RE = re.compile(r"badge|o_tag|nova_due|nova_digest|_alert|o_notification")
MOTION_EXEMPT = {
    "webclient/login/nova_login.scss",
    "portal/nova_portal.scss",
}

# Token-only files: variable names contain words like button/input/badge.
TOKEN_FILES_RE = re.compile(r"variables|overridden")


def is_token_file(rel):
    return bool(TOKEN_FILES_RE.search(rel))


def scss_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(SRC_ROOT):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules"}]
        for filename in filenames:
            if not filename.endswith(".scss"):
                continue
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, SRC_ROOT).replace(os.sep, "/")
            try:
                with open(full, "r", encoding="utf-8") as handle:
                    out.append((rel, handle.read()))
            except (OSError, UnicodeDecodeError):
                continue
    return out


def strip(text):
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub("", text))


def is_dark(rel):
    return rel.endswith(".dark.scss")


def focus_missing(files):
    missing = set()
    for rel, text in files:
        if is_dark(rel) or is_token_file(rel):
            continue
        cleaned = strip(text)
        if INTERACTIVE_RE.search(cleaned) and ":focus-visible" not in cleaned:
            missing.add(rel)
    return missing


def naked_outlines(files):
    counts = {}
    for rel, text in files:
        cleaned = strip(text)
        n = 0
        for _selector, body in BLOCK_RE.findall(cleaned):
            if re.search(r"outline\s*:\s*(none|0)\b", body) and not re.search(
                r"box-shadow\s*:\s*(?!none\b)[^;]+;", body
            ):
                n += 1
        if n:
            counts[rel] = n
    return counts


def motion_missing(files):
    missing = set()
    for rel, text in files:
        if is_dark(rel) or rel in MOTION_EXEMPT:
            continue
        cleaned = strip(text)
        if (
            re.search(r"transition\s*:|animation\s*:", cleaned)
            and "prefers-reduced-motion" not in cleaned
        ):
            missing.add(rel)
    return missing


def contrast_missing(files):
    missing = set()
    for rel, text in files:
        if is_dark(rel) or is_token_file(rel):
            continue
        cleaned = strip(text)
        if SEVERITY_RE.search(cleaned) and "forced-colors" not in cleaned:
            missing.add(rel)
    return missing


def load_list(path):
    known = set()
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    known.add(line)
    return known


def load_counts(path):
    known = {}
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                name, _, count = line.rpartition(":")
                try:
                    known[name.strip()] = int(count.strip())
                except ValueError:
                    continue
    return known


def write_list(path, values, header):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(header)
        for value in sorted(values):
            handle.write(value + "\n")


class TestNovaA11yGuard(unittest.TestCase):
    def test_focus_rings(self):
        if not os.path.isdir(SRC_ROOT):
            self.skipTest("nova sources not found")
        baseline = load_list(BASE_FOCUS)
        new = focus_missing(scss_files()) - baseline
        if new:
            sys.stdout.write(
                "Files with interactive selectors but no :focus-visible:\n"
                + "".join("  %s\n" % f for f in sorted(new))
            )
        self.assertEqual(
            sorted(new), [], "New files must ship :focus-visible rules."
        )

    def test_no_naked_outlines(self):
        if not os.path.isdir(SRC_ROOT):
            self.skipTest("nova sources not found")
        baseline = load_counts(BASE_NAKED)
        current = naked_outlines(scss_files())
        worse = sorted(
            f for f, n in current.items() if n > baseline.get(f, 0)
        )
        if worse:
            sys.stdout.write(
                "outline:none without a box-shadow ring:\n"
                + "".join("  %s (%d)\n" % (f, current[f]) for f in worse)
            )
        self.assertEqual(
            worse, [], "outline:none must pair with a visible ring."
        )

    def test_reduced_motion(self):
        if not os.path.isdir(SRC_ROOT):
            self.skipTest("nova sources not found")
        baseline = load_list(BASE_MOTION)
        new = motion_missing(scss_files()) - baseline
        if new:
            sys.stdout.write(
                "Files with motion but no prefers-reduced-motion block:\n"
                + "".join("  %s\n" % f for f in sorted(new))
            )
        self.assertEqual(
            sorted(new), [], "New motion must respect prefers-reduced-motion."
        )

    def test_forced_colors(self):
        if not os.path.isdir(SRC_ROOT):
            self.skipTest("nova sources not found")
        baseline = load_list(BASE_CONTRAST)
        new = contrast_missing(scss_files()) - baseline
        if new:
            sys.stdout.write(
                "Files painting severity without a forced-colors block:\n"
                + "".join("  %s\n" % f for f in sorted(new))
            )
        self.assertEqual(
            sorted(new), [], "New severity paint must handle forced-colors."
        )


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        files = scss_files()
        write_list(
            BASE_FOCUS,
            focus_missing(files),
            "# Nova a11y baseline: light files with interactive selectors "
            "but no :focus-visible yet.\n"
            "# Fix a file, then delete its line here.\n",
        )
        write_list(
            BASE_MOTION,
            motion_missing(files),
            "# Nova a11y baseline: files with motion but no "
            "prefers-reduced-motion block yet.\n"
            "# Fix a file, then delete its line here.\n",
        )
        write_list(
            BASE_CONTRAST,
            contrast_missing(files),
            "# Nova a11y baseline: files painting severity without a "
            "forced-colors block yet.\n"
            "# Fix a file, then delete its line here.\n",
        )
        naked = naked_outlines(files)
        with open(BASE_NAKED, "w", encoding="utf-8") as handle:
            handle.write(
                "# Nova a11y baseline: outline:none rules without a "
                "box-shadow ring, per file.\n"
                "# Fix a file, then delete its line here.\n"
            )
            for name in sorted(naked):
                handle.write("%s:%d\n" % (name, naked[name]))
        sys.stdout.write("a11y baselines refreshed\n")
    else:
        unittest.main(verbosity=2)
