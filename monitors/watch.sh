#!/bin/sh
case "$DECKHAND_ROLE" in captain | hand) ;; *) exec tail -f /dev/null ;; esac
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
while :; do
  "$root/bin/deckhand" watch apply "$DECKHAND_ROLE"
  sleep 5
done
