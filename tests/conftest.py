"""Fixture bersama: pastikan scripts/ ada di sys.path (agar `import common` dst
bisa dipakai dari tests/), dan tidak pernah menyentuh workspace/state/ asli."""
import os
import sys

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
