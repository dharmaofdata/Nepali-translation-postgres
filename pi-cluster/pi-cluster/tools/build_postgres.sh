#!/bin/sh
# Repackage the PostgreSQL server installed on this host into a relocatable
# artifact tree.  NOT reproducible and x86_64 only: a Pi artifact must be
# built from source by a different recipe.  See docs/GUIDE-GAPS.md.
set -e
out=$1
ver=${PGVERSION:-16}
lib=/usr/lib/postgresql/$ver
share=/usr/share/postgresql/$ver
[ -x "$lib/bin/postgres" ] || { echo "no $lib/bin/postgres" >&2; exit 1; }
tmp=$(mktemp -d)
mkdir -p "$tmp/pg/bin" "$tmp/pg/lib" "$tmp/pg/share"
cp -a "$lib/bin/." "$tmp/pg/bin/"
[ -d "$lib/lib" ] && cp -a "$lib/lib/." "$tmp/pg/lib/"
cp -a "$share/." "$tmp/pg/share/"
"$lib/bin/postgres" --version > "$tmp/pg/VERSION"
# Server-side extensions installed into the PostgreSQL tree travel with it.
ls "$tmp/pg/lib" > "$tmp/pg/LIBS"
tar -czf "$out" -C "$tmp" pg
rm -rf "$tmp"
