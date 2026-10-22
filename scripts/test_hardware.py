"""Every `HARDWARE:` comment in the code has a row in docs/hardware-checklist.md.
Run: python -m unittest discover -s scripts"""
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [*(ROOT / "plugin" / "src").glob("*.ts"), *(ROOT / "relay").glob("*.py"), *(ROOT / "hook").glob("*.py"), *(ROOT / "scripts").glob("*.py")]


class HardwareChecklist(unittest.TestCase):
    def test_every_hardware_comment_is_listed(self):
        in_code = Counter()
        for path in SOURCES:
            if path.name == "test_hardware.py":
                continue
            hits = len(re.findall(r"(//|#)\s*HARDWARE:", path.read_text(encoding="utf-8")))
            if hits:
                in_code[path.relative_to(ROOT).as_posix()] = hits
        checklist = (ROOT / "docs" / "hardware-checklist.md").read_text(encoding="utf-8")
        listed = Counter(re.findall(r"^\| \d+ \| `([^`]+)`", checklist, re.M))
        self.assertEqual(listed, in_code)


if __name__ == "__main__":
    unittest.main()
