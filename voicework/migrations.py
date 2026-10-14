"""Small, data-preserving SQLite schema migrations."""
import re

OLD_CHECK = "CHECK(provider IN ('offline','codex'))"
NEW_CHECK = "CHECK(provider IN ('offline','codex','anthropic'))"


def allow_live_provider(conn, table):
    """Widen a provider CHECK constraint so Anthropic-generated records can be stored.

    SQLite cannot alter a CHECK constraint, so the table is rebuilt in one
    BEGIN IMMEDIATE write transaction (committed by the caller's ``with conn``)
    with identical columns and rows. Existing records keep
    their original provider labels. Runs once; later calls are no-ops.
    """
    if not re.fullmatch(r"[a-z_]+", table):
        raise ValueError("Invalid table name")
    # Take the write lock before reading the schema so concurrent openers (web
    # app, MCP server, panel CLI) cannot interleave check-and-rebuild steps.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row is None or OLD_CHECK not in row[0]:
        return False
    extras = conn.execute("SELECT name FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,)).fetchall()
    if extras:
        raise RuntimeError("Unexpected index or trigger on %s; refusing to migrate automatically" % table)
    before = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    temporary = table + "_migrating"
    conn.execute("DROP TABLE IF EXISTS %s" % temporary)
    create, count = re.subn(r'^CREATE TABLE\s+(?:"%s"|%s\b)' % (table, table), "CREATE TABLE %s" % temporary, row[0].replace(OLD_CHECK, NEW_CHECK), count=1)
    if count != 1:
        raise RuntimeError("Unexpected schema for %s; refusing to migrate automatically" % table)
    conn.execute(create)
    conn.execute("INSERT INTO %s SELECT * FROM %s" % (temporary, table))
    after = conn.execute("SELECT COUNT(*) FROM %s" % temporary).fetchone()[0]
    if after != before:
        raise RuntimeError("Migration row count mismatch for %s" % table)
    conn.execute("DROP TABLE %s" % table)
    conn.execute("ALTER TABLE %s RENAME TO %s" % (temporary, table))
    return True
