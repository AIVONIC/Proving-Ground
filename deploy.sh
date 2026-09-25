#!/usr/bin/env bash
#
# Publish the Proving Ground site to theprovingground.io (also served on the
# original provingground.aivonic.ai, same nginx block and certificate).
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
BASE="https://theprovingground.io"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# repo path (under frontend/) -> published path (under the docroot).
# Anything not listed is NOT published, and will be deleted from the server.
MANIFEST=(
  "index.html:index.html"
  "app.js:app.js"        # index.html's script, external so the CSP needs no hashes
  "methodology.html:methodology.html"
  "404.html:404.html"    # nginx error_page target; try_files no longer falls back to /
  "leaderboard.html:leaderboard/index.html"   # served at /leaderboard/
  "cohort.html:cohort.html"                  # /cohort - the reference-cohort finding
  "llms.txt:llms.txt"
  "robots.txt:robots.txt"
  "sitemap.xml:sitemap.xml"
  "certs.json:certs.json"   # the public certificate index; the service reads this file
  "favicon.ico:favicon.ico"
  "favicons:favicons"
  "og.png:og.png"
  "og.jpg:og.jpg"        # unreferenced since Jul 2026; kept so old social cards resolve
  "taxonomy.html:taxonomy.html"   # joint taxonomy with Inquio; gated on .taxonomy-approved
)
# NOT published, on purpose:
#   standalone.html  - single-file noindex variant, for sending to people directly
#   taxonomy.html    - PUBLISHED 2026-09-23. Was embargoed: it is a joint taxonomy with
#                      Inquio and the dimension descriptions are OUR wording over Martin
#                      Franc's contribution, so publishing it was publishing his work as
#                      we had phrased it. He has now reviewed the dimensions AND the probe
#                      definitions and signed off; frontend/.taxonomy-approved records who
#                      and when, and what he changed. THE GUARD BELOW STAYS. The reason
#                      outlives this one approval: a MANIFEST nobody re-reads is one line
#                      away from publishing the NEXT revision of his contribution unread.
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

# ⛔ AND THE LEADERBOARD PAGE, which the check above does NOT cover.
#
# The lander check validates index/standalone. leaderboard.html is not validated
# by anything: deploy.sh merely COPIES it from the manifest. So on 2026-09-12 a
# re-grade could be promoted, the lander resynced, and the whole site published
# with a corrected homepage sitting on top of a two-day-old board -- and the
# preflight would have passed, because it was asked about a different file. That
# page is the one people link to and the one a graded vendor checks.
#
# render.py has no clock and no randomness, so re-rendering into a temp file and
# diffing is an exact staleness test rather than a heuristic (verified
# byte-identical on 2026-09-12).
_lb_tmp="$(mktemp -t pg-leaderboard-XXXXXX.html)"
# ⛔ --report-dir MUST match how the published page is rendered. Without it the
# check renders rows with no scorecard links, so it differs from the real file
# every time and the guard fails on a correct page - a check that cannot pass is
# a check that gets disabled.
( cd "$REPO/backend" && python3 -m app.leaderboard.render \
    --lander ../frontend/index.html --report-dir ../frontend/scorecards \
    --out "$_lb_tmp" >/dev/null 2>&1 ) \
  || { rm -f "$_lb_tmp"; fail "could not re-render the leaderboard to check it; this is 'could not look', not a pass"; }
if ! diff -q "$_lb_tmp" "$REPO/frontend/leaderboard.html" >/dev/null 2>&1; then
  rm -f "$_lb_tmp"
  fail "leaderboard.html is out of date with entries.json. Fix:
     cd backend && python3 -m app.leaderboard.render --lander ../frontend/index.html --report-dir ../frontend/scorecards --out ../frontend/leaderboard.html"
fi

# certs.json is what /verify and /badge resolve against, so a stale one does not
# merely look wrong -- it answers a buyer's question about a vendor with the
# wrong grade, or reports a real certificate as never issued. Same diff-preflight
# as the leaderboard above: regeneration is deterministic, so any difference means
# entries.json moved and this file did not.
_ct_tmp="$(mktemp)"; trap 'rm -f "$_lb_tmp" "$_ct_tmp"' EXIT
( cd backend && python3 -m app.leaderboard.certs --out "$_ct_tmp" >/dev/null ) \
  || fail "could not regenerate the certificate index"
if ! diff -q <(python3 -c 'import json,sys;print(json.dumps(json.load(open(sys.argv[1])),sort_keys=True))' "$_ct_tmp") \
             <(python3 -c 'import json,sys;print(json.dumps(json.load(open(sys.argv[1])),sort_keys=True))' "$REPO/frontend/certs.json") >/dev/null 2>&1; then
  fail "frontend/certs.json is stale against the leaderboard. Regenerate:
     cd backend && python3 -m app.leaderboard.certs --out ../frontend/certs.json"
fi
rm -f "$_lb_tmp"

( cd "$REPO/backend" && python3 -m pytest tests/test_lander_sync.py -q >/dev/null 2>&1 ) \
  || fail "lander tests fail; not publishing"
# The certificate contract gates the publish too: a deploy that changes entries.json
# changes what every /verify URL answers, and the expired-vs-unknown distinction
# fails silently -- nothing errors, no page looks wrong, a buyer just cannot tell a
# lapsed grade from a typo.
( cd "$REPO/backend" && python3 -m pytest tests/test_certificates.py -q >/dev/null 2>&1 ) \
  || fail "certificate tests fail; not publishing"
# The critical-failure bar decides whether a composite is capped from 86 to 40,
# which is the harshest verdict this benchmark issues. It was knife-edge once
# already (exfiltration critical at <= 0.25, reachable by a single dissenting
# judge), so it is gated rather than trusted.
( cd "$REPO/backend" && python3 -m pytest tests/test_critical_threshold.py -q >/dev/null 2>&1 ) \
  || fail "critical-threshold tests fail; not publishing"
# Squeezed paragraphs were reported four separate times, each fixed one element
# at a time, because the defect is structural: prose capped per element inside a
# box sized independently. The container is the measure now, and this gate keeps
# it that way - including on the GENERATED pages, where a source fix that was
# never re-rendered is the usual way it comes back.
( cd "$REPO/backend" && python3 -m pytest tests/test_prose_width.py -q >/dev/null 2>&1 ) \
  || fail "prose-width gate fails; a prose element is capping its own width"
# An agent withheld pending vendor disclosure must not reach ANY public surface.
# The board honoured the flag immediately; /cohort and the scorecard index did
# not, and /cohort was publishing the composite, the critical-failure count and a
# sentence naming what the agent complied with. Asserts on the OUTPUT, because a
# surface can call the right function and still print the name.
( cd "$REPO/backend" && python3 -m pytest tests/test_withheld_not_published.py -q >/dev/null 2>&1 ) \
  || fail "a withheld agent appears on a public surface; not publishing"
# A canonical or sitemap entry pointing at a URL that 301s is silently wrong: the
# page returns 200, the redirect works, the XML is valid, and Google indexes
# neither. Nothing else on this site would surface it.
( cd "$REPO/backend" && python3 -m pytest tests/test_seo_integrity.py -q >/dev/null 2>&1 ) \
  || fail "canonical or sitemap points at a redirect; not publishing"
# A published page must be a DOCUMENT, not a fragment. The lander was served as a
# bare fragment (no doctype, no <html>, no <head>, no <body>) for some time: every
# browser rendered it correctly, and LinkedIn's preview fetcher would not build a
# card for it. It was repaired on the SERVER on 2026-09-16 and the repair did not
# come back to the repo, so this deploy would have overwritten the fix with the
# fragment again -- rsync has no opinion about whether a file is a valid document.
( cd "$REPO/backend" && python3 -m pytest tests/test_document_structure.py -q >/dev/null 2>&1 ) \
  || fail "a published page is not a complete HTML document; not publishing"

# ⛔ THE EMBARGOED TAXONOMY MAY NOT REACH THE PUBLIC SITE.
#
# The manifest is a whitelist, so leaving taxonomy.html out of it is already enough to
# keep it off the server. This asserts it anyway, because "we left it out" is a fact
# about today's file and the thing being protected is an agreement with another party:
# the cost of a mistake is not a broken page, it is publishing a named collaborator's
# contribution in our words before he has read it. A whitelist nobody re-reads is one
# line away from including it.
#
# To publish it: get Martin's sign-off, create frontend/.taxonomy-approved recording WHO
# approved it and WHEN, and add taxonomy.html to the MANIFEST above.
if printf '%s\n' "${MANIFEST[@]}" | grep -q '^taxonomy\.html:'; then
  if [[ ! -f "$REPO/frontend/.taxonomy-approved" ]]; then
    fail "taxonomy.html is in the MANIFEST but frontend/.taxonomy-approved does not exist.
     The taxonomy is embargoed pending Martin Franc's review of the dimension descriptions
     and probe definitions. He approved the DISCLOSURE text only."
  fi
  echo "   taxonomy approved for publication by: $(head -1 "$REPO/frontend/.taxonomy-approved")"
fi
echo "   lander AND leaderboard match entries.json, lander + certificate tests pass"

if [[ -n "$(git -C "$REPO" status --porcelain -- frontend backend 2>/dev/null)" ]]; then
  echo "   NOTE: uncommitted changes in frontend/ or backend/ -- you are publishing them"
fi

# ------------------------------------------------------------------ staging
say "2. Build staging tree from the manifest"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"; rm -f "$_lb_tmp" "$_ct_tmp"' EXIT
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
  for f in index.html methodology.html leaderboard/index.html cohort.html; do
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
# Take the sample from what the rendered BOARD links to, not from `ls | head -1`.
# Alphabetical order picked crewai-northwind-782bf0c50b47, a SUPERSEDED card that
# nginx now 301s to its current replacement - so this check failed on a 301 and
# rolled back a perfectly good deploy. The board only ever links to current
# cards, so deriving the sample from it cannot pick a redirect source, and it
# stays correct as slugs change without anyone maintaining a list.
SAMPLE_CARD="$(ssh -o ConnectTimeout=10 "$HOST" \
  "grep -o '/$SCORECARDS/[a-z0-9-]*' '$DOCROOT.new/leaderboard/index.html' 2>/dev/null \
   | head -1 | sed 's|.*/||'" || true)"
# Fall back to the directory listing only if the board carries no card links at
# all, so a board rendered without --report-dir still checks something.
if [[ -z "$SAMPLE_CARD" ]]; then
  SAMPLE_CARD="$(ssh -o ConnectTimeout=10 "$HOST" \
    "grep -L 'pg:superseded' '$DOCROOT.new/$SCORECARDS'/*.html 2>/dev/null | grep -v '/index.html$' | head -1 | xargs -r basename" || true)"
fi

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
check "$BASE/cohort"       200 "co-title"
check "$BASE/robots.txt"   200
check "$BASE/llms.txt"     200
# The scorecards carried across the swap really are being served. Their links are
# already out in outreach threads, so a card that 404s is a broken promise to a
# vendor, and nothing else on the site would have shown it.
if [[ -n "$SAMPLE_CARD" ]]; then
  check "$BASE/$SCORECARDS/${SAMPLE_CARD%.html}" 200 "rp-title"
  check "$BASE/$SCORECARDS/"                     200 "Scorecards"
  # A superseded card must REDIRECT, never 404: three of those links are out in
  # outreach threads. Follow it and assert we land on a real card.
  _old="crewai-northwind-782bf0c50b47"
  _code="$(curl -s -o /tmp/pg_verify_body -w '%{http_code}' -L "$BASE/$SCORECARDS/$_old")"
  # A superseded card is now a redirect STUB (one score per card, the latest), and
  # curl does not follow a meta refresh, so follow exactly one stub hop by hand.
  if [[ "$_code" == "200" ]] && grep -q "pg:superseded" /tmp/pg_verify_body; then
    _next="$(grep -o 'url=/scorecards/[a-z0-9-]*' /tmp/pg_verify_body | head -1 | cut -d= -f2)"
    _code="$(curl -s -o /tmp/pg_verify_body -w '%{http_code}' -L "$BASE$_next")"
  fi
  if [[ "$_code" == "200" ]] && grep -q "rp-title" /tmp/pg_verify_body; then
    printf '   ok   %-46s superseded link redirects to a live card\n' "/$SCORECARDS/$_old"
  else
    printf '   \033[31mFAIL\033[0m %-46s superseded link does not resolve (HTTP %s)\n' \
      "/$SCORECARDS/$_old" "$_code"; ERR=1
  fi
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
