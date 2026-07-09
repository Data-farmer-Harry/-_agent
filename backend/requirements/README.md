# Backend dependency profiles

The project uses one named Conda environment, `lammps_agent`, instead of a
repository-local `.venv`.

## Will `.venv` be pushed to Git?

No. The repository `.gitignore` ignores local virtual environments:

- `.venv/`
- `**/.venv/`
- `venv/`
- `env/`

You can verify this before pushing:

```bash
git status --ignored
git ls-files | rg '(^|/)\.venv(/|$)|(^|/)venv(/|$)|(^|/)env(/|$)'
```

The second command should print nothing. If it ever prints a virtual
environment path, stop and remove it from Git before pushing.

## Complete environment

From the repository root:

```bash
conda env create -f backend/requirements/environment.yml
conda run -n lammps_agent python -m pip install -r requirements.txt
```

For an existing environment:

```bash
conda env update -n lammps_agent -f backend/requirements/environment.yml
conda run -n lammps_agent python -m pip install -r requirements.txt
```

Interactive use:

```bash
conda activate lammps_agent
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Profiles

- `environment.yml`: creates/updates the named `lammps_agent` Conda environment
  with Python 3.12 and pip only. It intentionally does not install large binary
  packages automatically.
- `base.txt`: FastAPI, LangGraph, RAG, pycalphad and scientific runtime.
- `visualization.txt`: the large optional OVITO/PySide visualization stack.
- `dev.txt`: pytest and HTTP test client dependencies.
- `all.txt`: installs every profile and preserves all current capabilities.
- `../../requirements.txt`: repository-root shortcut that points to
  `backend/requirements/all.txt`.

Install a lighter backend-only environment without OVITO media rendering:

```bash
conda run -n lammps_agent python -m pip install -r backend/requirements/base.txt
conda run -n lammps_agent python -m pip install -r backend/requirements/dev.txt
```

On Apple Silicon, the conda-forge `ovito` package provides the desktop/console
executable but not the Python scripting module used by this backend. Keep the
pip `ovito` profile in this environment; the project validates it with a real
trajectory-rendering contract test.

## External executables

Python packages do not replace these host tools:

- LAMMPS executable, configured through `LAMMPS_CMD` or the backend runtime UI;
- `ffmpeg` for browser-friendly video conversion;
- EAM potential files configured through `POTENTIALS_DIR`;
- an optional `ovitos` executable can replace the Python OVITO package.

If you want to install LAMMPS into the same Conda environment later, run it
manually when network/downloads are acceptable:

```bash
conda install -n lammps_agent -c conda-forge lammps ffmpeg
```

Then configure:

```bash
LAMMPS_CMD=/opt/anaconda3/envs/lammps_agent/bin/lmp
```

Environment variables and API keys remain in the ignored `backend/.env` file.
Do not add secrets to any requirements file.

## Removing old local virtual environments

If a repository-local virtual environment appears later, remove only the local
directory; do not commit it:

```bash
rm -rf .venv backend/.venv venv backend/venv env backend/env
```

Then use `lammps_agent` for all backend commands:

```bash
conda run -n lammps_agent python -m pytest
```
