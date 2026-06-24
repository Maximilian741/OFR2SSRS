# Contributing to Oracle2SSRS

Thanks for your interest in contributing! This document explains how to set up
your development environment, run the test suite, and submit a pull request.

## Setup

1. Fork and clone the repository:

   ```bash
   git clone https://github.com/<your-username>/Oracle2SSRS.git
   cd Oracle2SSRS
   ```

   (Local checkouts may use the working name `HackathonOracle2SSRS` — both
   refer to the same project.)

2. (Recommended) create a virtual environment:

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS / Linux
   source .venv/bin/activate
   ```

3. Launch the application to verify your setup:

   - Windows: `run.bat`
   - macOS / Linux: `./run.sh`

   The launcher installs the dependencies from `requirements.txt` itself and
   then starts the Flask app on `http://127.0.0.1:5057`. If you would rather
   install manually (for example, to run the tests without starting the app):

   ```bash
   pip install -r requirements.txt
   ```

   The dependency surface is intentionally small: Flask, lxml, python-docx,
   the Anthropic SDK (for the optional Claude assist), and pytest + pypdf for
   the test/render-verification path. Python 3.9 or newer is required.

## Running tests

The project uses [pytest](https://docs.pytest.org/). From the project root:

```bash
python -m pytest -q
```

The suite currently reports **620 passed, 19 skipped** (639 collected). The
skipped tests are render-verification cases that only run when the
RenderLab MS-engine host is available on the machine; they are skipped
cleanly elsewhere rather than failing.

Generated RDL is validated against Microsoft's own RDL 2008 XSD
(`tests/fixtures/schema/ReportDefinition_2008.xsd`, loaded with
`lxml.etree.XMLSchema`), so a change that produces schema-invalid RDL will
fail the suite.

Please make sure all tests pass before opening a pull request, and add tests
for any new behavior you introduce. Fixtures must be name-agnostic — see the
note on customer data below.

## Code style

- Follow [PEP 8](https://peps.python.org/pep-0008/) for all Python code.
- Use type hints on new and modified function signatures.
- Keep functions small and focused; prefer pure functions where possible.
- Do not introduce new third-party dependencies without first discussing the
  change in an issue. Keeping the dependency surface small is a project goal.
- Match the existing project conventions for naming, imports, and module
  layout. When in doubt, look at neighboring files.

## No customer data, ever

This is a public repository; the Oracle reports it is used against in the
field are private. Real report names, table names, column names, and bind
variables must **not** appear anywhere in the code, tests, fixtures, docs, or
commit messages. Test fixtures are name-agnostic and parametrized — when you
add a test case, use a synthetic report shape, not a real one. A PR that
introduces identifiable customer data will not be merged.

## Pull request process

1. Create a feature branch off of `master`:

   ```bash
   git checkout -b feature/short-description
   ```

2. Make your changes in small, logically grouped commits with clear messages.
3. Run `python -m pytest -q` locally and confirm all tests pass.
4. Update documentation (README, docstrings, examples) when behavior changes.
5. Push your branch and open a pull request against `master`.
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

## License

This project is licensed under the Elastic License 2.0 (see `LICENSE`). By
submitting a contribution you agree that it will be licensed under the same
terms.

Thanks again for contributing!
