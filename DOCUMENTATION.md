# AI Recruitment Assistant — Complete Technical Documentation

> **Stack:** Python 3.11 · Flask 3.x · LangGraph 1.2.5 · Groq (LLaMA) · SQLite · Tailwind CSS  
> **Server:** `python main.py` → http://localhost:5001  
> **Default login:** `admin` / `admin123`

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [File Structure](#3-file-structure)
4. [Multi-Agent Pipeline](#4-multi-agent-pipeline)
5. [Database Schema](#5-database-schema)
6. [API Reference](#6-api-reference)
7. [Authentication System](#7-authentication-system)
8. [Frontend Pages](#8-frontend-pages)
9. [Configuration & Environment Variables](#9-configuration--environment-variables)
10. [Feature Log — What Was Built](#10-feature-log--what-was-built)
11. [Known Issues & Future Improvements](#11-known-issues--future-improvements)
12. [Running the Project](#12-running-the-project)

---

## 1. Project Overview

An end-to-end multi-agent AI system that automates the early stages of the hiring pipeline:

1. **Upload** one or more candidate PDFs + a job description
2. **AI screens** each resume and scores it 0–100 against the JD
3. **AI drafts** a personalised invitation (≥70 score) or rejection email
4. **Human reviews** the draft, optionally refines it via AI, then approves sending
5. All results are **persisted** to a SQLite database across sessions
6. A **live dashboard** shows analytics, candidate history, and job pipelines

---

## 2. Architecture

```
Browser
  │
  ├─ GET /login          → login.html     (JWT auth)
  ├─ GET /               → index.html     (process candidates)
  ├─ GET /dashboard      → dashboard.html (analytics)
  ├─ GET /candidates     → candidates.html
  ├─ GET /jobs           → jobs.html
  │
  ├─ POST /process       → SSE stream (Server-Sent Events)
  │       │
  │       └─ LangGraph workflow ──────────────────────────────────┐
  │               │                                               │
  │           resume_screener                                     │
  │           (Groq LLaMA → JSON score/skills/summary)           │
  │               │                                               │
  │           invitation_drafter  OR  rejection_drafter           │
  │           (Groq LLaMA → subject + body)                      │
  │               │                                               │
  │           ── INTERRUPT ── (human-in-the-loop)                │
  │               │                                               │
  │           email_sender                                        │
  │           (SMTP via Gmail App Password)                       │
  │                                                               │
  ├─ POST /refine_email  → re-run drafter node with instructions  │
  ├─ POST /send_email    → resume graph past interrupt ───────────┘
  │
  ├─ POST /auth/login    → JSON Bearer token
  ├─ GET  /api/*         → REST API (jobs, candidates, stats)
  │
  └─ SQLite (recruitment.db)
         ├── jobs
         ├── candidates
         └── audit_log
```

### Key design decisions

| Decision | Rationale |
|---|---|
| **SSE instead of WebSocket** | POST required for file upload; SSE works over HTTP/1.1 with no extra infra |
| **LangGraph `interrupt_before=["email_sender"]`** | Human-in-the-loop: draft is shown before any email is sent |
| **SQLite not PostgreSQL** | Zero-config, single-file, perfect for demo; swap by changing `DB_PATH` |
| **JWT in HttpOnly cookie** | Browser navigation (GET /dashboard) sends cookie automatically — no JS changes needed. Also accepts `Authorization: Bearer` header for API clients |
| **werkzeug.security for hashing** | Already a Flask dependency; avoids bcrypt 4.x / passlib incompatibility |
| **Groq with LLaMA** | Free tier, fast inference, sufficient quality for recruitment screening |

---

## 3. File Structure

```
.
├── main.py                        # Flask app — all routes, SSE, auth
├── recruitment.db                 # SQLite database (auto-created)
├── requirements.txt
├── .env                           # Secrets (gitignored)
│
├── src/
│   ├── agents/
│   │   ├── resume_screening_agent.py       # Scores resume vs JD (0-100)
│   │   ├── candidate_communication_agent.py # Drafts invitation email
│   │   ├── rejection_email_agent.py        # Drafts rejection email
│   │   ├── email_sending_agent.py          # Sends via SMTP
│   │   └── job_posting_agent.py            # AI generates JD from notes
│   │
│   ├── auth/
│   │   ├── utils.py           # JWT create/decode, password hash, cookie helpers
│   │   └── dependencies.py    # require_auth decorator, get_current_username
│   │
│   ├── core/
│   │   └── workflow.py        # LangGraph StateGraph definition
│   │
│   ├── database/
│   │   └── db.py              # SQLite CRUD (jobs, candidates, audit_log, stats)
│   │
│   └── utils/
│       ├── pdf_parser.py      # PyMuPDF → plain text
│       ├── email_sender.py    # SMTP wrapper
│       ├── slack_notify.py    # Slack webhook (score ≥ 85)
│       └── helpers.py
│
├── templates/
│   ├── login.html             # Split-screen login (JWT)
│   ├── index.html             # Main processing page (~1400 lines)
│   ├── dashboard.html         # Analytics (Chart.js)
│   ├── candidates.html        # Candidate history + slide-over detail
│   └── jobs.html              # Job pipeline CRUD
│
└── uploads/                   # Temp PDF storage (cleared after processing)
```

---

## 4. Multi-Agent Pipeline

### LangGraph nodes (`src/core/workflow.py`)

```
resume_screener → [invitation_drafter | rejection_drafter] → ── INTERRUPT ── → email_sender
```

#### Node: `resume_screener`
- **Input:** `job_description` + `resume_content`
- **Model:** Groq LLaMA via `ChatGroq`
- **Output (JSON):**
  ```json
  {
    "candidateName": "Jane Doe",
    "candidateEmail": "jane@example.com",
    "matchScore": 82,
    "summary": "Strong Python background, 4yrs experience...",
    "skillsMatched": ["Python", "Flask", "AWS"],
    "skillsMissing": ["Kubernetes", "Go"]
  }
  ```
- **Routing:** score ≥ 70 → `invitation_drafter`, else → `rejection_drafter`

#### Node: `invitation_drafter` / `rejection_drafter`
- **Input:** screening results + optional `refinement_instructions`
- **Output:** `drafted_email = {subject, body}`
- Can be re-run via `POST /refine_email` without touching the graph checkpoint

#### Node: `email_sender`
- Paused before execution by `interrupt_before=["email_sender"]`
- Only runs when user clicks **Approve & Send**
- Uses Gmail SMTP with App Password

### SSE event protocol

The `POST /process` endpoint streams newline-delimited events:

```
data: {"type": "start",    "filename": "john.pdf", "thread_id": "uuid", "total": 3, "index": 0}
data: {"type": "node",     "filename": "john.pdf", "node": "resume_screener"}
data: {"type": "node",     "filename": "john.pdf", "node": "invitation_drafter"}
data: {"type": "result",   "data": { ...full result object... }}
data: {"type": "complete"}
```

Each `thread_id` maps to a LangGraph checkpoint stored in memory (`MemorySaver`). The frontend stores this to call `/send_email` or `/refine_email` later.

---

## 5. Database Schema

**File:** `src/database/db.py` · **Engine:** SQLite at `./recruitment.db`

### `jobs`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `title` | TEXT | Job title |
| `description` | TEXT | Full JD text |
| `status` | TEXT | `active` / `paused` / `closed` |
| `created_at` | TIMESTAMP | Auto |

### `candidates`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | Extracted by AI |
| `email` | TEXT | Extracted by AI |
| `filename` | TEXT | Original PDF name |
| `job_id` | INTEGER FK | → `jobs.id` |
| `match_score` | INTEGER | 0–100 |
| `decision` | TEXT | `shortlisted` / `rejected` |
| `summary` | TEXT | AI-generated narrative |
| `skills_matched` | TEXT | JSON array |
| `skills_missing` | TEXT | JSON array |
| `email_subject` | TEXT | Draft subject line |
| `email_body` | TEXT | Draft body |
| `email_status` | TEXT | `pending` / `sent` / `failed` |
| `email_sent_at` | TIMESTAMP | Set when sent |
| `thread_id` | TEXT UNIQUE | LangGraph checkpoint ID |
| `created_at` | TIMESTAMP | Auto |

### `audit_log`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `action` | TEXT | `screened` / `email_refined` / `email_sent` / `job_created` / `job_closed` |
| `candidate_id` | INTEGER FK | |
| `job_id` | INTEGER FK | |
| `details` | TEXT | Free text context |
| `created_at` | TIMESTAMP | Auto |

### Key DB functions
```python
init_db()                          # Create tables + default job on first run
save_candidate(data: dict) -> int  # Insert + return candidate_id
update_candidate_email_status(thread_id, status)
update_candidate_email_draft(candidate_id, subject, body)
get_stats(job_id=None) -> dict     # Aggregated KPIs + daily distribution
get_audit_log(limit=50) -> list
log_action(action, candidate_id, job_id, details)
```

---

## 6. API Reference

All routes require JWT (cookie or `Authorization: Bearer <token>` header).

### Auth

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/login` | — | Login page HTML |
| `POST` | `/login` | Form: `username`, `password` | 302 → `/` + sets HttpOnly cookie |
| `POST` | `/auth/login` | JSON: `{username, password}` | `{access_token, token_type, expires_in}` |
| `GET` | `/logout` | — | 302 → `/login` + clears cookie |

### Pages

| Method | Path | Returns |
|---|---|---|
| `GET` | `/` | Process candidates page |
| `GET` | `/dashboard` | Analytics dashboard |
| `GET` | `/candidates` | Candidate history (params: `job_id`, `decision`, `search`) |
| `GET` | `/jobs` | Job pipeline management |

### Processing

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/process` | Form: `job_description_text`, `resumes[]`, `job_id` | SSE stream |
| `POST` | `/generate_jd` | JSON: `{notes}` | `{job_description}` |
| `POST` | `/refine_email` | JSON: `{thread_id, instructions, candidate_id}` | `{status, new_state}` |
| `POST` | `/send_email` | JSON: `{thread_id, candidate_id}` | `{status, final_status}` |
| `POST` | `/export_report` | JSON: `{results: [...]}` | CSV file download |

### REST API

| Method | Path | Params / Body | Response |
|---|---|---|---|
| `GET` | `/api/jobs` | — | `[{id, title, description, status, candidate_count}]` |
| `POST` | `/api/jobs` | JSON: `{title, description}` | `{id, title}` 201 |
| `PUT` | `/api/jobs/<id>` | JSON: `{status}` or `{title}` | `{ok: true}` |
| `DELETE` | `/api/jobs/<id>` | — | `{ok: true}` |
| `GET` | `/api/candidates` | `?job_id&decision&search&limit&offset` | `[candidate...]` |
| `GET` | `/api/candidates/<id>` | — | Full candidate object |
| `GET` | `/api/stats` | `?job_id` | Stats object (see below) |

#### `/api/stats` response shape
```json
{
  "total": 24,
  "shortlisted": 9,
  "rejected": 15,
  "emailed": 7,
  "avg_score": 68,
  "active_jobs": 3,
  "shortlist_rate": 37,
  "score_distribution": { "low": 4, "mid": 11, "good": 6, "excellent": 3 },
  "daily_applications": [{ "day": "2026-06-15", "count": 5 }],
  "top_jobs": [{ "title": "Senior Backend Engineer", "count": 12, "avg_score": 71, "shortlisted": 5 }]
}
```

---

## 7. Authentication System

### Flow

```
Browser form POST /login
        ↓
  Validate username == ADMIN_USERNAME
          && password == ADMIN_PASSWORD
        ↓
  create_access_token(username)   ← HS256 JWT, 24h expiry
        ↓
  set_auth_cookie(response)       ← HttpOnly, SameSite=Lax, max_age=86400
        ↓
  302 redirect → /
        ↓
  Every subsequent request: @require_auth reads cookie
        → decode_access_token() → verify sub == ADMIN_USERNAME
        → 200 if valid, 302 /login if invalid (HTML routes)
        → 401 JSON if invalid (API/SSE routes)
```

### Files

#### `src/auth/utils.py`
```python
hash_password(plain)           # PBKDF2-SHA256 via werkzeug
verify_password(plain, hashed) # constant-time compare
create_access_token(subject)   # signs {sub, iat, exp, type} with HS256
decode_access_token(token)     # returns payload dict or None on any error
set_auth_cookie(response, username)  # HttpOnly cookie, 24h
clear_auth_cookie(response)          # deletes cookie on logout
```

All functions read `SECRET_KEY` from `os.getenv()` at **call-time** (not import-time) — immune to `load_dotenv()` ordering issues.

#### `src/auth/dependencies.py`
```python
require_auth          # decorator — gates every protected route
get_current_username  # decodes token → str username for templates
_extract_token()      # cookie first, then Authorization: Bearer header
_is_api_request()     # detects JSON vs HTML caller → 401 vs 302
```

### JWT token structure
```json
{
  "sub":  "admin",
  "iat":  1750000000,
  "exp":  1750086400,
  "type": "access"
}
```

### Security properties
- **HttpOnly** — JS cannot access the cookie (XSS safe)
- **SameSite=Lax** — not sent on cross-site POST (CSRF protection)
- **HS256** — tamper-evident signature; tampering returns `None`
- **24h expiry** — enforced by `jose.jwt.decode()`
- `secure=False` currently — set to `True` when running behind HTTPS in production

---

## 8. Frontend Pages

All pages share the same design language: `#f0f2f7` background, white cards, `#4f46e5` indigo accent, `Inter` + `Plus Jakarta Sans` fonts.

### `login.html` — `/login`
- Split-screen: gradient brand panel (left) + form (right)
- Password show/hide toggle
- Username preserved on failed login
- Shake animation on error
- No credentials displayed (removed)
- Responsive: brand panel hides on mobile

### `index.html` — `/` (~1400 lines)
Main processing interface. Left panel = form; right panel = streaming results.

**Left panel features:**
- Step 1: JD textarea + "✨ Generate with AI" button (calls `/generate_jd`)
- Job selector dropdown (pre-fills JD textarea from selected job)
- Step 2: Drag-and-drop PDF upload with file chip previews
- Process Candidates submit button

**Right panel — result cards per candidate:**
- Animated score arc (SVG, CSS transition)
- Animated score counter (JS)
- Score bar with "above/below 70%" threshold label
- Skill tags: matched (green) / missing (red)
- "Why this score?" collapsible breakdown
- AI summary section
- Email draft toggle (show/hide)
- Refinement textarea → Refine with AI button
- Approve & Send button (with confirmation checkbox)
- Interview slot picker (shortlisted candidates)
- Agent audit log (collapsible, shows which nodes ran)
- Badge states: Pending Review / Shortlisted / Rejected / Sent

**Live dashboard (during streaming):**
- Done / Shortlisted / Rejected counters update in real-time

**Summary bar (after processing):**
- Total / Shortlisted / Rejected / Avg Score / Funnel visualization
- Cards ↔ Ranking table view toggle
- Send All (batch approve pending emails)
- Export CSV
- Compare (side-by-side modal for shortlisted)

### `dashboard.html` — `/dashboard`
- 6 KPI cards: Total Screened, Shortlisted, Rejected, Emails Sent, Avg Score, Active Jobs
- Bar chart: Applications over last 30 days (Chart.js)
- Donut chart: Score distribution by band (Chart.js)
- Top roles table: volume + avg score + shortlisted count
- Recent activity log (last 15 audit entries)

### `candidates.html` — `/candidates`
- Filter bar: free-text search, job selector, decision filter
- Sortable table: avatar, name/email, role, score chip, decision badge, email status, date
- Slide-over detail panel: full candidate profile, skills, email draft, score breakdown
- "View" button opens panel without page navigation

### `jobs.html` — `/jobs`
- Create new job (inline expand panel with title + description textarea)
- Job cards with: status badge, candidate count, description preview, created date
- Per-job actions: View Candidates / Pause / Resume / Close / Delete
- JS handles all CRUD via `/api/jobs` without page reloads

---

## 9. Configuration & Environment Variables

**File:** `.env` (gitignored)

```bash
# ── Groq AI ───────────────────────────────────────────────────
GROQ_API_KEY=gsk_...           # Required — get free key at console.groq.com

# ── Email (SMTP) ──────────────────────────────────────────────
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=yourname@gmail.com
EMAIL_PASS=xxxx xxxx xxxx xxxx  # Gmail App Password (not account password)
                                 # Generate at myaccount.google.com/apppasswords

# ── Auth ──────────────────────────────────────────────────────
SECRET_KEY=recruitment-ai-secret-2026   # Change in production
ADMIN_USERNAME=admin                    # Login username
ADMIN_PASSWORD=admin123                 # Login password

# ── Optional integrations ─────────────────────────────────────
SLACK_WEBHOOK_URL=https://hooks.slack.com/...  # Notified when score ≥ 85
CALENDLY_LINK=https://calendly.com/yourname    # Embedded in invitation emails
```

### Startup banner
Every `python main.py` prints:
```
────────────────────────────────────────────────────
  AI Recruitment Assistant
────────────────────────────────────────────────────
  URL      : http://localhost:5001
  Username : admin
  Password : admin123
────────────────────────────────────────────────────
```

---

## 10. Feature Log — What Was Built

### Phase 1 — SSE Streaming Fix
**Problem:** Frontend was crashing with `"Unexpected token 'd'"` when parsing SSE lines.  
**Fix:**
- Changed `line.slice(6)` → `line.replace(/^data:\s*/, '')` (regex prefix strip)
- Added `if (!raw || raw === '[DONE]') continue` guard
- Changed `catch {}` → `catch(e) {}` (explicit binding)
- Wrapped `displayResults()` in its own try/catch
- Added `setEl()` null-guard helper for DOM updates

---

### Phase 2 — CEO Demo Feature Enhancements
All implemented in `templates/index.html`:

| Feature | What it does |
|---|---|
| **Live Dashboard** | Done / Shortlisted / Rejected counters animate during SSE streaming |
| **Ranking Table** | Sortable view (score desc) with 🥇🥈🥉 medals, toggled by view switch |
| **Batch Send** | "Send All (N)" button in summary bar dispatches all pending emails sequentially |
| **Interview Slots** | Next 3 weekday slots shown on shortlisted cards; clicking auto-fills refine textarea |
| **Audit Log** | Collapsible per-card log of which agent nodes ran (built from SSE node events) |
| **View Toggle** | Cards ↔ Ranking table switch with active state styling |
| **Compare Modal** | Side-by-side comparison of all shortlisted candidates |

**TDZ fix:** `const cardId` declaration moved above `window.__pendingCards.push({cardId})` — was causing "Cannot access 'cardId' before initialization".

---

### Phase 3 — Light / Professional UI Theme
Full CSS rewrite from dark glass morphism to clean light theme:

| Element | Before | After |
|---|---|---|
| Body background | `#07080f` near-black | `#f0f2f7` cool gray |
| Header / Left panel | Dark translucent glass | `#ffffff` white + 1px border |
| Cards | Dark glass `rgba(15,16,28,0.7)` | `#ffffff` + subtle shadow |
| Card headers | Dark gradient | Pastel `#eef2ff` (good) / `#fffbeb` (poor) |
| Badges | Light-on-dark | Dark-on-pastel (WCAG AA contrast) |
| Skill tags | Washed green/red on dark | `#ecfdf5`/`#fef2f2` with border |
| Notifications | Dark glass | White card + colored left border |
| Score arc track | `rgba(255,255,255,0.06)` | `#e2e8f0` |
| All `text-white` | — | `#0f172a` / `#1e293b` |

---

### Phase 4 — Database + Multi-page App

**New files:** `src/database/db.py`, `src/utils/slack_notify.py`

**New pages:**
- `/dashboard` — Chart.js analytics
- `/candidates` — searchable history with slide-over
- `/jobs` — full pipeline CRUD

**New routes:** 8 REST API endpoints + 4 new pages

**`main.py` changes:**
- DB initialized on startup via `init_db()`
- `save_candidate()` called after every SSE result event
- `log_action()` called on screen / send / refine / job create
- `notify_slack()` called when score ≥ 85 (if `SLACK_WEBHOOK_URL` set)
- Job selector in index.html form pre-fills JD and links candidate to pipeline
- `candidate_id` passed from SSE result → send/refine routes → DB updates

---

### Phase 5 — JWT Authentication

**New files:** `src/auth/utils.py`, `src/auth/dependencies.py`

**Replaced:** Flask `session`-based auth → JWT in HttpOnly cookie

**Login page redesign:** Split-screen, no credentials hint, password toggle, shake on error.

**How it works:**
1. `POST /login` (form) → `set_auth_cookie()` → 302 `/`
2. `POST /auth/login` (JSON) → returns Bearer token (+ also sets cookie)
3. Every `@require_auth` route → reads cookie (browser) or `Authorization:` header (API) → validates JWT
4. `GET /logout` → `clear_auth_cookie()` → 302 `/login`

**Fixes applied:**
- Moved all `os.getenv()` calls to call-time (not import-time) to eliminate load_dotenv ordering races
- Replaced `passlib[bcrypt]` (bcrypt 4.x incompatibility) with `werkzeug.security` PBKDF2
- Added credentials to `.env` so they're explicit and persistent across restarts

---

## 11. Known Issues & Future Improvements

### Known limitations
| Issue | Detail |
|---|---|
| **LangGraph in-memory** | `MemorySaver` means thread checkpoints are lost on server restart. In-progress emails cannot be resumed after restart. |
| **Single user** | Only one admin account. Adding multi-user requires a `users` table + per-user JWT `sub`. |
| `secure=False` cookie | Must be flipped to `True` when deployed behind HTTPS. |
| **No rate limiting** | `/auth/login` is unbounded. Add `flask-limiter` before public deployment. |
| **No resume storage** | PDFs are deleted after processing. Candidates table has filename but not the file. |

### Suggested next steps

1. **PostgreSQL migration** — change `DB_PATH` to use `psycopg2`; all SQL is standard.
2. **Multi-user auth** — add `users` table, hash passwords on creation, `sub` in JWT per user.
3. **Resume storage** — save PDFs to S3/local `storage/` folder, link via `candidates.resume_path`.
4. **LangGraph persistence** — swap `MemorySaver` for `SqliteSaver` so threads survive restarts.
5. **Google Calendar** — embed real Calendly/Cal.com link in invitation emails (`CALENDLY_LINK` env var ready).
6. **Rate limiting** — `pip install flask-limiter`, add `@limiter.limit("5/minute")` to `/auth/login`.
7. **HTTPS + production** — `gunicorn main:app`, nginx reverse proxy, `secure=True` on cookies.
8. **Email templates** — move email bodies to Jinja2 HTML templates instead of plain text.

---

## 12. Running the Project

### First time setup
```bash
# Clone and enter directory
cd Multi-Agent-System-for-Automated-Recruitment

# Create virtual environment
pyenv local 3.11.x
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env (copy and fill in your values)
cp .env.example .env
# Edit .env: add GROQ_API_KEY, EMAIL_USER, EMAIL_PASS at minimum

# Run
python main.py
```

### Every subsequent run
```bash
source venv/bin/activate
python main.py
# → http://localhost:5001
# → Login: admin / admin123 (or values from .env)
```

### Dependency tree (key packages)
```
flask                  Web framework + Jinja2 templates
python-dotenv          .env loading
langgraph              Multi-agent workflow with checkpointing
langchain-groq         Groq LLM integration
pymupdf                PDF → text extraction
python-jose[cryptography]  JWT signing / verification
werkzeug               Password hashing (PBKDF2, ships with Flask)
sqlite3                Database (Python stdlib, no install needed)
```

### Testing auth
```bash
source venv/bin/activate
python -c "
from dotenv import load_dotenv; load_dotenv()
import main
app = main.app
app.config['TESTING'] = True
with app.test_client() as c:
    r = c.post('/auth/login', json={'username':'admin','password':'admin123'})
    print(r.get_json())
"
```

---

*Documentation generated 2026-06-18. Covers all work done in the current session.*
