/**
 * Shared DB access — the SAME database file the Python backend (../app,
 * ../modules) reads and writes, via better-sqlite3 instead of SQLAlchemy.
 * schema.sql (repo root) is the source of truth for every table here;
 * this file does not create or migrate anything, it just connects.
 *
 * Path resolution: defaults to ../data/leadgen.db (sibling to this
 * dashboard/ directory), matching the Python side's default
 * DATABASE_URL=sqlite:///./data/leadgen.db. Override with
 * DASHBOARD_SQLITE_PATH in production, since schema.sql is written
 * SQLite-flavored — if the backend ever moves to Postgres, this file
 * (and every query in app/api/**) is what needs to change, nothing else.
 */
import Database from "better-sqlite3";
import path from "node:path";

let db: Database.Database | null = null;
let readonlyDb: Database.Database | null = null;

function resolveDbPath(): string {
  return (
    process.env.DASHBOARD_SQLITE_PATH ||
    path.resolve(process.cwd(), "..", "data", "leadgen.db")
  );
}

/** Read-write connection. Use for the mutations this app owns directly
 * (e.g. deadlines.completed) — anything with an external side effect
 * should go through lib/backend.ts instead, see its file comment. */
export function getDb(): Database.Database {
  if (db) return db;
  db = new Database(resolveDbPath(), { fileMustExist: true });
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  return db;
}

/** Read-only connection, `query_only` pragma set — for the Ask Jarvis
 * query_database tool. This is a second guard, not the only one: the tool
 * handler must ALSO reject anything but a single SELECT before this
 * connection ever sees the string. Never open this without fileMustExist —
 * better-sqlite3 will otherwise silently create a new empty database file
 * if the path is wrong. */
export function getReadonlyDb(): Database.Database {
  if (readonlyDb) return readonlyDb;
  readonlyDb = new Database(resolveDbPath(), { readonly: true, fileMustExist: true });
  readonlyDb.pragma("query_only = ON");
  return readonlyDb;
}
