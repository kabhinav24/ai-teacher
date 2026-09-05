# Putting this on GitHub

## 1. Push the repository

The repo is already initialised and committed, so this is one command if you have the
[GitHub CLI](https://cli.github.com):

```bash
./scripts/push_to_github.sh my-ai-teacher public
```

Without `gh`, create an empty repository in the browser (no README, no .gitignore — this
repo already has both), then:

```bash
git remote add origin https://github.com/<you>/<repo>.git
git branch -M main
git push -u origin main
```

`data/` is gitignored. It holds the SQLite database, the vector indexes and rendered videos,
and must never be committed.

## 2. What runs automatically

Pushing to `main` or opening a pull request triggers `.github/workflows/ci.yml`. Nothing
needs configuring — every job runs on the offline providers, so no secrets are required for
the pipeline to go green.

```
push / PR
   ├── test      pytest on 3.11 + 3.12, coverage artifact
   ├── lint      ruff (advisory, never blocks)
   ├── frontend  vite build, dist artifact
   ├── pipeline  ← needs test
   │             teaches a real lesson from samples/, renders lesson.mp4,
   │             ffprobes it, uploads video + subtitles + chapters
   └── docker    ← needs test
                 build image → boot it → poll /health → push to GHCR (main only)
```

The **pipeline** job is the one worth watching. It isn't a unit test — it runs the whole
product end to end and then verifies the artifact is a real playable video, which is the
check that would catch a broken ffmpeg filter graph or a renderer that silently produces an
empty frame.

## 3. Optional secrets

| Secret | Needed for | Without it |
|---|---|---|
| `LLM_API_KEY` | Real lesson content in the manual demo workflow | Falls back to the stub provider with a warning |

Add under **Settings → Secrets and variables → Actions → New repository secret**.

`GITHUB_TOKEN` is provided automatically and is what the Docker job uses to push to the
GitHub Container Registry — you don't create it.

## 4. Generating your demo video

**Actions → Generate demo lesson → Run workflow.** Choose topic, language, level and
duration. When it finishes, download the `demo-lesson-<language>` artifact: it contains
`lesson.mp4`, `lesson.srt`, `chapters.json` and the full teaching transcript including the
decision log.

The run summary page also prints the decision trace, which is the fastest way to show
someone that the teacher adapts rather than recites.

## 5. Enabling the container registry

The first push to `main` publishes `ghcr.io/<you>/<repo>:latest`. Packages default to
private; to make the image pullable, open the package from your profile → **Package
settings** → **Change visibility**. Then anyone can run it:

```bash
docker run -p 8000:8000 -e LLM_PROVIDER=echo ghcr.io/<you>/<repo>:latest
```

## 6. Branch protection (optional)

If you're collaborating, **Settings → Branches → Add rule** on `main`, requiring the `test`,
`pipeline` and `frontend` checks. Leave `lint` out — it's deliberately advisory, so requiring
it would let a formatting nit block a working merge.

## 7. Fixing the badges

The README badges point at `OWNER/REPO`. After pushing:

```bash
sed -i 's|OWNER/REPO|<you>/<repo>|g' README.md
git commit -am "Point CI badges at the real repository" && git push
```

## Troubleshooting

**`lint` job shows a red cross but CI is green.** Working as designed — it's
`continue-on-error`. Clear the findings with `make lint-fix`.

**`frontend` job fails on `npm ci`.** It falls back to `npm install` when there's no
lockfile. Commit one to make builds reproducible:

```bash
cd frontend && npm install && git add package-lock.json && git commit -m "Add lockfile"
```

**`docker` job fails to push with a permissions error.** Check **Settings → Actions →
General → Workflow permissions** is set to read *and write*.

**`pipeline` job times out.** Video rendering is CPU-bound. The sample lesson takes about a
minute on a standard runner; if you raise `--minutes` in the workflow, raise the job timeout
with it.
