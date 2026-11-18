#!/usr/bin/env bash
# PreToolUse hook (Bash): enforce the worktree workflow from CLAUDE.md.
# Denies: switching branches in place, committing on main, pushing to main.
set -euo pipefail

input=$(cat)
cmd=$(jq -r '.tool_input.command // ""' <<<"$input")
cwd=$(jq -r '.cwd // "."' <<<"$input")

deny() {
  jq -n --arg r "$1" '{hookSpecificOutput: {hookEventName: "PreToolUse",
    permissionDecision: "deny", permissionDecisionReason: $r}}'
  exit 0
}

# Only look at git invocations.
grep -Eq '(^|[;&|[:space:]])git[[:space:]]' <<<"$cmd" || exit 0

if grep -Eq 'git[[:space:]]+switch([[:space:]]|$)' <<<"$cmd" ||
   grep -Eq 'git[[:space:]]+checkout[[:space:]]+(-b[[:space:]]+|-B[[:space:]]+)?[^-[:space:]]' <<<"$cmd"; then
  deny "Do not switch branches in this directory. Create a worktree outside the repo: git worktree add ../<repo>-<feature> -b <feature> (see CLAUDE.md)."
fi

branch=$(git -C "$cwd" branch --show-current 2>/dev/null || true)

if [[ "$branch" == "main" ]] && grep -Eq 'git[[:space:]]+commit([[:space:]]|$)' <<<"$cmd"; then
  deny "Do not commit on main. Work in a worktree on a feature branch and open a PR (see CLAUDE.md)."
fi

if grep -Eq 'git[[:space:]]+push([[:space:]]|$)' <<<"$cmd"; then
  if grep -Eq 'git[[:space:]]+push.*([[:space:]:]|^)main([[:space:]]|$)' <<<"$cmd" ||
     { [[ "$branch" == "main" ]] && ! grep -Eq 'git[[:space:]]+push.*[[:space:]][^-[:space:]]+[[:space:]]+[^-[:space:]]' <<<"$cmd"; }; then
    deny "Do not push to main. Push a feature branch and open a PR (see CLAUDE.md)."
  fi
fi

exit 0
