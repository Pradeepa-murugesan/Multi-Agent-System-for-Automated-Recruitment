import os
import sys
import json
import uuid
import io
import csv
import queue
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, make_response
from dotenv import load_dotenv
from src.extensions import limiter

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv()  # must run before auth imports so os.getenv picks up .env values

# ── LangSmith tracing (optional — only activates when API key is present) ─────
if os.getenv('LANGCHAIN_API_KEY'):
    os.environ.setdefault('LANGCHAIN_TRACING_V2', 'true')
    os.environ.setdefault('LANGCHAIN_PROJECT',     os.getenv('LANGCHAIN_PROJECT', 'ai-recruitment'))

from src.utils.pdf_parser import parse_pdf_from_path
from src.core.workflow import app as recruitment_app
from src.agents.job_posting_agent import generate_jd_from_notes
from src.agents.candidate_ranking_agent import rank_candidates
from src.agents.followup_agent import generate_followup_email
from src.agents.jd_optimizer_agent import optimize_jd
from src.agents.talent_intelligence_agent import analyze_talent_pool
from src.utils.pdf_generator import generate_candidate_pdf, generate_batch_pdf
from src.database.db import (
    init_db, get_all_jobs, get_job, create_job, update_job, delete_job,
    save_candidate, update_candidate_email_status, update_candidate_email_draft,
    get_candidates, get_candidate, get_stats, log_action, get_audit_log,
    get_followup_pending, mark_followup_sent,
)
from src.utils.slack_notify import notify_slack
from src.auth.dependencies import require_auth, get_current_username
from src.auth.blueprint import auth_bp

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'recruitment-ai-secret-2026')

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

ADMIN_USER = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASS = os.getenv('ADMIN_PASSWORD', 'admin123')
MAX_CONCURRENT_RESUMES = int(os.getenv('MAX_CONCURRENT_RESUMES', '3'))

# Register auth Blueprint and rate limiter
app.register_blueprint(auth_bp)
limiter.init_app(app)
init_db()  # idempotent — creates tables + seeds admin on first run

# ─── Pages ────────────────────────────────────────────────────────────────────

@app.route('/')
@require_auth
def index():
    jobs = get_all_jobs(status='active')
    return render_template('index.html', jobs=jobs, username=get_current_username())

@app.route('/dashboard')
@require_auth
def dashboard():
    stats = get_stats()
    jobs  = get_all_jobs()
    audit = get_audit_log(limit=15)
    return render_template('dashboard.html', stats=stats, jobs=jobs, audit=audit,
                           username=get_current_username())

@app.route('/candidates')
@require_auth
def candidates_page():
    job_id   = request.args.get('job_id', type=int)
    decision = request.args.get('decision', '')
    search   = request.args.get('search', '').strip()
    candidates = get_candidates(job_id=job_id, decision=decision or None, search=search or None)
    jobs = get_all_jobs()
    return render_template('candidates.html', candidates=candidates, jobs=jobs,
                           filter_job=job_id, filter_decision=decision,
                           search=search, username=get_current_username())

@app.route('/jobs')
@require_auth
def jobs_page():
    jobs = get_all_jobs()
    return render_template('jobs.html', jobs=jobs, username=get_current_username())

# ─── API: Jobs ────────────────────────────────────────────────────────────────

@app.route('/api/jobs', methods=['GET'])
@require_auth
def api_get_jobs():
    return jsonify(get_all_jobs())

@app.route('/api/jobs', methods=['POST'])
@require_auth
def api_create_job():
    data  = request.get_json() or {}
    title = data.get('title', '').strip()
    desc  = data.get('description', '').strip()
    if not title or not desc:
        return jsonify({'error': 'title and description are required'}), 400
    job_id = create_job(title, desc)
    log_action('job_created', job_id=job_id, details=f'"{title}"')
    return jsonify({'id': job_id, 'title': title}), 201

@app.route('/api/jobs/<int:job_id>', methods=['PUT'])
@require_auth
def api_update_job(job_id):
    data = request.get_json() or {}
    allowed = {k: v for k, v in data.items() if k in ('title', 'description', 'status')}
    if not allowed:
        return jsonify({'error': 'Nothing to update'}), 400
    update_job(job_id, **allowed)
    if 'status' in allowed and allowed['status'] == 'closed':
        log_action('job_closed', job_id=job_id)
    return jsonify({'ok': True})

@app.route('/api/jobs/<int:job_id>', methods=['DELETE'])
@require_auth
def api_delete_job(job_id):
    delete_job(job_id)
    return jsonify({'ok': True})

# ─── API: Candidates ──────────────────────────────────────────────────────────

@app.route('/api/candidates', methods=['GET'])
@require_auth
def api_get_candidates():
    job_id   = request.args.get('job_id', type=int)
    decision = request.args.get('decision')
    search   = request.args.get('search')
    limit    = request.args.get('limit', 200, type=int)
    offset   = request.args.get('offset', 0, type=int)
    return jsonify(get_candidates(job_id=job_id, decision=decision, search=search,
                                  limit=limit, offset=offset))

@app.route('/api/candidates/<int:candidate_id>', methods=['GET'])
@require_auth
def api_get_candidate(candidate_id):
    c = get_candidate(candidate_id)
    return (jsonify(c) if c else (jsonify({'error': 'Not found'}), 404))

# ─── API: Stats ───────────────────────────────────────────────────────────────

@app.route('/api/stats', methods=['GET'])
@require_auth
def api_stats():
    job_id = request.args.get('job_id', type=int)
    return jsonify(get_stats(job_id=job_id))

# ─── Process (SSE) ────────────────────────────────────────────────────────────

@app.route('/process', methods=['POST'])
@require_auth
def process():
    job_description_text = request.form.get('job_description_text')
    resume_files = request.files.getlist('resumes')
    job_id = request.form.get('job_id', type=int)

    if not job_description_text or not resume_files:
        return jsonify({'error': 'Missing job description or resumes.'}), 400

    resume_files.sort(key=lambda x: x.filename)

    saved_files = []
    for resume_file in resume_files:
        if not resume_file.filename:
            continue
        thread_id = str(uuid.uuid4())
        resume_path = os.path.join(app.config['UPLOAD_FOLDER'],
                                   f"{thread_id}_{resume_file.filename}")
        resume_file.save(resume_path)
        saved_files.append({'filename': resume_file.filename,
                            'thread_id': thread_id, 'path': resume_path})

    job_info  = get_job(job_id) if job_id else None
    job_title = job_info['title'] if job_info else 'General Applications'

    workers = min(MAX_CONCURRENT_RESUMES, len(saved_files))

    def generate():
        total        = len(saved_files)
        event_queue  = queue.Queue()
        all_results  = []           # collects result payloads for post-batch ranking

        # Announce how many resumes will be processed and how many run in parallel
        yield f"data: {json.dumps({'type': 'batch_start', 'total': total, 'concurrent': workers})}\n\n"

        def process_one(file_info, index):
            """Runs in a worker thread — pushes all SSE events into event_queue."""
            filename    = file_info['filename']
            thread_id   = file_info['thread_id']
            resume_path = file_info['path']

            event_queue.put(json.dumps({
                'type': 'start', 'filename': filename,
                'thread_id': thread_id, 'total': total, 'index': index,
            }))

            try:
                resume_text   = parse_pdf_from_path(resume_path)
                initial_state = {
                    'job_description':         job_description_text,
                    'resume_content':          resume_text,
                    'refinement_instructions': '',
                    'refinement_count':        0,
                    'draft_quality_score':     0,
                    'quality_feedback':        '',
                    'skills_matrix':           {},
                    'bias_score':              0,
                    'bias_flags':              [],
                    'interview_questions':     [],
                    'career_analysis':         {},
                    'score_variance':          0,
                    'github_analysis':         {},
                }
                config = {
                    'configurable': {'thread_id': thread_id},
                    # LangSmith run metadata — makes traces filterable by job/file
                    'metadata': {
                        'filename':  filename,
                        'job_title': job_title,
                        'job_id':    str(job_id) if job_id else 'none',
                    },
                    'run_name': f'screen:{filename}',
                    'tags':     ['resume-screening', f'job-{job_id or "general"}'],
                }

                for chunk in recruitment_app.stream(initial_state, config=config, stream_mode='updates'):
                    node_name = next(iter(chunk))
                    event_queue.put(json.dumps({
                        'type': 'node', 'filename': filename, 'node': node_name,
                    }))

                state_snapshot = recruitment_app.get_state(config)
                sv        = state_snapshot.values
                screening = sv.get('screening_results', {})
                drafted   = sv.get('drafted_email', {})
                score     = screening.get('matchScore', 0)
                decision  = 'shortlisted' if score >= 70 else 'rejected'

                skills_matrix        = sv.get('skills_matrix', {})
                draft_quality_score  = sv.get('draft_quality_score', 0)
                refinement_count     = sv.get('refinement_count', 0)
                bias_score           = sv.get('bias_score', 0)
                bias_flags           = sv.get('bias_flags', [])
                interview_questions  = sv.get('interview_questions', [])
                career_analysis      = sv.get('career_analysis', {})
                score_variance       = sv.get('score_variance', 0)
                github_analysis      = sv.get('github_analysis', {})

                # ── P4.1 Agentic Autonomy Mode ─────────────────────────────
                agentic_mode      = os.getenv('AGENTIC_MODE', 'false').lower() == 'true'
                auto_min_score    = int(os.getenv('AUTO_SEND_MIN_SCORE',   '80'))
                auto_min_quality  = int(os.getenv('AUTO_SEND_MIN_QUALITY', '8'))
                auto_sent         = False

                if (agentic_mode and
                        list(state_snapshot.next) == ['email_sender'] and
                        score >= auto_min_score and
                        draft_quality_score >= auto_min_quality):
                    try:
                        print(f"---AGENTIC: Auto-sending for {filename} "
                              f"(score={score}, quality={draft_quality_score})---")
                        for chunk in recruitment_app.stream(None, config=config, stream_mode='updates'):
                            node_name = next(iter(chunk))
                            event_queue.put(json.dumps({
                                'type': 'node', 'filename': filename, 'node': f'[auto] {node_name}',
                            }))
                        # Re-read state after auto-send
                        final_snap = recruitment_app.get_state(config)
                        sv         = final_snap.values
                        update_candidate_email_status(thread_id, 'sent')
                        log_action('email_sent', job_id=job_id,
                                   details=f'Agentic auto-send (score={score}, quality={draft_quality_score})')
                        auto_sent = True
                    except Exception as ae:
                        print(f"---AGENTIC: Auto-send failed for {filename}: {ae}---")

                candidate_id = save_candidate({
                    'name':                screening.get('candidateName', 'Unknown'),
                    'email':               screening.get('candidateEmail', 'N/A'),
                    'filename':            filename,
                    'job_id':              job_id,
                    'match_score':         score,
                    'decision':            decision,
                    'summary':             screening.get('summary', ''),
                    'skills_matched':      screening.get('skillsMatched', []),
                    'skills_missing':      screening.get('skillsMissing', []),
                    'email_subject':       drafted.get('subject', ''),
                    'email_body':          drafted.get('body', ''),
                    'thread_id':           thread_id,
                    'skills_matrix':       skills_matrix,
                    'interview_questions': interview_questions,
                    'bias_flags':          bias_flags,
                    'bias_score':          bias_score,
                    'career_analysis':     career_analysis,
                    'score_variance':      score_variance,
                    'github_analysis':     github_analysis,
                })

                log_action('screened', candidate_id=candidate_id, job_id=job_id,
                           details=f'Score {score} → {decision}')

                if score >= 85:
                    notify_slack(
                        candidate_name=screening.get('candidateName', 'Unknown'),
                        score=score, job_title=job_title,
                        email=screening.get('candidateEmail', 'N/A'),
                    )

                result_payload = {
                    'filename':     filename,
                    'thread_id':    thread_id,
                    'candidate_id': candidate_id,
                    'state': {
                        'screening_results':   screening,
                        'drafted_email':       drafted,
                        'final_status':        sv.get('final_status', ''),
                        'skills_matrix':       skills_matrix,
                        'draft_quality_score': draft_quality_score,
                        'refinement_count':    refinement_count,
                        'bias_score':          bias_score,
                        'bias_flags':          bias_flags,
                        'interview_questions': interview_questions,
                        'career_analysis':     career_analysis,
                        'score_variance':      score_variance,
                        'github_analysis':     github_analysis,
                    },
                    'next_step': [] if auto_sent else list(state_snapshot.next),
                    'auto_sent': auto_sent,
                }

                event_queue.put(json.dumps({'type': 'result', 'data': result_payload}))
                # Collect for post-batch ranking
                all_results.append(result_payload)

            except Exception as e:
                print(f"Error processing {filename}: {e}")
                event_queue.put(json.dumps({
                    'type': 'result',
                    'data': {
                        'filename':  filename,
                        'thread_id': thread_id,
                        'state': {
                            'screening_results': {
                                'candidateName': 'Error', 'matchScore': 0,
                                'summary': str(e), 'skillsMatched': [], 'skillsMissing': [],
                            },
                            'drafted_email': {}, 'final_status': '',
                        },
                        'next_step': [],
                    },
                }))
            finally:
                try:
                    os.remove(resume_path)
                except OSError:
                    pass
                event_queue.put('__DONE__')   # sentinel — one per resume

        # Submit all resumes; shutdown(wait=False) returns immediately while
        # threads continue running and pushing events into event_queue.
        executor = ThreadPoolExecutor(max_workers=workers)
        for i, file_info in enumerate(saved_files):
            executor.submit(process_one, file_info, i)
        executor.shutdown(wait=False)

        # Drain the queue and yield events as they arrive from worker threads.
        # 120 s timeout per get() guards against a hung Groq call.
        completed = 0
        while completed < total:
            try:
                msg = event_queue.get(timeout=120)
            except queue.Empty:
                break
            if msg == '__DONE__':
                completed += 1
            else:
                yield f"data: {msg}\n\n"

        # Post-batch: rank + talent intelligence (requires ≥2 shortlisted)
        shortlisted_results = [
            r for r in all_results
            if r.get('state', {}).get('screening_results', {}).get('matchScore', 0) >= 70
        ]
        if len(shortlisted_results) >= 2:
            try:
                yield f"data: {json.dumps({'type': 'ranking_start'})}\n\n"
                ranking = rank_candidates(shortlisted_results, job_description_text)
                yield f"data: {json.dumps({'type': 'batch_ranking', 'data': ranking})}\n\n"
            except Exception as rank_err:
                print(f"Batch ranking error (non-fatal): {rank_err}")

        if len(all_results) >= 2:
            try:
                intelligence = analyze_talent_pool(all_results, job_description_text)
                yield f"data: {json.dumps({'type': 'talent_intelligence', 'data': intelligence})}\n\n"
            except Exception as ti_err:
                print(f"Talent intelligence error (non-fatal): {ti_err}")

        yield f"data: {json.dumps({'type': 'complete'})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

# ─── Generate JD ──────────────────────────────────────────────────────────────

@app.route('/generate_jd', methods=['POST'])
@require_auth
def generate_jd():
    data = request.get_json() or {}
    notes = data.get('notes', '').strip()
    if not notes:
        return jsonify({'error': 'No notes provided.'}), 400
    try:
        generated_jd = generate_jd_from_notes(notes)
        return jsonify({'job_description': generated_jd})
    except Exception as e:
        print(f"JD generation error: {e}")
        return jsonify({'error': 'Internal server error.'}), 500

# ─── Refine Email ─────────────────────────────────────────────────────────────

@app.route('/refine_email', methods=['POST'])
@require_auth
def refine_email():
    data         = request.get_json() or {}
    thread_id    = data.get('thread_id')
    instructions = data.get('instructions')
    candidate_id = data.get('candidate_id')

    if not thread_id or not instructions:
        return jsonify({'error': 'Missing thread_id or instructions.'}), 400

    config = {'configurable': {'thread_id': thread_id}}
    try:
        state = recruitment_app.get_state(config).values
        state_for_refinement = {**state, 'refinement_instructions': instructions}

        if state.get('screening_results', {}).get('matchScore', 0) >= 70:
            from src.agents.candidate_communication_agent import draft_email_node
            new_state_part = draft_email_node(state_for_refinement)
        else:
            from src.agents.rejection_email_agent import draft_rejection_node
            new_state_part = draft_rejection_node(state_for_refinement)

        recruitment_app.update_state(config, new_state_part)

        if candidate_id:
            new_draft = new_state_part.get('drafted_email', {})
            update_candidate_email_draft(candidate_id, new_draft.get('subject'), new_draft.get('body'))
            log_action('email_refined', candidate_id=candidate_id,
                       details=instructions[:120])

        updated_state = recruitment_app.get_state(config).values
        return jsonify({'status': 'refined',
                        'new_state': {'drafted_email': updated_state.get('drafted_email', {})}})
    except Exception as e:
        print(f"Refinement error: {e}")
        return jsonify({'error': str(e)}), 500

# ─── Send Email ───────────────────────────────────────────────────────────────

@app.route('/send_email', methods=['POST'])
@require_auth
def send_email():
    data         = request.get_json() or {}
    thread_id    = data.get('thread_id')
    candidate_id = data.get('candidate_id')

    if not thread_id:
        return jsonify({'error': 'Missing thread_id.'}), 400

    config = {'configurable': {'thread_id': thread_id}}
    try:
        recruitment_app.invoke(None, config=config)
        final_state = recruitment_app.get_state(config).values
        update_candidate_email_status(thread_id, 'sent')
        if candidate_id:
            log_action('email_sent', candidate_id=candidate_id)
        return jsonify({'status': 'sent', 'final_status': final_state.get('final_status', 'Sent')})
    except Exception as e:
        print(f"Send email error: {e}")
        update_candidate_email_status(thread_id, 'failed')
        return jsonify({'error': str(e)}), 500

# ─── Export CSV ───────────────────────────────────────────────────────────────

@app.route('/export_report', methods=['POST'])
@require_auth
def export_report():
    data    = request.get_json() or {}
    results = data.get('results', [])
    output  = io.StringIO()
    writer  = csv.writer(output)
    writer.writerow(['Name', 'Email', 'Score', 'Decision', 'Email Status', 'Filename'])
    for r in results:
        state     = r.get('state', {})
        screening = state.get('screening_results', {})
        score     = screening.get('matchScore', 0)
        writer.writerow([
            screening.get('candidateName', 'Unknown'),
            screening.get('candidateEmail', 'N/A'),
            score,
            'Shortlisted' if score >= 70 else 'Rejected',
            state.get('final_status', 'Pending'),
            r.get('filename', '')
        ])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=recruitment_report.csv'})

# ─── PDF Reports ──────────────────────────────────────────────────────────────

@app.route('/api/candidates/<int:candidate_id>/report.pdf')
@require_auth
def candidate_pdf_report(candidate_id):
    candidate = get_candidate(candidate_id)
    if not candidate:
        return jsonify({'error': 'Not found'}), 404
    job_title = candidate.get('job_title') or ''
    try:
        pdf_bytes = generate_candidate_pdf(candidate, job_title)
        safe_name = (candidate.get('name') or 'candidate').replace(' ', '_')
        return Response(
            pdf_bytes, mimetype='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{safe_name}_report.pdf"'},
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/candidates/batch/report.pdf')
@require_auth
def batch_pdf_report():
    job_id    = request.args.get('job_id', type=int)
    job_info  = get_job(job_id) if job_id else None
    job_title = job_info['title'] if job_info else 'All Positions'
    candidates = get_candidates(job_id=job_id, limit=500)
    if not candidates:
        return jsonify({'error': 'No candidates found'}), 404
    try:
        pdf_bytes = generate_batch_pdf(candidates, job_title)
        safe_title = job_title.replace(' ', '_')[:30]
        return Response(
            pdf_bytes, mimetype='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="batch_{safe_title}.pdf"'},
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── JD Optimiser ─────────────────────────────────────────────────────────────

@app.route('/optimize_jd', methods=['POST'])
@require_auth
def optimize_jd_route():
    data = request.get_json(silent=True) or {}
    jd   = (data.get('job_description') or '').strip()
    if not jd:
        return jsonify({'error': 'job_description is required'}), 400
    result = optimize_jd(jd)
    return jsonify(result)

# ─── Follow-up Routes ──────────────────────────────────────────────────────────

@app.route('/api/followup/pending', methods=['GET'])
@require_auth
def followup_pending():
    days = int(request.args.get('days', os.getenv('FOLLOWUP_DAYS', '3')))
    candidates = get_followup_pending(days)
    return jsonify({'candidates': candidates, 'count': len(candidates)})


@app.route('/api/followup/<int:candidate_id>', methods=['POST'])
@require_auth
def send_followup(candidate_id):
    candidate = get_candidate(candidate_id)
    if not candidate:
        return jsonify({'error': 'Candidate not found'}), 404
    if candidate.get('email_status') != 'sent':
        return jsonify({'error': 'Initial email not yet sent for this candidate'}), 400

    from datetime import datetime as _dt
    sent_at    = candidate.get('email_sent_at') or ''
    days_since = 0
    if sent_at:
        try:
            delta = _dt.now() - _dt.fromisoformat(sent_at)
            days_since = delta.days
        except Exception:
            pass

    job_title   = candidate.get('job_title') or 'the position'
    followup    = generate_followup_email(
        candidate_name   = candidate.get('name', 'Candidate'),
        job_title        = job_title,
        original_subject = candidate.get('email_subject', ''),
        original_body    = candidate.get('email_body', ''),
        days_since_sent  = days_since,
    )

    # Send via the existing email utility
    from src.utils.email_sender import send_email as _send_smtp_util
    try:
        _send_smtp_util(
            to_address = candidate['email'],
            subject    = followup['subject'],
            body_html  = followup['body'],
        )
        mark_followup_sent(candidate_id)
        log_action('followup_sent', candidate_id=candidate_id,
                   details=f'Follow-up sent {days_since} days after initial email')
        return jsonify({'ok': True, 'subject': followup['subject']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Automated Follow-up Scheduler ────────────────────────────────────────────

def _run_scheduled_followups():
    """Background job: auto-send follow-ups for eligible candidates."""
    days = int(os.getenv('FOLLOWUP_DAYS', '3'))
    pending = get_followup_pending(days)
    if not pending:
        return
    print(f"--- SCHEDULER: {len(pending)} follow-up(s) to send ---")
    for c in pending:
        try:
            sent_at = c.get('email_sent_at') or ''
            days_since = 0
            if sent_at:
                from datetime import datetime as _dt
                delta = _dt.now() - _dt.fromisoformat(sent_at)
                days_since = delta.days

            followup = generate_followup_email(
                candidate_name   = c.get('name', 'Candidate'),
                job_title        = c.get('job_title') or 'the position',
                original_subject = c.get('email_subject', ''),
                original_body    = c.get('email_body', ''),
                days_since_sent  = days_since,
            )
            from src.utils.email_sender import send_email as _send_smtp_util
            _send_smtp_util(c['email'], followup['subject'], followup['body'])
            mark_followup_sent(c['id'])
            log_action('followup_sent', candidate_id=c['id'],
                       details=f'Auto follow-up sent {days_since}d after initial')
            print(f"--- SCHEDULER: follow-up sent to {c['name']} ({c['email']}) ---")
        except Exception as e:
            print(f"--- SCHEDULER: failed for candidate {c.get('id')}: {e} ---")


def _start_followup_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        import atexit
        interval_hours = int(os.getenv('FOLLOWUP_CHECK_INTERVAL_HOURS', '6'))
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            _run_scheduled_followups, 'interval',
            hours=interval_hours, id='followup_check', replace_existing=True,
        )
        scheduler.start()
        atexit.register(scheduler.shutdown)
        print(f"--- Follow-up scheduler started (every {interval_hours}h) ---")
    except ImportError:
        print("--- APScheduler not installed — automated follow-ups disabled ---")
    except Exception as e:
        print(f"--- Follow-up scheduler failed to start: {e} ---")


# Start scheduler once (skip in Werkzeug reloader child process)
import werkzeug.serving
if not werkzeug.serving.is_running_from_reloader():
    _start_followup_scheduler()


if __name__ == '__main__':
    from src.core.workflow import CHECKPOINT_DB
    print("\n" + "─" * 52)
    print("  AI Recruitment Assistant")
    print("─" * 52)
    print(f"  URL           : http://localhost:5001")
    print(f"  Username      : {ADMIN_USER}")
    print(f"  Password      : {ADMIN_PASS}")
    print(f"  Parallel workers : {MAX_CONCURRENT_RESUMES}")
    print(f"  Checkpoint DB : {CHECKPOINT_DB}")
    print("─" * 52 + "\n")
    app.run(port=5001, debug=True)
