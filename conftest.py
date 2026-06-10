import sys
from pathlib import Path

# Ensure the project root is on sys.path so that `import core`, `import jurisdictions` etc. work
# when pytest is invoked from the project root directory.
sys.path.insert(0, str(Path(__file__).parent))
