"""Bounded, per-process document jobs; no writing is added to training storage."""
import copy
import threading
import time
import uuid


class DocumentJobs:
    def __init__(self, generation_slots, max_jobs=16, lifetime=3600, failure_messages=None):
        self.failure_messages = failure_messages
        self.slots = generation_slots
        self.max_jobs = max_jobs
        self.lifetime = lifetime
        self.lock = threading.Lock()
        self.jobs = {}

    def _prune(self):
        now = time.monotonic()
        expired = [key for key, item in self.jobs.items()
                   if item['status'] in ('complete', 'failed') and now - item['_finished'] > self.lifetime]
        for key in expired:
            del self.jobs[key]
        while len(self.jobs) >= self.max_jobs:
            finished = [(item['_finished'], key) for key, item in self.jobs.items()
                        if item['status'] in ('complete', 'failed')]
            if not finished:
                break
            del self.jobs[min(finished)[1]]

    @staticmethod
    def _public(item):
        return copy.deepcopy({key: value for key, value in item.items() if not key.startswith('_')})

    def start(self, operation, kind='rewrite'):
        if not self.slots.acquire(blocking=False):
            raise RuntimeError('Generation is busy; try again shortly')
        ident = str(uuid.uuid4())
        try:
            with self.lock:
                self._prune()
                if len(self.jobs) >= self.max_jobs:
                    raise RuntimeError('Document queue is full; try again shortly')
                self.jobs[ident] = {'id': ident, 'kind': kind, 'status': 'pending'}
            worker = threading.Thread(target=self._run, args=(ident, operation), daemon=True)
            worker.start()
        except Exception:
            with self.lock:
                self.jobs.pop(ident, None)
            self.slots.release()
            raise
        return self.get(ident)

    def _run(self, ident, operation):
        with self.lock:
            self.jobs[ident]['status'] = 'running'
        try:
            result = operation()
            update = {'status': 'complete', 'result': result}
        except ValueError:
            # Provider validation errors may originate in untrusted output.
            update = {'status': 'failed', 'error': self.failure_messages[0] if self.failure_messages else 'The document could not be completed or checked. Your source is unchanged. Check provider availability and retry; no partial draft was accepted.'}
        except Exception:
            update = {'status': 'failed', 'error': self.failure_messages[1] if self.failure_messages else 'The document job failed. Your source and saved training records are unchanged.'}
        finally:
            self.slots.release()
        with self.lock:
            self.jobs[ident].update(update, _finished=time.monotonic())

    def get(self, ident):
        with self.lock:
            item = self.jobs.get(ident)
            if item is None or (item['status'] in ('complete', 'failed') and time.monotonic() - item['_finished'] > self.lifetime):
                self.jobs.pop(ident, None)
                raise KeyError(ident)
            return self._public(item)
