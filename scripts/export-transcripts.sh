#!/usr/bin/env bash
# Copies this project's AI-session transcripts into transcripts/ (required by the brief:
# "No transcripts means zero for the AI-usage part"). Run it again just before submitting:
#   bash scripts/export-transcripts.sh
# Covers sessions started in the project folder AND in its worktrees (.claude/worktrees/*).
# OpenCode sessions are NOT files on disk: export each one from the OpenCode CLI/TUI with
#   opencode export
# and save the result into transcripts/ as well.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/transcripts"
mkdir -p "$DEST"

# Where Claude Code stores sessions depends on the shell: plain Windows, Git Bash ($HOME=/c/Users/..)
# and WSL ($HOME=/root) disagree. Probe the known locations and use every one that exists.
SRC_CANDIDATES=(
  "${CLAUDE_PROJECTS_DIR:-}"
  "$HOME/.claude/projects"
  /mnt/c/Users/*/.claude/projects
  /c/Users/*/.claude/projects
)

count=0
for src in "${SRC_CANDIDATES[@]}"; do
  [ -n "$src" ] || continue
  for dir in "$src"/*Acko-Assignment*; do
    [ -d "$dir" ] || continue
    for file in "$dir"/*.jsonl; do
      [ -f "$file" ] || continue
      # prefix with the folder name so sessions from the project and its worktrees don't collide
      cp "$file" "$DEST/claude__$(basename "$dir")__$(basename "$file")"
      count=$((count + 1))
    done
  done
done
echo "Copied $count Claude session file(s) into $DEST"
if [ "$count" -eq 0 ]; then
  echo "WARNING: 0 files. If you used Claude Code elsewhere, set CLAUDE_PROJECTS_DIR and rerun."
fi
echo "Next: export OpenCode sessions (stored in the OpenCode service, not on disk):"
echo "  opencode session list                      # shows <session-id> and title"
echo "  opencode session export <session-id> > transcripts/opencode__<session-id>.json"
echo "Before committing: skim them for secrets (none should exist; only demo credentials were used)."
echo "Also save exports of any other AI chats you used into transcripts/ (the brief requires all AI use)."
