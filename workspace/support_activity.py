"""Local request accounting for support agents; no prompts or responses are stored."""
from contextlib import contextmanager
import time
import uuid

AGENT_IDS = ('studio-designer', 'improvement-advisor', 'merge-resolver')


class SupportActivity:
    def __init__(self, db):
        self.db = db
        db.execute('''CREATE TABLE IF NOT EXISTS support_activity (
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, status TEXT NOT NULL,
            started_at REAL NOT NULL, finished_at REAL)''')
        db.execute("INSERT OR IGNORE INTO state VALUES ('support_activity_since', ?)", (str(time.time()),))
        # A restarted host cannot attest to the outcome of its in-flight calls.
        db.execute("UPDATE support_activity SET status='interrupted' WHERE status='running'")
        db.commit()

    @contextmanager
    def track(self, agent_id, *, ident=None, success='completed'):
        if agent_id not in AGENT_IDS or success not in ('completed', 'submitted'):
            raise ValueError('Unknown support agent or outcome')
        ident = ident or uuid.uuid4().hex
        with self.db:
            self.db.execute('''INSERT INTO support_activity VALUES (?, ?, 'running', ?, NULL)
                ON CONFLICT(id) DO UPDATE SET status='running', finished_at=NULL
                WHERE support_activity.status IN ('failed', 'interrupted')''', (ident, agent_id, time.time()))
        try:
            yield
        except BaseException as exc:
            self.finish(ident, 'failed' if isinstance(exc, Exception) else 'interrupted')
            raise
        else:
            self.finish(ident, success)

    def finish(self, ident, status):
        with self.db:
            # A failed retry cannot erase an earlier acknowledged submission.
            self.db.execute('''UPDATE support_activity SET status=?, finished_at=? WHERE id=?
                AND status NOT IN ('submitted', 'completed')''', (status, time.time(), ident))

    def summary(self, agent_id):
        counts = dict(self.db.execute('SELECT status, COUNT(*) FROM support_activity WHERE agent_id=? GROUP BY status', (agent_id,)))
        since = float(self.db.execute("SELECT value FROM state WHERE key='support_activity_since'").fetchone()[0])
        return {'since': since, 'requests': sum(counts.values()),
                **{status: counts.get(status, 0) for status in ('running', 'submitted', 'completed', 'failed', 'interrupted')}}
