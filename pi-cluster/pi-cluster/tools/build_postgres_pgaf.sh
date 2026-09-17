#!/bin/sh
# Repackage PostgreSQL together with the pg_auto_failover CLI: one
# distribution, because pg_autoctl supervises a PostgreSQL it must find next
# to it.  The server-side extension (pgautofailover.so and its SQL) already
# lives inside the PostgreSQL tree.  NOT reproducible and x86_64 only.
set -e
out=$1
ver=${PGVERSION:-16}
lib=/usr/lib/postgresql/$ver
share=/usr/share/postgresql/$ver
[ -x "$lib/bin/postgres" ] || { echo "no $lib/bin/postgres" >&2; exit 1; }
[ -x /usr/bin/pg_autoctl ] || { echo "no /usr/bin/pg_autoctl" >&2; exit 1; }
[ -f "$lib/lib/pgautofailover.so" ] || { echo "no pgautofailover.so" >&2; exit 1; }
tmp=$(mktemp -d)
mkdir -p "$tmp/tree/pg/bin" "$tmp/tree/pg/lib" "$tmp/tree/pg/share" "$tmp/tree/pgaf/bin"
cp -a "$lib/bin/." "$tmp/tree/pg/bin/"
cp -a "$lib/lib/." "$tmp/tree/pg/lib/"
cp -a "$share/." "$tmp/tree/pg/share/"
cp -a /usr/bin/pg_autoctl "$tmp/tree/pgaf/bin/"
{ "$lib/bin/postgres" --version; /usr/bin/pg_autoctl --version | head -1; } \
    > "$tmp/tree/VERSION"
tar -czf "$out" -C "$tmp/tree" pg pgaf
rm -rf "$tmp"
