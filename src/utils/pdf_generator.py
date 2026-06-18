"""
PDF report generator using fpdf2.
Generates per-candidate and batch reports.
"""
import io
from datetime import datetime


def _safe(text) -> str:
    """Sanitize text for FPDF (remove unsupported chars)."""
    if not text:
        return ''
    return str(text).encode('latin-1', errors='replace').decode('latin-1')


def _score_color(score: int) -> tuple:
    """Returns (R, G, B) for a score value."""
    if score >= 70:
        return (4, 120, 87)     # green
    elif score >= 50:
        return (146, 64, 14)    # amber
    return (185, 28, 28)        # red


def generate_candidate_pdf(candidate: dict, job_title: str = '') -> bytes:
    """
    Per-candidate PDF report. Returns raw bytes.
    Requires: pip install fpdf2
    """
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # ── Header ──────────────────────────────────────────────────────────────
    pdf.set_fill_color(79, 70, 229)   # indigo
    pdf.rect(0, 0, 210, 28, 'F')
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_xy(10, 8)
    pdf.cell(190, 8, 'AI Recruitment Report', align='C')
    pdf.set_font('Helvetica', '', 9)
    pdf.set_xy(10, 17)
    pdf.cell(190, 6, f'Generated {datetime.now().strftime("%d %b %Y %H:%M")}', align='C')
    pdf.ln(20)

    # ── Candidate identity ───────────────────────────────────────────────────
    pdf.set_text_color(15, 23, 42)
    pdf.set_font('Helvetica', 'B', 18)
    pdf.cell(0, 10, _safe(candidate.get('name', 'Unknown')), ln=True)

    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(71, 85, 105)
    if candidate.get('email'):
        pdf.cell(0, 6, _safe(candidate['email']), ln=True)
    if job_title:
        pdf.cell(0, 6, f'Applied for: {_safe(job_title)}', ln=True)
    if candidate.get('created_at'):
        pdf.cell(0, 6, f'Processed: {_safe(candidate["created_at"][:10])}', ln=True)
    pdf.ln(4)

    # ── Score + Decision ─────────────────────────────────────────────────────
    score    = candidate.get('match_score', 0)
    decision = candidate.get('decision', 'pending')
    r, g, b  = _score_color(score)

    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(10, pdf.get_y(), 190, 22, 'FD')
    pdf.set_xy(12, pdf.get_y() + 4)
    pdf.set_font('Helvetica', 'B', 22)
    pdf.set_text_color(r, g, b)
    pdf.cell(25, 10, f'{score}%')
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(0, 10, f'Match Score  |  Decision: {_safe(decision).capitalize()}')
    pdf.ln(26)

    def section(title: str):
        pdf.set_text_color(79, 70, 229)
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 8, title, ln=True)
        pdf.set_draw_color(199, 210, 254)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
        pdf.set_text_color(51, 65, 85)
        pdf.set_font('Helvetica', '', 10)

    # ── Summary ──────────────────────────────────────────────────────────────
    if candidate.get('summary'):
        section('AI Analysis Summary')
        pdf.multi_cell(0, 5, _safe(candidate['summary']))
        pdf.ln(4)

    # ── Skills ───────────────────────────────────────────────────────────────
    matched = candidate.get('skills_matched', [])
    missing = candidate.get('skills_missing', [])
    if matched or missing:
        section('Skills Assessment')
        if matched:
            pdf.set_font('Helvetica', 'B', 9)
            pdf.set_text_color(5, 150, 105)
            pdf.cell(0, 6, 'Matched Skills:', ln=True)
            pdf.set_font('Helvetica', '', 9)
            pdf.set_text_color(51, 65, 85)
            pdf.multi_cell(0, 5, _safe('  ·  '.join(matched)))
            pdf.ln(2)
        if missing:
            pdf.set_font('Helvetica', 'B', 9)
            pdf.set_text_color(185, 28, 28)
            pdf.cell(0, 6, 'Missing Skills:', ln=True)
            pdf.set_font('Helvetica', '', 9)
            pdf.set_text_color(51, 65, 85)
            pdf.multi_cell(0, 5, _safe('  ·  '.join(missing)))
        pdf.ln(4)

    # ── Career Analysis ───────────────────────────────────────────────────────
    career = candidate.get('career_analysis', {})
    if career.get('career_health_score'):
        section('Career Trajectory')
        ch = career.get('career_health_score', 0)
        cr, cg, cb = _score_color(ch)
        pdf.set_text_color(cr, cg, cb)
        pdf.set_font('Helvetica', 'B', 11)
        pdf.cell(0, 6, f'Career Health Score: {ch}/100', ln=True)
        pdf.set_text_color(51, 65, 85)
        pdf.set_font('Helvetica', '', 10)
        lines = [
            f'Experience: {career.get("total_experience_years", "?")} years',
            f'Avg Tenure: {career.get("avg_tenure_months", "?")} months',
            f'Trajectory: {career.get("promotion_trajectory", "unknown").capitalize()}',
            f'Job-hop Risk: {career.get("job_hopping_risk", "unknown").capitalize()}',
        ]
        for line in lines:
            pdf.cell(0, 5, _safe(line), ln=True)
        if career.get('career_summary'):
            pdf.ln(2)
            pdf.multi_cell(0, 5, _safe(career['career_summary']))
        pdf.ln(4)

    # ── Bias Audit ────────────────────────────────────────────────────────────
    bias_score = candidate.get('bias_score', 0)
    bias_flags = candidate.get('bias_flags', [])
    if bias_score > 0 or bias_flags:
        section('DEI / Bias Audit')
        br, bg, bb = _score_color(100 - bias_score * 10)  # invert: higher = worse
        pdf.set_text_color(br, bg, bb)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(0, 6, f'Bias Risk Score: {bias_score}/10', ln=True)
        if bias_flags:
            pdf.set_text_color(51, 65, 85)
            pdf.set_font('Helvetica', '', 9)
            for flag in bias_flags:
                pdf.multi_cell(0, 5, _safe(f'  • {flag}'))
        pdf.ln(4)

    # ── Interview Questions ───────────────────────────────────────────────────
    questions = candidate.get('interview_questions', [])
    if questions:
        section('Suggested Interview Questions')
        q_by_type: dict = {}
        for q in questions:
            t = q.get('type', 'general')
            q_by_type.setdefault(t, []).append(q)
        type_labels = {
            'technical': 'Technical', 'behavioral': 'Behavioral',
            'skills-gap': 'Skills Gap', 'culture': 'Culture Fit'
        }
        for qtype in ['technical', 'behavioral', 'skills-gap', 'culture']:
            qs = q_by_type.get(qtype, [])
            if not qs:
                continue
            pdf.set_font('Helvetica', 'B', 9)
            pdf.set_text_color(67, 56, 202)
            pdf.cell(0, 6, type_labels.get(qtype, qtype).upper(), ln=True)
            pdf.set_font('Helvetica', '', 9)
            pdf.set_text_color(51, 65, 85)
            for i, q in enumerate(qs, 1):
                pdf.multi_cell(0, 5, _safe(f'{i}. {q.get("question", "")}'))
                if q.get('rationale'):
                    pdf.set_text_color(148, 163, 184)
                    pdf.multi_cell(0, 4, _safe(f'   → {q["rationale"]}'))
                    pdf.set_text_color(51, 65, 85)
            pdf.ln(2)
        pdf.ln(2)

    # ── Email Draft ───────────────────────────────────────────────────────────
    if candidate.get('email_subject') and candidate.get('email_body'):
        section('Email Draft')
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(0, 6, _safe(f'Subject: {candidate["email_subject"]}'), ln=True)
        pdf.set_font('Helvetica', '', 9)
        pdf.multi_cell(0, 5, _safe(candidate['email_body']))
        pdf.ln(4)

    # ── GitHub ────────────────────────────────────────────────────────────────
    gh = candidate.get('github_analysis', {})
    if gh.get('found'):
        section('GitHub Profile')
        pdf.cell(0, 5, _safe(f'@{gh.get("username")}  |  Score: {gh.get("github_score", 0)}/100'), ln=True)
        pdf.cell(0, 5, _safe(f'Repos: {gh.get("public_repos", 0)}  |  Stars: {gh.get("total_stars", 0)}  |  Followers: {gh.get("followers", 0)}'), ln=True)
        if gh.get('languages'):
            pdf.cell(0, 5, _safe(f'Languages: {", ".join(gh["languages"][:6])}'), ln=True)
        pdf.ln(4)

    # ── Footer ────────────────────────────────────────────────────────────────
    pdf.set_y(-12)
    pdf.set_font('Helvetica', 'I', 7)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(0, 5, 'Generated by AI Recruitment Assistant', align='C')

    buf = io.BytesIO()
    buf.write(pdf.output())
    return buf.getvalue()


def generate_batch_pdf(candidates: list, job_title: str = '', ranking: dict = None) -> bytes:
    """
    Batch PDF report for all candidates in a job pipeline. Returns raw bytes.
    """
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # ── Header ──────────────────────────────────────────────────────────────
    pdf.set_fill_color(79, 70, 229)
    pdf.rect(0, 0, 210, 28, 'F')
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_xy(10, 8)
    pdf.cell(190, 8, f'Batch Recruitment Report — {_safe(job_title)}', align='C')
    pdf.set_font('Helvetica', '', 9)
    pdf.set_xy(10, 17)
    pdf.cell(190, 6, f'Generated {datetime.now().strftime("%d %b %Y %H:%M")} · {len(candidates)} candidates', align='C')
    pdf.ln(20)

    def section(title: str):
        pdf.set_text_color(79, 70, 229)
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 8, title, ln=True)
        pdf.set_draw_color(199, 210, 254)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
        pdf.set_text_color(51, 65, 85)
        pdf.set_font('Helvetica', '', 10)

    # ── Summary Stats ─────────────────────────────────────────────────────────
    total      = len(candidates)
    shortlisted = [c for c in candidates if c.get('decision') == 'shortlisted']
    rejected    = [c for c in candidates if c.get('decision') == 'rejected']
    avg_score  = round(sum(c.get('match_score', 0) for c in candidates) / total) if total else 0

    section('Pipeline Summary')
    stats = [
        ('Total Screened', str(total)),
        ('Shortlisted', str(len(shortlisted))),
        ('Rejected', str(len(rejected))),
        ('Avg Score', f'{avg_score}%'),
    ]
    for label, val in stats:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(55, 6, f'{label}:', ln=False)
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 6, val, ln=True)
    pdf.ln(4)

    # ── Ranking (if available) ────────────────────────────────────────────────
    if ranking and ranking.get('ranked_candidates'):
        section('AI Ranking')
        if ranking.get('summary'):
            pdf.multi_cell(0, 5, _safe(ranking['summary']))
            pdf.ln(2)
        for r in ranking['ranked_candidates']:
            medals = ['1st', '2nd', '3rd']
            medal  = medals[r['rank'] - 1] if r['rank'] <= 3 else f"#{r['rank']}"
            pdf.set_font('Helvetica', 'B', 10)
            pdf.set_text_color(79, 70, 229)
            pdf.cell(0, 6, f'{medal}  {_safe(r.get("name", ""))} — {r.get("interview_priority", "").capitalize()}', ln=True)
            pdf.set_font('Helvetica', '', 9)
            pdf.set_text_color(51, 65, 85)
            pdf.multi_cell(0, 5, _safe(r.get('rationale', '')))
            pdf.ln(2)
        pdf.ln(2)

    # ── Candidate table ───────────────────────────────────────────────────────
    section('Candidate Summary Table')
    # Header row
    pdf.set_fill_color(241, 245, 249)
    pdf.set_font('Helvetica', 'B', 8)
    pdf.set_text_color(51, 65, 85)
    col_w = [65, 40, 18, 25, 42]
    headers = ['Name', 'Email', 'Score', 'Decision', 'Status']
    for h, w in zip(headers, col_w):
        pdf.cell(w, 6, h, border=1, fill=True)
    pdf.ln()

    pdf.set_font('Helvetica', '', 8)
    sorted_cands = sorted(candidates, key=lambda c: c.get('match_score', 0), reverse=True)
    for c in sorted_cands:
        score = c.get('match_score', 0)
        r, g, b = _score_color(score)
        row_vals = [
            _safe(c.get('name', ''))[:30],
            _safe(c.get('email', ''))[:25],
            f"{score}%",
            _safe(c.get('decision', ''))[:10],
            _safe(c.get('email_status', ''))[:15],
        ]
        for val, w in zip(row_vals, col_w):
            if val == row_vals[2]:   # score cell coloured
                pdf.set_text_color(r, g, b)
            else:
                pdf.set_text_color(51, 65, 85)
            pdf.cell(w, 5, val, border=1)
        pdf.ln()
    pdf.ln(4)

    # ── Footer ────────────────────────────────────────────────────────────────
    pdf.set_y(-12)
    pdf.set_font('Helvetica', 'I', 7)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(0, 5, 'Generated by AI Recruitment Assistant', align='C')

    buf = io.BytesIO()
    buf.write(pdf.output())
    return buf.getvalue()
