# OakNationalDB

Build a local mirror of the Oak National Academy AQA Secondary Science curriculum.

This repository contains a modular pipeline to download, cache, normalise and store curriculum data from the Oak National Academy Open API into a local SQLite database.

Quick start

1. Create a Python 3.12+ virtual environment and install dependencies:

   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

2. Set your API key (once you have it) as an environment variable:

   export OAK_API_KEY="your_api_key_here"

3. Create the database schema (this will create database/curriculum.db):

   python setup_database.py

4. Run the downloader (will use cached responses when available):

   python commands/download.py

Design notes

- The importer is the only component that knows about the Oak API.
- All API responses are cached under data/cache/.
- SQLite is the canonical source of truth and stores normalised entities.

See docs in the README and code comments for extension points.

## Forces quiz prototype

The **Generate Forces quiz prototype** workflow creates up to five cached,
15-20-question quizzes from the `1ForcesPhysics` learning points. Before
running it, add `GEMINI_API_KEY` as a repository Actions secret. The workflow
uses `gemini-3.5-flash` by default, waits 60 seconds between requests, commits
the generated JSON files under `site/quizzes/forces/`, and deploys the static
site. The "Generate quiz" button on the Key Learning Points page becomes
available for the Forces unit once those files have been published.

The **Generate all quiz caches** workflow extends this to every unit. It skips
valid existing quiz files, generates five quizzes per incomplete unit, and
commits/deploys a checkpoint after each five-unit batch. Refreshing the Pages
site after a checkpoint exposes the newly available units while later batches
continue running. It defaults to 25 concurrent Gemini requests and resumes
incomplete units after any failed run.
