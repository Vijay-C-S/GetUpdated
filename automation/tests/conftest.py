import sys
from pathlib import Path

# The automation modules import each other by bare name (e.g. `import rrb`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
