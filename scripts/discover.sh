#!/usr/bin/env bash
# Find every repository in an org whose pyproject.toml mentions a package.
#
# Writes a JSON array of bare repository names to stdout. Bare names, not `owner/repo`:
# create-github-app-token's `repositories` input takes names, and callers that need the
# qualified form prefix the owner themselves.
#
# Usage: discover.sh <owner> <package> <self> [override]
#   self     repository name to exclude (the publisher's own)
#   override space-separated names to use instead of searching; an owner/ prefix is stripped
#
# Requires GH_TOKEN in the environment for `gh search code`.
set -euo pipefail

OWNER="${1:?usage: discover.sh <owner> <package> <self> [override]}"
PACKAGE="${2:?missing package}"
SELF="${3:-}"
OVERRIDE="${4:-}"

# `grep -v` exits 1 when it selects no lines, which under `pipefail` would fail the script on
# an empty result. Empty is a legitimate answer here — the caller decides whether it is an
# error — so the filters are allowed to match nothing.
if [ -n "$OVERRIDE" ]; then
  { tr ' ' '\n' <<<"$OVERRIDE" | grep -v '^$' || true; } | sed 's;.*/;;' | jq -R . | jq -sc .
  exit 0
fi

# Code search indexes default branches only, which is exactly the set worth bumping. It
# matches on text, so a pyproject.toml that merely mentions the package in a comment can
# appear; the bump step discards those once it checks the lockfiles.
#
# 1000 is the Search API's hard ceiling on total results and gh paginates up to it.
# Truncation would otherwise be silent, so the count is checked below.
HITS=$(mktemp)
trap 'rm -f "$HITS"' EXIT
gh search code --owner "$OWNER" "$PACKAGE" --filename pyproject.toml \
  --limit 1000 --json repository \
  --jq '.[].repository.nameWithOwner' | sed 's;.*/;;' >"$HITS"

if [ "$(wc -l <"$HITS")" -ge 1000 ]; then
  echo "::error::Code search returned the API's 1000-result maximum, so the dependent list may be truncated. Narrow the query or pass the repositories input." >&2
  exit 1
fi

# Filtered on the publisher's own repository name rather than on the package, which only
# happens to match it.
if [ -n "$SELF" ]; then
  { sort -u "$HITS" | grep -v "^${SELF}$" || true; } | jq -R . | jq -sc .
else
  sort -u "$HITS" | jq -R . | jq -sc .
fi
