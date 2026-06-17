import os
import sys
import json
import uuid
import io
import csv
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, make_response
from dotenv import load_dotenv

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv()  # must run before auth imports so os.getenv picks up .env values

from src.utils.pdf_parser import parse_pdf_from_path
from src.core.workflow import app as recruitment_app
from src.agents.job_posting_agent import generate_jd_from_notes
from src.database.db import (
    init_db, get_all_jobs, get_job, create_job, update_job, delete_job,
    save_candidate, update_candidate_email_status, update_candidate_email_draft,
    get_candidates, get_candidate, get_stats, log_action, get_audit_log
)
from src.utils.slack_notify import notify_slack
from src.auth.utils import (
    verify_password, create_access_token, set_auth_cookie, clear_auth_cookie,
    TOKEN_EXPIRE_HOURS
)
from src.auth.dependencies import require_auth, get_current_username

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'recruitment-ai-secret-2026')

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

ADMIN_USER = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASS = os.getenv('ADMIN_PASSWORD', 'admin123')

# ─── Auth routes ──────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Browser form login — sets JWT in HttpOnly cookie on success."""
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == ADMIN_USER and password == ADMIN_PASS:
            response = make_response(redirect(url_for('index')))
            set_auth_cookie(response, username)
            return response
        error = 'Invalid username or password.'
    return render_template('login.html', error=error)


@app.route('/auth/login', methods=['POST'])
def api_login():
    """
    JSON API login — returns Bearer token for programmatic access.
    Body: {"username": "...", "password": "..."}
    Also sets the HttpOnly cookie so browser callers are covered too.
    """
    data     = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400

    if username != ADMIN_USER or password != ADMIN_PASS:
        return jsonify({'error': 'Invalid credentials'}), 401

    token    = create_access_token(username)
    response = jsonify({
        'access_token': token,
        'token_type':   'bearer',
        'expires_in':   TOKEN_EXPIRE_HOURS * 3600,
    })
    set_auth_cookie(response, username)
    return response


@app.route('/logout')
def logout():
    response = make_response(redirect(url_for('login')))
    clear_auth_cookie(response)
    return response

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

    def generate():
        total = len(saved_files)
        for i, file_info in enumerate(saved_files):
            filename  = file_info['filename']
            thread_id = file_info['thread_id']
            resume_path = file_info['path']

            yield f"data: {json.dumps({'type': 'start', 'filename': filename, 'thread_id': thread_id, 'total': total, 'index': i})}\n\n"

            try:
                resume_text = parse_pdf_from_path(resume_path)
                initial_state = {
                    'job_description': job_description_text,
                    'resume_content': resume_text,
                    'refinement_instructions': ''
                }
                config = {'configurable': {'thread_id': thread_id}}

                for chunk in recruitment_app.stream(initial_state, config=config, stream_mode='updates'):
                    node_name = next(iter(chunk))
                    yield f"data: {json.dumps({'type': 'node', 'filename': filename, 'node': node_name})}\n\n"

                state_snapshot = recruitment_app.get_state(config)
                sv = state_snapshot.values
                screening = sv.get('screening_results', {})
                drafted   = sv.get('drafted_email', {})
                score     = screening.get('matchScore', 0)
                decision  = 'shortlisted' if score >= 70 else 'rejected'

                candidate_id = save_candidate({
                    'name': screening.get('candidateName', 'Unknown'),
                    'email': screening.get('candidateEmail', 'N/A'),
                    'filename': filename,
                    'job_id': job_id,
                    'match_score': score,
                    'decision': decision,
                    'summary': screening.get('summary', ''),
                    'skills_matched': screening.get('skillsMatched', []),
                    'skills_missing': screening.get('skillsMissing', []),
                    'email_subject': drafted.get('subject', ''),
                    'email_body': drafted.get('body', ''),
                    'thread_id': thread_id
                })

                log_action('screened', candidate_id=candidate_id, job_id=job_id,
                           details=f'Score {score} → {decision}')

                if score >= 85:
                    notify_slack(
                        candidate_name=screening.get('candidateName', 'Unknown'),
                        score=score,
                        job_title=job_title,
                        email=screening.get('candidateEmail', 'N/A')
                    )

                result_payload = {
                    'filename': filename,
                    'thread_id': thread_id,
                    'candidate_id': candidate_id,
                    'state': {
                        'screening_results': screening,
                        'drafted_email': drafted,
                        'final_status': sv.get('final_status', '')
                    },
                    'next_step': list(state_snapshot.next)
                }
                yield f"data: {json.dumps({'type': 'result', 'data': result_payload})}\n\n"

            except Exception as e:
                print(f"Error processing {filename}: {e}")
                err_payload = {
                    'filename': filename, 'thread_id': thread_id,
                    'state': {'screening_results': {'candidateName': 'Error', 'matchScore': 0,
                              'summary': str(e), 'skillsMatched': [], 'skillsMissing': []},
                              'drafted_email': {}, 'final_status': ''},
                    'next_step': []
                }
                yield f"data: {json.dumps({'type': 'result', 'data': err_payload})}\n\n"
            finally:
                try:
                    os.remove(resume_path)
                except OSError:
                    pass

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

if __name__ == '__main__':
    init_db()
    print("\n" + "─" * 52)
    print("  AI Recruitment Assistant")
    print("─" * 52)
    print(f"  URL      : http://localhost:5001")
    print(f"  Username : {ADMIN_USER}")
    print(f"  Password : {ADMIN_PASS}")
    print("─" * 52 + "\n")
    app.run(port=5001, debug=True)
