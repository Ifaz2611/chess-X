# Contributing to Chess-X

Thanks for your interest in improving Chess-X.

This project is built for educational, local, and private-board use. It is not intended to support cheating or unfair play on online rated games. Please keep contributions aligned with the project goals in the [README](README.md), the site policies of chess.com and lichess.org, and the safety guidance in [SECURITY.md](SECURITY.md).

## Project goals

- Improve board detection, engine integration, overlays, and automation tooling
- Support local analysis, puzzles, private boards, and bot-vs-bot use cases
- Keep the codebase readable, testable, and maintainable
- Respect platform rules and avoid features that encourage abuse

## Before you contribute

1. Read the [README](README.md) and relevant module code.
2. Check whether an issue already exists before opening a new one.
3. Keep changes narrowly scoped and easy to review.
4. If you touch browser selectors or board parsing, validate behavior against both chess.com and lichess.org.
5. Do not propose or implement features that bypass fair-play rules or encourage cheating against human opponents.

## Local setup

```bash
git clone https://github.com/Ifaz2611/chess-X
cd chess-X
python -m venv venv

# Windows
venv\Scripts\pip.exe install -r requirements.txt

# Linux / macOS
venv/bin/pip install -r requirements.txt
```

## Development workflow

- Use the existing project style and keep formatting consistent with `ruff` and `black`
- Keep commit messages clear and focused
- Add or update tests when behavior changes
- Update user-facing documentation when needed

## Validation

Run the smallest relevant checks before opening a pull request:

```bash
pytest -q
ruff check .
mypy src
```

If you modify grabbers or site-specific logic, test against both supported sites when possible. For GUI or overlay changes, include a brief description and screenshots or a short GIF when it helps explain the change.

## Issue reporting

Please open a GitHub issue for:

- bugs
- missing features
- documentation problems
- questions about usage or project direction

When reporting bugs, include:

- OS and Python version
- Chrome/Chromium version
- Stockfish version
- the exact reproduction steps
- relevant logs or screenshots
- whether the issue affects chess.com, lichess.org, or both

## Pull request checklist

Before submitting a pull request, confirm the following:

- [ ] The change is focused and does not mix unrelated work
- [ ] Tests or targeted validation were run
- [ ] Style checks pass (`ruff`, `black`, and mypy as applicable)
- [ ] Documentation was updated if the behavior is user-facing
- [ ] The PR description explains the rationale and testing performed
- [ ] The work does not promote online cheating or unfair play

## Code of conduct and safety

We expect contributors to follow the project's [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). We also ask contributors to keep security and ethical concerns in mind, especially when working with browser automation or any code that interacts with third-party game sites.

Thank you for helping improve Chess-X responsibly.
