# job

Personal job search for Graciela (Senior QA Engineer / QA Lead, Berlin). See `README.md`.

## When asked to "run the search" ("roda a busca", "busca vagas", ...)

1. `python3 search.py` with flags matching the request (`--days`, `--berlin-only`,
   `--remote-only`, `--min-score`, `--sources`). It takes ~2–3 minutes (it also reads each job description).
2. Read `results/<today>.md` and reply in Portuguese with the best matches first
   (title as a markdown link, company, location, why it fits), highlighting 🆕 jobs and
   flagging ones that need fluent German. Don't paste all rows: top ~15, then mention
   how many more are in the file.
3. Commit `results/` and `data/seen.json` and push, so the next run knows what was already seen.

When she says she already applied to a job (or to "the others" in a list you showed), add it
to `data/applied.json` (title, company, url, date) so it never shows up again, and commit it.

When this session can drive Graciela's own browser (Claude in Chrome or the desktop app's
built-in browser), also collect Indeed, Glassdoor and GermanTechJobs as described in
`browser/README.md` and pass them with `--extra`. Never try to get past a captcha or bot
check: ask her to solve it. In a cloud session without a browser, skip these and say so.

## Conventions

- Standard library only (Python 3.11+); no dependencies.
- Search criteria belong in `profile.toml`, not in code.
- A source that breaks must not break the run: `main()` reports it in the results header.
