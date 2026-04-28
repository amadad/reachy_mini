# Contributing

## Code quality

The quality of code is insured by ruff and mypy. Please make sure to run them before pushing your code.

All the tools are available in the [dev] dependency group, so you can install them with:

```bash
uv sync --group dev
```

### Ruff

A good practice is to install the pre-commit hook to run ruff on the staged files before pushing. You can do it by running the following command at the root of the repository:

```bash
pre-commit install
```

or you can run ruff manually with:

```bash
ruff check .
```

### Mypy

To check the type annotations of the code, you can run mypy with the following command:

```bash
mypy
```

Please note that mpy results depend on the installed package. The [CI](../.github/workflows/lint.yml) runs mypy with the full installation with uv on linux. Any differences between the CI and the local environment are probably due to missing dependencies. To ensure you have the same environment as the CI, you can install the package with all the extras with uv:

```bash
uv sync --all-extras --group dev
```

## Code testing

The code is tested with pytest. For a local no-hardware pass, run the same marker subset as CI:

```bash
uv run pytest -m "not audio and not video and not wireless"
```

Tests marked `audio`, `video`, or `wireless` require the matching hardware or network setup. Optional-backend tests such as Placo are skipped when the extra is not installed.

For instance, to run audio-specific tests:

```bash
uv run pytest -m "audio"
```
