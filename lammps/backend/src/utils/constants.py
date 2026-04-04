from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent
OUTPUTS_DIR = BACKEND_DIR / "outputs"
UPLOADS_DIR = BACKEND_DIR / "uploads"
HTML_DIR = PROJECT_ROOT / "frontend" / "html"
ASSETS_DIR = BACKEND_DIR / "assets"
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"

SUPPORTED_TASKS = {
    "equilibration": "NVT equilibration for a single FCC metal",
    "heating": "linear heating ramp for a single FCC metal",
}

REQUIRED_REQUEST_FIELDS = [
    "material",
    "potential_family",
    "task_type",
    "temperature",
    "steps",
]
