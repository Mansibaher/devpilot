# DevPilot portfolio walkthrough

## Project description

DevPilot helps developers navigate unfamiliar GitHub repositories through natural-language code search. It indexes public source code in a background worker, generates code embeddings, and retrieves matching excerpts from PostgreSQL with pgvector. Its browser workspace shows filenames, line references, and links to the indexed GitHub commit.

## Resume bullet

- Built a semantic code search application with FastAPI, PostgreSQL/pgvector, and code embeddings, supporting background GitHub repository indexing and a browser interface with source citations.

Use this description only for work you can explain and demonstrate. No accuracy, user-count, or production-uptime claim is implied.

## A short interview demo

1. Open the workspace and select BrainTumorClassifier.
2. Ask: “Where is the dataset split into training and validation data?”
3. Show the result in src/utils.py and the random_split call.
4. Expand the excerpt and point out its file and line references.
5. Follow the GitHub citation to the exact indexed commit.
6. Explain the flow: repository → worker → code chunks → embeddings → database → ranked results.
7. Explain the limit: DevPilot currently finds relevant code; generated explanations and hybrid search are planned.

## Narration for the recorded demo

“This is DevPilot, my semantic code search project. I can select an indexed GitHub repository and ask a question in everyday language. Here I am asking where the dataset is split into training and validation data. DevPilot retrieves the relevant source in utils.py and shows the file and line references. I can expand the code or open its original GitHub commit. Behind the interface, a background worker chunks and embeds code, and PostgreSQL with pgvector finds relevant matches. The application currently runs locally using Docker.”

The recording shows a real successful search with explanatory captions and no audio. It does not show account passwords or tokens. Add narration using this script if desired.

## Publishing checklist

- Review the README and screenshot.
- Use the [recorded demo](../devpilot-demo.webm) hosted in this repository for portfolio links.
- Review the pending source changes before committing or pushing.
- Publish source and media only; exclude local credentials, database dumps, .env, model caches, and local launchers.
- Label the project “local demo” until a hosted deployment is verified.

## Validation evidence

During local development, the 195-test backend suite passed before the browser workspace was added. After the workspace addition, 130 unit tests, Ruff checks, and mypy passed. A subsequent real browser smoke check verified authentication, dataset-split retrieval, a commit-specific citation, responsive layout, and sign-out. These are development checks, not a production load test or a retrieval-quality benchmark.

Publication check (September 26, 2026): all 196 backend tests passed against a separate PostgreSQL test database after applying migrations through 0004. Ruff lint and formatting, mypy (58 source files), and browser JavaScript syntax checks also passed.
