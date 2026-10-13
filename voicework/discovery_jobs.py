"""Resumable, user-started computer discovery. Persistent frontier, no background daemon."""
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from . import discovery
from .context import infer_context
from .provider import Provider

MAX_BATCH_ENTRIES = 10000
MAX_BATCH_SECONDS = 5


def _signature(info):
    return ':'.join(str(value) for value in (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size))


class DiscoveryJobs:
    def __init__(self, store):
        self.store = store
        with store._connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS discovery_jobs (
                id TEXT PRIMARY KEY, roots TEXT NOT NULL, status TEXT NOT NULL,
                scanned INTEGER DEFAULT 0, added INTEGER DEFAULT 0,
                duplicates INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0,
                warnings TEXT DEFAULT '[]', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS discovery_queue (
                job_id TEXT NOT NULL, path TEXT NOT NULL, root TEXT NOT NULL,
                cursor INTEGER NOT NULL DEFAULT 0, signature TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(job_id,path)
            );
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(discovery_queue)')}
            if 'cursor' not in columns:
                db.execute('ALTER TABLE discovery_queue ADD COLUMN cursor INTEGER NOT NULL DEFAULT 0')
            if 'signature' not in columns:
                db.execute("ALTER TABLE discovery_queue ADD COLUMN signature TEXT NOT NULL DEFAULT ''")

    def list(self):
        with self.store._connect() as db:
            return [self._public(db, r) for r in db.execute('SELECT * FROM discovery_jobs ORDER BY created_at DESC')]

    def _public(self, db, row):
        if row is None:
            raise KeyError('Discovery job not found')
        result = dict(row)
        result['roots'] = json.loads(result['roots'])
        result['warnings'] = json.loads(result['warnings'])
        result['remaining'] = db.execute('SELECT count(*) FROM discovery_queue WHERE job_id=?', (result['id'],)).fetchone()[0]
        result['coverage'] = 'Supported readable files in selected roots, excluding hidden/sensitive/generated trees and links. Unreadable or unsupported material is not covered.'
        return result

    def create(self, roots):
        if not isinstance(roots, list) or not 1 <= len(roots) <= 8:
            raise ValueError('Choose 1 to 8 absolute folders')
        cleaned = []
        for value in roots:
            if not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value:
                raise ValueError('Invalid discovery folder')
            path = Path(value)
            if not path.is_absolute() or '..' in path.parts or path.resolve() != path or path.is_symlink() or discovery._forbidden(path):
                raise ValueError('Choose an absolute non-sensitive folder, without symlinks or parent traversal')
            if not path.is_dir() or discovery._inside(path, self.store.path.parent.resolve()):
                raise ValueError('Folder is unavailable or is the private app data folder')
            if not any(discovery._inside(path, Path(r)) for r in cleaned):
                cleaned = [r for r in cleaned if not discovery._inside(Path(r), path)] + [str(path)]
        ident = uuid.uuid4().hex
        with self.store._connect() as db:
            db.execute('INSERT INTO discovery_jobs (id,roots,status,created_at) VALUES (?,?,?,?)', (ident, json.dumps(cleaned), 'paused', datetime.now(timezone.utc).isoformat()))
            db.executemany('INSERT INTO discovery_queue (job_id,path,root,signature) VALUES (?,?,?,?)',
                           [(ident, p, p, _signature(os.stat(p, follow_symlinks=False))) for p in cleaned])
            return self._public(db, db.execute('SELECT * FROM discovery_jobs WHERE id=?', (ident,)).fetchone())

    def step(self, ident, max_files=100):
        if isinstance(max_files, bool) or not isinstance(max_files, int) or not 1 <= max_files <= 200:
            raise ValueError('Batch size must be 1 to 200 files')
        start, processed, entries, total_bytes = time.monotonic(), 0, 0, 0
        with self.store._connect() as db:
            job = db.execute('SELECT * FROM discovery_jobs WHERE id=?', (ident,)).fetchone()
            if job is None:
                raise KeyError('Discovery job not found')
            if job['status'] in ('completed', 'cancelled'):
                return self._public(db, job)
        while processed < max_files and entries < MAX_BATCH_ENTRIES and total_bytes < discovery.MAX_TOTAL_BYTES and time.monotonic() - start < MAX_BATCH_SECONDS:
            with self.store._connect() as db:
                row = db.execute('SELECT * FROM discovery_queue WHERE job_id=? ORDER BY path LIMIT 1', (ident,)).fetchone()
            if row is None:
                break
            path, root = Path(row['path']), Path(row['root'])
            warnings, scanned, added, duplicates, skipped, children = [], 0, 0, 0, 0, []
            retain_directory, cursor, signature = False, row['cursor'], row['signature']
            try:
                if path.is_symlink() or path.resolve() != path or not discovery._inside(path, root) or discovery._forbidden(path) or discovery._inside(path, self.store.path.parent.resolve()):
                    skipped = 1
                    warnings.append(str(path) + ': source path changed, became excluded, or is a link; skipped')
                else:
                    current_signature = _signature(os.stat(path, follow_symlinks=False))
                    if signature and signature != current_signature:
                        raise ValueError('Queued source changed')
                    signature = current_signature
                    if path.is_dir():
                        # The cursor and metadata checkpoint bound newly queued
                        # entries. A changed directory is reported, never resumed
                        # against a different listing. Reopening skips the already
                        # visited prefix; no document text is reread.
                        with os.scandir(path) as listing:
                            for index, entry in enumerate(listing):
                                if index < row['cursor']:
                                    continue
                                entries += 1
                                cursor = index + 1
                                try:
                                    candidate = Path(entry.path)
                                    if entry.is_symlink() or discovery._forbidden(candidate) or discovery._inside(candidate, self.store.path.parent.resolve()):
                                        skipped += 1
                                    elif entry.is_dir(follow_symlinks=False) or (entry.is_file(follow_symlinks=False) and candidate.suffix.lower() in discovery.SUPPORTED):
                                        children.append((ident, str(candidate), str(root), _signature(entry.stat(follow_symlinks=False))))
                                    else:
                                        skipped += 1
                                except OSError:
                                    skipped += 1
                                    warnings.append(str(path) + ': directory entry became unreadable or changed; skipped')
                                if entries >= MAX_BATCH_ENTRIES or time.monotonic() - start >= MAX_BATCH_SECONDS:
                                    retain_directory = True
                                    break
                        if _signature(os.stat(path, follow_symlinks=False)) != signature:
                            children = []
                            raise ValueError('Directory changed during listing')
                    else:
                        processed += 1
                        scanned = 1
                        raw = discovery._read_regular(path, root)
                        total_bytes += len(raw)
                        if _signature(os.stat(path, follow_symlinks=False)) != signature:
                            raise ValueError('File changed during read')
                        items = discovery.extract(path, raw)
                        accepted = [s for s in items if not discovery.SECRET_TEXT.search(s['text'])]
                        skipped += len(items) - len(accepted)
                        if skipped:
                            warnings.append(str(path) + ': credential-like content skipped')
                        if accepted:
                            for candidate in accepted:
                                if not candidate.get('context'):
                                    candidate['context'] = infer_context(candidate, Provider('offline'))['context']
                            result = self.store.add_samples(accepted)
                            added, duplicates = result['added'], result['duplicates']
                        if path.suffix.lower() == '.pdf':
                            warnings.append(str(path) + ': PDF extraction covers first 30 pages only')
                        if len(items) >= discovery.MAX_ITEMS or any(len(x['text']) >= discovery.MAX_SAMPLE_CHARS for x in items):
                            warnings.append(str(path) + ': extraction cap reached; review source for omitted content')
            except Exception:
                # Specific source bytes and decoder errors never enter logs/UI.
                retain_directory = False
                skipped += 1
                warnings.append(str(path) + ': unreadable, changed, unsupported, or extraction failed')
            with self.store._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT warnings FROM discovery_jobs WHERE id=?', (ident,)).fetchone()
                combined = (json.loads(previous[0]) + warnings)[-100:]
                db.executemany('INSERT OR IGNORE INTO discovery_queue (job_id,path,root,signature) VALUES (?,?,?,?)', children)
                if retain_directory:
                    db.execute('UPDATE discovery_queue SET cursor=?,signature=? WHERE job_id=? AND path=?', (cursor, signature, ident, str(path)))
                else:
                    db.execute('DELETE FROM discovery_queue WHERE job_id=? AND path=?', (ident, str(path)))
                db.execute('UPDATE discovery_jobs SET scanned=scanned+?,added=added+?,duplicates=duplicates+?,skipped=skipped+?,warnings=? WHERE id=?', (scanned, added, duplicates, skipped, json.dumps(combined), ident))
        with self.store._connect() as db:
            remaining = db.execute('SELECT count(*) FROM discovery_queue WHERE job_id=?', (ident,)).fetchone()[0]
            db.execute('UPDATE discovery_jobs SET status=? WHERE id=?', ('paused' if remaining else 'completed', ident))
            return self._public(db, db.execute('SELECT * FROM discovery_jobs WHERE id=?', (ident,)).fetchone())

    def cancel(self, ident):
        with self.store._connect() as db:
            if not db.execute('SELECT id FROM discovery_jobs WHERE id=?', (ident,)).fetchone():
                raise KeyError('Discovery job not found')
            db.execute('UPDATE discovery_jobs SET status=? WHERE id=?', ('cancelled', ident))
            db.execute('DELETE FROM discovery_queue WHERE job_id=?', (ident,))
            return self._public(db, db.execute('SELECT * FROM discovery_jobs WHERE id=?', (ident,)).fetchone())
