"""Entry point for hosting panels that start the app with `python main.py`.

Locally you can keep using `python MainFile/discordBot.py` — this file just runs it.
"""
import runpy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
MAIN_FILE = PROJECT_ROOT / "MainFile" / "discordBot.py"

# discordBot.py does `import main`, so MainFile must come first on the path —
# otherwise that import would find this file instead of MainFile/main.py.
sys.path.insert(0, str(PROJECT_ROOT / "MainFile"))

runpy.run_path(str(MAIN_FILE), run_name="__main__")
