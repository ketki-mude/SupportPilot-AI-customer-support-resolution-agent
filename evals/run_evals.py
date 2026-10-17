"""Run the evaluation dataset through the real agent graph and save a report.

    make eval          # live model; needs GOOGLE_API_KEY (prints the estimated cost first)
    make eval-smoke    # first 10 cases with the offline keyword stand-in, as CI runs it

Or inside the backend container: python /evals/run_evals.py --help
"""

import sys
from pathlib import Path

# Outside the container the backend package sits next to this folder.
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.evals.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
