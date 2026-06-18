import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def bias_detection_node(state: dict) -> dict:
    """
    Audits the screening decision for potential hiring bias.
    Checks: proxy language, score–skills inconsistency, demographic proxies,
    and requirement phrasing. Warning-only — never blocks the pipeline.
    Adds bias_flags (list), bias_score (0–10), bias_summary to state.
    """
    screening     = state.get('screening_results', {})
    skills_matrix = state.get('skills_matrix', {})
    resume_text   = state.get('resume_content', '')
    jd_text       = state.get('job_description', '')

    candidate_name  = screening.get('candidateName', 'Unknown')
    match_score     = screening.get('matchScore', 0)
    skills_matched  = screening.get('skillsMatched', [])
    skills_missing  = screening.get('skillsMissing', [])
    summary         = screening.get('summary', '')
    seniority_level = screening.get('seniorityLevel', 'unknown')

    llm = ChatGroq(
        model='llama-3.1-8b-instant',
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.1,
    )

    prompt = f"""You are a Diversity, Equity & Inclusion auditor for AI hiring systems.
Your task: audit this screening decision for potential bias. Be analytical, not accusatory.

SCREENING DECISION:
- Candidate: {candidate_name}
- Match Score: {match_score}%
- Decision: {'Shortlisted' if match_score >= 70 else 'Rejected'}
- Skills Matched: {json.dumps(skills_matched)}
- Skills Missing: {json.dumps(skills_missing)}
- Seniority Inferred: {seniority_level}
- AI Summary: {summary[:400]}

JOB DESCRIPTION (first 800 chars): {jd_text[:800]}
RESUME SNIPPET (first 600 chars): {resume_text[:600]}

Analyze for these bias categories:
1. PROXY BIAS — Does the screening penalise non-traditional backgrounds, employment gaps, or non-prestigious institutions?
2. SCORE INCONSISTENCY — Does the score seem too high or low relative to skills matched/missing?
3. REQUIREMENT BIAS — Are any JD requirements phrased in ways that disadvantage protected groups (over-specifying years, degree requirements not needed for role)?
4. LANGUAGE BIAS — Does the screening summary use coded language (e.g., "polished", "articulate", "culture fit") that may encode bias?
5. AGE/GENDER PROXIES — Are experience year ranges, graduation year, or gendered language used as proxies?

Bias score guide: 0 = no bias detected, 3 = minor concerns, 6 = moderate — review recommended, 9–10 = significant bias detected.

Return ONLY valid JSON. No markdown, no commentary.
{{
  "bias_score": 2,
  "flags": ["Specific concern 1", "Specific concern 2"],
  "cleared": ["What was checked and found clean"],
  "summary": "One sentence overall assessment",
  "recommendation": "no_action | review_recommended | escalate"
}}

If no bias is found, return bias_score: 0, empty flags, and list what was checked in cleared."""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip()

        if '```json' in raw:
            raw = raw.split('```json')[1].split('```')[0].strip()
        elif '```' in raw:
            raw = raw.split('```')[1].split('```')[0].strip()
        start = raw.find('{')
        end   = raw.rfind('}') + 1
        if start != -1 and end > start:
            raw = raw[start:end]

        bias_data = json.loads(raw)

        bias_score     = int(bias_data.get('bias_score', 0))
        flags          = bias_data.get('flags', [])
        bias_summary   = bias_data.get('summary', '')
        recommendation = bias_data.get('recommendation', 'no_action')

        print(f"---BIAS AUDIT: score={bias_score}/10, flags={len(flags)}, "
              f"rec={recommendation} for {candidate_name}---")
        if flags:
            print(f"   Flags: {'; '.join(flags[:2])}")

        # Attach bias metadata to screening_results so it's visible in the result
        updated_screening = {
            **screening,
            'biasScore':          bias_score,
            'biasFlags':          flags,
            'biasSummary':        bias_summary,
            'biasRecommendation': recommendation,
        }

        return {
            'bias_score':       bias_score,
            'bias_flags':       flags,
            'screening_results': updated_screening,
        }

    except Exception as e:
        print(f"---BIAS AUDIT ERROR (non-fatal): {e}---")
        return {
            'bias_score': 0,
            'bias_flags': [],
            'screening_results': {
                **screening,
                'biasScore':          0,
                'biasFlags':          [],
                'biasSummary':        'Audit skipped.',
                'biasRecommendation': 'no_action',
            },
        }
