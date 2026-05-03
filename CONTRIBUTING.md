# Contributing to Oracle2SSRS

Thanks for your interest in contributing! This document explains how to set up
your development environment, run the test suite, and submit a pull request.

## Setup

1. Fork and clone the repository:

   ```bash
   git clone https://github.com/<your-username>/Oracle2SSRS.git
   cd Oracle2SSRS
   ```

2. (Recommended) create a virtual environment:

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS / Linux
   source .venv/bin/activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Launch the application to verify your setup:

   - Windows: `run.bat`
   - macOS / Linux: `./run.sh`

## Running tests

The project uses [pytest](https://docs.pytest.org/). From the project root:

```bash
pytest
```

For quieter output and short tracebacks:

```bash
pytest --tb=short -q
```

Please make sure all tests pass before opening a pull request, and add tests
for any new behavior you introduce.

## Code style

- Follow [PEP 8](https://peps.python.org/pep-0008/) for all Python code.
- Use type hints on new and modified function signatures.
- Keep functions small and focused; prefer pure functions where possible.
- Do not introduce new third-party dependencies without first discussing the
  change in an issue. Keeping the dependency surface small is a project goal.
- Match the existing project conventions for naming, imports, and module
  layout. When in doubt, look at neighboring files.

## Pull request process

1. Create a feature branch off of `main`:

   ```bash
   git checkout -b feature/short-description
   ```

2. Make your changes in small, logically grouped commits with clear messages.
3. Run `pytest` locally and confirm all tests pass.
4. Update documentation (README, docstrings, examples) when behavior changes.
5. Push your branch and open a pull request against `main`.
6. Fill out the pull request template completely. Link any related issues.
7. A maintainer will review your PR. Please respond to feedback promptly and
   push follow-up commits to the same branch (do not force-push during review
   unless asked).
8. Once approved and CI is green, a maintainer will merge your PR.

## Reporting bugs and requesting features

Please use the GitHub issue templates:

- Bug report: describe the environment, reproduction steps, expected vs actual
  behavior.
- Feature request: describe the problem you are trying to solve and the
  proposed solution.

Thanks again for contributing!
