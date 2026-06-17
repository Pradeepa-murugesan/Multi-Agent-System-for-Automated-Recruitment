import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'recruitment.db'
)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT,
        filename TEXT,
        job_id INTEGER,
        match_score INTEGER DEFAULT 0,
        decision TEXT,
        summary TEXT,
        skills_matched TEXT DEFAULT '[]',
        skills_missing TEXT DEFAULT '[]',
        email_subject TEXT,
        email_body TEXT,
        email_status TEXT DEFAULT 'pending',
        email_sent_at TIMESTAMP,
        thread_id TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        candidate_id INTEGER,
        job_id INTEGER,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute("SELECT COUNT(*) FROM jobs")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO jobs (title, description, status) VALUES (?, ?, ?)",
            ('General Applications', 'Default pool for general applications.', 'active')
        )

    conn.commit()
    conn.close()

# ─── Jobs ────────────────────────────────────────────────────────────────────

def get_all_jobs(status=None):
    conn = get_db()
    if status:
        rows = conn.execute(
            'SELECT j.*, COUNT(c.id) as candidate_count FROM jobs j '
            'LEFT JOIN candidates c ON c.job_id=j.id WHERE j.status=? GROUP BY j.id ORDER BY j.created_at DESC',
            (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT j.*, COUNT(c.id) as candidate_count FROM jobs j '
            'LEFT JOIN candidates c ON c.job_id=j.id GROUP BY j.id ORDER BY j.created_at DESC'
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_job(job_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def create_job(title, description):
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO jobs (title, description) VALUES (?, ?)', (title, description))
    job_id = c.lastrowid
    conn.commit()
    conn.close()
    return job_id

def update_job(job_id, **kwargs):
    conn = get_db()
    for key, value in kwargs.items():
        if key in ('title', 'description', 'status'):
            conn.execute(f'UPDATE jobs SET {key}=? WHERE id=?', (value, job_id))
    conn.commit()
    conn.close()

def delete_job(job_id):
    conn = get_db()
    conn.execute('DELETE FROM jobs WHERE id=?', (job_id,))
    conn.commit()
    conn.close()

# ─── Candidates ───────────────────────────────────────────────────────────────

def save_candidate(data):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO candidates
        (name, email, filename, job_id, match_score, decision, summary,
         skills_matched, skills_missing, email_subject, email_body, thread_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            data.get('name'),
            data.get('email'),
            data.get('filename'),
            data.get('job_id'),
            data.get('match_score', 0),
            data.get('decision'),
            data.get('summary'),
            json.dumps(data.get('skills_matched', [])),
            json.dumps(data.get('skills_missing', [])),
            data.get('email_subject'),
            data.get('email_body'),
            data.get('thread_id')
        )
    )
    candidate_id = c.lastrowid
    conn.commit()
    conn.close()
    return candidate_id

def update_candidate_email_status(thread_id, status):
    conn = get_db()
    sent_at = datetime.now().isoformat() if status == 'sent' else None
    conn.execute(
        'UPDATE candidates SET email_status=?, email_sent_at=? WHERE thread_id=?',
        (status, sent_at, thread_id)
    )
    conn.commit()
    conn.close()

def update_candidate_email_draft(candidate_id, subject, body):
    conn = get_db()
    conn.execute(
        'UPDATE candidates SET email_subject=?, email_body=? WHERE id=?',
        (subject, body, candidate_id)
    )
    conn.commit()
    conn.close()

def get_candidates(job_id=None, decision=None, search=None, limit=200, offset=0):
    conn = get_db()
    query = ('SELECT c.*, j.title as job_title FROM candidates c '
             'LEFT JOIN jobs j ON c.job_id=j.id WHERE 1=1')
    params = []
    if job_id:
        query += ' AND c.job_id=?'
        params.append(job_id)
    if decision:
        query += ' AND c.decision=?'
        params.append(decision)
    if search:
        query += ' AND (c.name LIKE ? OR c.email LIKE ?)'
        params += [f'%{search}%', f'%{search}%']
    query += ' ORDER BY c.created_at DESC LIMIT ? OFFSET ?'
    params += [limit, offset]
    rows = conn.execute(query, params).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d['skills_matched'] = json.loads(d.get('skills_matched') or '[]')
        d['skills_missing'] = json.loads(d.get('skills_missing') or '[]')
        result.append(d)
    return result

def get_candidate(candidate_id):
    conn = get_db()
    row = conn.execute(
        'SELECT c.*, j.title as job_title FROM candidates c '
        'LEFT JOIN jobs j ON c.job_id=j.id WHERE c.id=?', (candidate_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d['skills_matched'] = json.loads(d.get('skills_matched') or '[]')
    d['skills_missing'] = json.loads(d.get('skills_missing') or '[]')
    return d

def get_stats(job_id=None):
    conn = get_db()
    where = 'WHERE 1=1'
    params = []
    if job_id:
        where += ' AND job_id=?'
        params.append(job_id)

    total      = conn.execute(f'SELECT COUNT(*) FROM candidates {where}', params).fetchone()[0]
    shortlisted = conn.execute(f"SELECT COUNT(*) FROM candidates {where} AND decision='shortlisted'", params).fetchone()[0]
    rejected   = conn.execute(f"SELECT COUNT(*) FROM candidates {where} AND decision='rejected'", params).fetchone()[0]
    emailed    = conn.execute(f"SELECT COUNT(*) FROM candidates {where} AND email_status='sent'", params).fetchone()[0]
    avg_raw    = conn.execute(f'SELECT AVG(match_score) FROM candidates {where}', params).fetchone()[0]
    avg_score  = round(avg_raw or 0)

    dist = conn.execute(f'''SELECT
        SUM(CASE WHEN match_score < 40 THEN 1 ELSE 0 END),
        SUM(CASE WHEN match_score >= 40 AND match_score < 70 THEN 1 ELSE 0 END),
        SUM(CASE WHEN match_score >= 70 AND match_score < 85 THEN 1 ELSE 0 END),
        SUM(CASE WHEN match_score >= 85 THEN 1 ELSE 0 END)
        FROM candidates {where}''', params).fetchone()

    daily = conn.execute(f'''SELECT DATE(created_at) as day, COUNT(*) as cnt
        FROM candidates {where} AND created_at >= date('now','-30 days')
        GROUP BY DATE(created_at) ORDER BY day''', params).fetchall()

    top_jobs = conn.execute('''SELECT j.title, COUNT(c.id) as cnt,
        ROUND(AVG(c.match_score)) as avg_sc,
        SUM(CASE WHEN c.decision='shortlisted' THEN 1 ELSE 0 END) as sl
        FROM candidates c JOIN jobs j ON c.job_id=j.id
        GROUP BY c.job_id ORDER BY cnt DESC LIMIT 5''').fetchall()

    active_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='active'").fetchone()[0]

    conn.close()
    return {
        'total': total,
        'shortlisted': shortlisted,
        'rejected': rejected,
        'emailed': emailed,
        'avg_score': avg_score,
        'active_jobs': active_jobs,
        'shortlist_rate': round((shortlisted / total * 100) if total > 0 else 0),
        'score_distribution': {
            'low': dist[0] or 0,
            'mid': dist[1] or 0,
            'good': dist[2] or 0,
            'excellent': dist[3] or 0
        },
        'daily_applications': [{'day': r[0], 'count': r[1]} for r in daily],
        'top_jobs': [{'title': r[0], 'count': r[1], 'avg_score': r[2] or 0, 'shortlisted': r[3]} for r in top_jobs]
    }

# ─── Audit Log ────────────────────────────────────────────────────────────────

ACTION_LABELS = {
    'screened': 'Resume Screened',
    'email_refined': 'Email Refined by AI',
    'email_sent': 'Email Sent',
    'job_created': 'Job Created',
    'job_closed': 'Job Closed',
}

def log_action(action, candidate_id=None, job_id=None, details=None):
    conn = get_db()
    conn.execute(
        'INSERT INTO audit_log (action, candidate_id, job_id, details) VALUES (?, ?, ?, ?)',
        (action, candidate_id, job_id, details)
    )
    conn.commit()
    conn.close()

def get_audit_log(limit=50, job_id=None):
    conn = get_db()
    query = ('''SELECT a.*, c.name as candidate_name, c.email as candidate_email,
                j.title as job_title FROM audit_log a
                LEFT JOIN candidates c ON a.candidate_id=c.id
                LEFT JOIN jobs j ON a.job_id=j.id WHERE 1=1''')
    params = []
    if job_id:
        query += ' AND (a.job_id=? OR c.job_id=?)'
        params += [job_id, job_id]
    query += ' ORDER BY a.created_at DESC LIMIT ?'
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['action_label'] = ACTION_LABELS.get(d['action'], d['action'])
        result.append(d)
    return result
