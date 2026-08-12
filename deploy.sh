#!/usr/bin/env bash
# Validate, build, commit, and publish a manual update.
#
#   ./deploy.sh                 build + commit (+ push if a remote is configured)
#   ./deploy.sh "commit msg"    same, with a custom commit message
#
# Normal daily updates are handled by GitHub Actions. This helper remains useful
# for a manual content fix; Vercel deploys automatically after the push.
set -euo pipefail

cd "$(dirname "$0")"

MSG="${1:-日报更新 $(date '+%Y-%m-%d %H:%M')}"

echo "==> checking and building"
npm run check

echo "==> committing"
git add -A
if git diff --cached --quiet; then
  echo "    nothing changed, skipping commit"
else
  git commit -m "$MSG"
fi

if git remote get-url origin >/dev/null 2>&1; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  echo "==> pushing to origin/$BRANCH"
  git push origin "$BRANCH"
  echo "==> done, Vercel will pick it up"
else
  echo "==> no 'origin' remote configured, stopped after the local commit"
  echo "    to publish:  git remote add origin <repo-url> && git push -u origin main"
fi
