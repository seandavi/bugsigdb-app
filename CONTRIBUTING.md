# Contributing

Thanks for helping. Bug reports, ideas, and pull requests are all welcome.
Everyone taking part agrees to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Where things go

| You found… | Where to report it |
|---|---|
| A bug, a confusing page, a broken link, a slow query | [Issues here](https://github.com/seandavi/bugsigdb-app/issues/new/choose) |
| An idea for a page, API endpoint, MCP tool, or analysis | [Issues here](https://github.com/seandavi/bugsigdb-app/issues/new/choose) |
| A wrong signature, taxon, condition, or study detail | [BugSigDB](https://bugsigdb.org): this app only mirrors its exports |
| A wrong physiology trait | [bugphyzz issues](https://github.com/waldronlab/bugphyzz/issues) |

Data fixed upstream shows up here after the next load.

## Making a change

1. **Open or find an issue first** for anything beyond a small fix, so we can agree on the approach
   before you write code.
2. Fork, then branch from `main`.
3. Set up and run it locally (needs Docker, [uv](https://docs.astral.sh/uv/) and [just](https://just.systems)):

   ```sh
   just up      # Postgres + app in Docker; app on http://127.0.0.1:8042
   just load    # fetch the public exports into Postgres (~5 s)
   just dev     # optional: app with auto-reload on http://127.0.0.1:8043
   just test
   ```

4. Keep the change focused, add or update a test when you change logic (`tests/`), and check the
   pages you touched in a browser, light and dark, desktop and phone width.
5. Open a pull request that says what changed and why, and links the issue (`Closes #123`).

## How the code is organized

- `src/bugsigdb_app/load.py` fetches the exports and builds the database in a staging schema,
  then swaps it in. Data shaping and quirks of the exports live here.
- `src/bugsigdb_app/queries.py` holds every read query. The HTML pages, the REST API and the
  MCP tools all call these functions, so a new capability usually starts here.
- `src/bugsigdb_app/app.py` (REST + MCP) and `web.py` + `templates/` (HTML) are thin layers on top.
- `static/` has one stylesheet and one small script; there is no frontend build step.

The app is read-only by design: [BugSigDB](https://bugsigdb.org) is the source of truth, and each
load replaces the database.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
