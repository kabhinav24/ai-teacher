#!/usr/bin/env bash
# Publish this project to a new GitHub repository.
#
#   ./scripts/push_to_github.sh my-ai-teacher
#
# Needs the GitHub CLI (https://cli.github.com) and `gh auth login` done once.
set -euo pipefail

REPO_NAME="${1:-ai-teacher}"
VISIBILITY="${2:-public}"

cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
  git init -q
fi

# data/ holds the database, indexes and rendered video — never commit it.
git add .
git commit -qm "AI Teacher: adaptive AI educator with RAG, multilingual teaching and video generation" || \
  echo "Nothing new to commit."

if command -v gh >/dev/null 2>&1; then
  gh repo create "$REPO_NAME" --"$VISIBILITY" --source=. --remote=origin --push
  echo "Pushed. Open it with: gh repo view --web"
else
  cat <<'MSG'
GitHub CLI not found. Create the repo in the browser, then:

  git remote add origin https://github.com/<you>/<repo>.git
  git branch -M main
  git push -u origin main
MSG
fi
