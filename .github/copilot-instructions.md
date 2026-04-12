# Copilot Instructions

- Use Docker-only workflows for this repository.
- Never create a Python virtual environment on the host.
- Never run `pip install`, `python -m venv`, `pytest`, or other Python tooling directly on the host system.
- Use [run.sh](../run.sh) for all common operations.
- Prefer these commands:
  - `./run.sh up`
  - `./run.sh down`
  - `./run.sh sync`
  - `./run.sh unit-test`
  - `./run.sh test`
  - `./run.sh dev-shell`
- If a new development or test workflow is needed, add it to [run.sh](../run.sh) rather than documenting a host-level command.
- When editing or validating code, assume the repository is mounted into the dev container via Docker Compose.
- Keep source code editable from the host, but execute Python inside containers only.