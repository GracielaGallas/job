# job

Personal job search for Graciela (Senior QA Engineer / QA Lead, Berlin). See `README.md`.

## When asked to "run the search" ("roda a busca", "busca vagas", ...)

1. `python3 search.py` with flags matching the request (`--days`, `--berlin-only`,
   `--remote-only`, `--min-score`, `--sources`). It takes ~2 minutes.
2. Read `results/<today>.md` and reply in Portuguese with the best matches first
   (title as a markdown link, company, location, why it fits), highlighting 🆕 jobs and
   flagging ones that need fluent German. Don't paste all rows: top ~15, then mention
   how many more are in the file.
3. Commit `results/` and `data/seen.json` and push, so the next run knows what was already seen.

## Conventions

- Standard library only (Python 3.11+); no dependencies.
- Search criteria belong in `profile.toml`, not in code.
- A source that breaks must not break the run: `main()` reports it in the results header.
