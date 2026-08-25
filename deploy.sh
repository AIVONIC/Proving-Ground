#!/usr/bin/env bash
#
# Publish the Proving Ground site to provingground.aivonic.ai.
#
# The docroot used to be updated with bare `scp`, which adds files and never
# removes them. Over one month that left SEVENTEEN backup copies of the site
# sitting in the web root, all publicly downloadable, including seven historical
# versions of the methodology page -- on a benchmark whose credibility rests on a
# stable, versioned methodology. nginx serves whatever is in that directory.
#
# So this deploys a MIRROR, not a patch: a staging tree is built from the manifest
# below and rsync'd with --delete, which means anything not declared here is
# removed from the server. That kills the stray-file class rather than filtering
# it with a `.bak` deny rule, which would be a blocklist and would miss
# `index-old.html`, `notes.txt` or `.env`.
#
# It refuses to publish a lander whose scorecard disagrees with the graded
# entries, it will not silently delete files you did not expect to lose, and it
# checks the live site afterwards -- so the deploy reports whether it worked
# instead of you going to look.
#
#   ./deploy.sh              # show the plan, change nothing
#   ./deploy.sh --apply      # do it
#
set -euo pipefail

HOST="root@72.62.59.75"
DOCROOT="/var/www/html/pg"
BACKUPS="/root/pg-backups"          # deliberately OUTSIDE the docroot
BASE="https://provingground.aivonic.ai"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# repo path (under frontend/) -> published path (under the docroot).
# Anything not listed is NOT published, and will be deleted from the server.
MANIFEST=(
  "index.html:index.html"
  "app.js:app.js"        # index.html's script, external so the CSP needs no hashes
  "methodology.html:methodology.html"
  "404.html:404.html"    # nginx error_page target; try_files no longer falls back to /
  "leaderboard.html:leaderboard/index.html"   # served at /leaderboard/
  "llms.txt:llms.txt"
  "robots.txt:robots.txt"
  "sitemap.xml:sitemap.xml"
  "favicon.ico:favicon.ico"
  "favicons:favicons"
  "og.png:og.png"
  "og.jpg:og.jpg"        # unreferenced since Jul 2026; kept so old social cards resolve
)
# NOT published, on purpose:
#   standalone.html  - single-file noindex variant, for sending to people directly
#
# CARRIED ACROSS THE SWAP, not published from here: scorecards/
#
# Per-agent scorecards are generated into frontend/scorecards/, which is
# gitignored because a card carries a vendor's own transcripts, and they are put
# on the server by hand when one is sent to someone. They are therefore NOT in
# the manifest -- and until 2026-08-25 that meant the next --apply silently
# deleted every one of them, including the three cards whose links are already
# out in outreach threads. Excluding them from the rsync is not enough on its
# own: step 6 swaps the WHOLE docroot, so a tree missing from $DOCROOT.new is a
# tree that disappears. They are copied across explicitly below, and step 7
# proves a known card still resolves afterwards.
SCORECARDS="scorecards"

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight
say "1. Preflight"

command -v rsync >/dev/null || fail "rsync not installed locally"
ssh -o ConnectTimeout=10 "$HOST" true || fail "cannot reach $HOST"

# A lander whose hero scorecard disagrees with entries.json must never be
# published: that card is the one number a visitor reads before anything else.
( cd "$REPO/backend" && python3 -m app.leaderboard.sync_lander --check \
    --bundle ../frontend/index.html,../frontend/app.js \
    --bundle ../frontend/standalone.html ) \
  || fail "lander scorecard is out of date with entries.json (see command above)"

( cd "$REPO/backend" && python3 -m pytest tests/test_lander_sync.py -q >/dev/null 2>&1 ) \
  || fail "lander tests fail; not publishing"
echo "   lander matches entries.json, lander tests pass"

if [[ -n "$(git -C "$REPO" status --porcelain -- frontend backend 2>/dev/null)" ]]; then
  echo "   NOTE: uncommitted changes in frontend/ or backend/ -- you are publishing them"
fi

# ------------------------------------------------------------------ staging
say "2. Build staging tree from the manifest"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
for pair in "${MANIFEST[@]}"; do
  src="$REPO/frontend/${pair%%:*}"; dst="$STAGE/${pair##*:}"
  [[ -e "$src" ]] || fail "manifest lists ${pair%%:*} but it is not in frontend/"
  mkdir -p "$(dirname "$dst")"
  cp -r "$src" "$dst"
done
echo "   $(find "$STAGE" -type f | wc -l) files staged"

# ------------------------------------------------------- what would change
say "3. Diff against the live server"

# Same flags as the real publish below, or the plan describes a deploy that will
# not happen -- which is how the ownership change that took the site down slipped
# past a dry run that had honestly displayed it.
PLAN="$(rsync -ai --checksum --no-times --omit-dir-times --delete --dry-run --no-owner --no-group --chmod=D755,F644 \
        --exclude "/$SCORECARDS/" \
        -e "ssh -o ConnectTimeout=10" "$STAGE/" "$HOST:$DOCROOT/")"
if [[ -z "$PLAN" ]]; then
  echo "   live site already matches the repo, nothing to do"; exit 0
fi
echo "$PLAN" | sed 's/^/   /'

DELETES="$(echo "$PLAN" | grep '^\*deleting' || true)"
if [[ -n "$DELETES" ]]; then
  printf '\n\033[33m   %s file(s) will be REMOVED from the server:\033[0m\n' "$(echo "$DELETES" | wc -l)"
  echo "$DELETES" | sed 's/^\*deleting   /     - /'
  echo "   (they are not in the manifest; add them there if that is wrong)"
fi

if [[ $APPLY -eq 0 ]]; then
  printf '\n   Dry run. Re-run with --apply to publish.\n'
  exit 0
fi

# ------------------------------------------------------------------ backup
say "4. Back up the live docroot (outside the docroot)"

TS="$(date +%Y%m%d_%H%M%S)"
ssh -o ConnectTimeout=10 "$HOST" "set -e
  mkdir -p '$BACKUPS'
  tar -czf '$BACKUPS/pg-docroot.$TS.tar.gz' -C '$(dirname "$DOCROOT")' '$(basename "$DOCROOT")'
  ls -la '$BACKUPS/pg-docroot.$TS.tar.gz'" || fail "backup failed, nothing was published"

# ----------------------------------------------------------------- publish
#
# Upload beside the live tree, prove it, then swap -- never rsync straight onto
# the docroot. On 2026-08-13 a direct `rsync -a` put the site down for two
# minutes: -a preserves the SOURCE's owner and mode, and `mktemp -d` makes 0700,
# so the docroot became drwx------ ubuntu and nginx could not traverse it (403,
# then 500). Verification caught it, but only after it was already serving.
#
# --no-owner --no-group --chmod pin the published permissions regardless of what
# the staging tree looks like locally, so that specific failure cannot recur; the
# swap means any OTHER failure never reaches a visitor either.
say "5. Upload beside the live site"

rsync -a --checksum --no-times --omit-dir-times --delete --no-owner --no-group --chmod=D755,F644 \
      --exclude "/$SCORECARDS/" \
      -e "ssh -o ConnectTimeout=10" "$STAGE/" "$HOST:$DOCROOT.new/" || fail "rsync failed"

# Carry the hand-published scorecards into the tree that is about to become live.
# Counted on both sides, because "copied it" is not the same claim as "it is there".
ssh -o ConnectTimeout=10 "$HOST" "set -e
  if [ -d '$DOCROOT/$SCORECARDS' ]; then
    cp -a '$DOCROOT/$SCORECARDS' '$DOCROOT.new/$SCORECARDS'
    before=\$(find '$DOCROOT/$SCORECARDS' -type f | wc -l)
    after=\$(find '$DOCROOT.new/$SCORECARDS' -type f | wc -l)
    [ \"\$before\" = \"\$after\" ] || { echo \"scorecards not carried: \$before -> \$after\"; exit 1; }
    echo \"   scorecards carried across: \$after file(s)\"
  else
    echo '   no scorecards directory on the server, nothing to carry'
  fi" || fail "could not carry scorecards across; live site untouched"

ssh -o ConnectTimeout=10 "$HOST" "set -e
  for f in index.html methodology.html leaderboard/index.html; do
    test -s '$DOCROOT.new/'\$f || { echo \"staged \$f missing or empty\"; exit 1; }
  done
  grep -q 'sc-composite' '$DOCROOT.new/index.html'
  chown -R root:root '$DOCROOT.new'
  find '$DOCROOT.new' -type d -exec chmod 755 {} +
  find '$DOCROOT.new' -type f -exec chmod 644 {} +" \
  || fail "staged tree failed its checks; live site untouched"
echo "   staged tree verified, ownership and modes pinned"

# One real card path, captured before the swap so step 7 can prove it survived.
# A card is unlisted by design, so nothing else on the site would notice its loss.
SAMPLE_CARD="$(ssh -o ConnectTimeout=10 "$HOST" \
  "ls '$DOCROOT.new/$SCORECARDS'/*.html 2>/dev/null | grep -v '/index.html$' | head -1 | xargs -r basename" || true)"

say "6. Swap"

ssh -o ConnectTimeout=10 "$HOST" "set -e
  rm -rf '$DOCROOT.prev'
  mv '$DOCROOT' '$DOCROOT.prev'
  mv '$DOCROOT.new' '$DOCROOT'" || fail "swap failed; live site is $DOCROOT (check it)"

rollback() {
  printf '\033[33m   rolling back\033[0m\n'
  ssh -o ConnectTimeout=10 "$HOST" "set -e
    rm -rf '$DOCROOT.bad'
    mv '$DOCROOT' '$DOCROOT.bad'
    mv '$DOCROOT.prev' '$DOCROOT'"
  printf '   previous tree restored; the rejected one is at %s.bad\n' "$DOCROOT"
}
echo "   swapped (previous tree kept at $DOCROOT.prev)"

# ------------------------------------------------------------------ verify
say "7. Verify the live site"

ERR=0
check() { # url  expected-code  [must-contain]
  local code body
  code="$(curl -s -o /tmp/pg_verify_body -w '%{http_code}' "$1")"
  body="$(cat /tmp/pg_verify_body)"
  if [[ "$code" != "$2" ]]; then
    printf '   \033[31mFAIL\033[0m %-46s HTTP %s (want %s)\n' "$1" "$code" "$2"; ERR=1; return
  fi
  if [[ -n "${3:-}" ]] && ! grep -q "$3" <<<"$body"; then
    printf '   \033[31mFAIL\033[0m %-46s missing %s\n' "$1" "$3"; ERR=1; return
  fi
  printf '   ok   %-46s HTTP %s\n' "$1" "$code"
}

COMP="$(cd "$REPO/backend" && python3 -c "
from app.leaderboard.store import load
from app.leaderboard.sync_lander import pick
print(f\"{pick(load(), None)['composite']:.0f}\")")"

check "$BASE/"             200 "sc-composite\">$COMP"
check "$BASE/methodology"  200 "weight"
check "$BASE/leaderboard/" 200 "lb-rank"
check "$BASE/robots.txt"   200
check "$BASE/llms.txt"     200
# The scorecards carried across the swap really are being served. Their links are
# already out in outreach threads, so a card that 404s is a broken promise to a
# vendor, and nothing else on the site would have shown it.
if [[ -n "$SAMPLE_CARD" ]]; then
  check "$BASE/$SCORECARDS/${SAMPLE_CARD%.html}" 200 "rp-title"
  check "$BASE/$SCORECARDS/"                     200 "Scorecards"
else
  printf '   --   %-46s no cards on the server to check\n' "/$SCORECARDS/"
fi

# Nothing outside the manifest may be served. A stale backup left in the docroot
# is exactly what this script exists to prevent, so prove it is gone.
# Assert the invariant directly -- no ARCHIVED page is retrievable -- rather than
# assuming a status code. Checking for "not 200" was wrong while try_files fell
# back to the homepage, which is a 200 that contains no archived content.
STRAYS=(
  "index.html.bak2.211730"
  "methodology.html.bak.20260729_212558"
  "leaderboard/index.html.bak.20260729_231717"
)
for stray in "${STRAYS[@]}"; do
  code="$(curl -s -o /tmp/pg_verify_body -w '%{http_code}' "$BASE/$stray")"
  # Markers that only ever appear in an archived copy: a rendered board, or the
  # old invented scorecard.
  if grep -qE 'lb-rank|Illustrative example|sc-composite">82' /tmp/pg_verify_body; then
    printf '   \033[31mFAIL\033[0m %-46s serves ARCHIVED content (HTTP %s)\n' "/$stray" "$code"; ERR=1
  else
    printf '   ok   %-46s HTTP %s, no archived content\n' "/$stray" "$code"
  fi
done

# Permissions are the thing that actually broke this, so assert them rather than
# trusting that the chmod above ran.
PERMS="$(ssh -o ConnectTimeout=10 "$HOST" "stat -c '%a %U' '$DOCROOT'")"
if [[ "$PERMS" != "755 root" ]]; then
  printf '   \033[31mFAIL\033[0m docroot is %s (want "755 root")\n' "$PERMS"; ERR=1
else
  printf '   ok   docroot 755 root\n'
fi

rm -f /tmp/pg_verify_body
if [[ $ERR -ne 0 ]]; then
  rollback
  fail "verification failed; the previous site is live again. Nothing to clean up by hand."
fi

say "Published and verified. Rollback tree: $DOCROOT.prev  ·  Backup: $BACKUPS/pg-docroot.$TS.tar.gz"
