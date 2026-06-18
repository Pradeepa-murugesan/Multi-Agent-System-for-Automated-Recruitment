import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def rank_candidates(candidates_data: list, job_description: str) -> dict:
    """
    Post-batch comparative ranking of all shortlisted candidates.
    Called from main.py after all parallel workers complete.

    candidates_data: list of result payloads from main.py (state dicts with
                     screening_results, skills_matrix, draft_quality_score, etc.)
    job_description: raw JD text used for the batch.

    Returns a ranking dict with ranked_candidates, recommendation, summary.
    Non-fatal: returns empty result on any error.
    """
    if len(candidates_data) < 2:
        return {
            'ranked_candidates': [],
            'summary':           'Only one candidate — ranking requires ≥2.',
            'top_pick':          None,
            'hire_confidence':   'n/a',
        }

    # Build a compact candidate digest for the prompt
    digests = []
    for i, c in enumerate(candidates_data):
        state    = c.get('state', {})
        sc       = state.get('screening_results', {})
        sm       = state.get('skills_matrix', {})
        filename = c.get('filename', f'candidate_{i+1}')

        skills_analysis = sm.get('skills_analysis', {})
        high_conf = [
            k for k, v in skills_analysis.items()
            if v.get('confidence', 0) >= 0.8 and v.get('present')
        ]
        seniority_signals = sm.get('seniority_signals', {})
        red_flags = sm.get('red_flags', [])

        digests.append({
            'id':              i,
            'name':            sc.get('candidateName', filename),
            'score':           sc.get('matchScore', 0),
            'seniority':       sc.get('seniorityLevel', 'unknown'),
            'skills_matched':  sc.get('skillsMatched', []),
            'skills_missing':  sc.get('skillsMissing', []),
            'high_conf_skills': high_conf,
            'seniority_indicators': seniority_signals.get('indicators', []),
            'red_flags':       red_flags,
            'quality_score':   state.get('draft_quality_score', 0),
            'summary':         sc.get('summary', '')[:250],
        })

    llm = ChatGroq(
        model='llama-3.3-70b-versatile',
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.1,
    )

    prompt = f"""You are a head of talent acquisition making a final hiring recommendation.

JOB DESCRIPTION (first 800 chars):
{job_description[:800]}

SHORTLISTED CANDIDATES:
{json.dumps(digests, indent=2)}

Rank ALL {len(digests)} candidates from most to least suitable for this role.
Consider: skills match depth (not just count), seniority fit, experience recency, red flags.
Be decisive — identify a clear top pick and explain why.

Return ONLY valid JSON. No markdown, no commentary.
{{
  "ranked_candidates": [
    {{
      "rank": 1,
      "candidate_id": 0,
      "name": "Candidate Name",
      "rationale": "2-3 sentence comparative rationale — why ranked here vs others",
      "strengths": ["Strength 1", "Strength 2"],
      "concerns": ["Concern 1"],
      "interview_priority": "immediate | standard | backup"
    }}
  ],
  "top_pick": "Name of top candidate",
  "summary": "2-3 sentence hiring recommendation comparing the shortlist",
  "hire_confidence": "high | medium | low",
  "recommendation": "Hire top candidate immediately | Interview all shortlisted | Expand search"
}}"""

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

        result = json.loads(raw)

        print(f"---RANKING: top={result.get('top_pick','?')}, "
              f"confidence={result.get('hire_confidence','?')}, "
              f"ranked {len(result.get('ranked_candidates',[]))} candidates---")

        return result

    except Exception as e:
        print(f"---RANKING ERROR (non-fatal): {e}---")
        # Fallback: sort by score
        sorted_c = sorted(digests, key=lambda x: x['score'], reverse=True)
        ranked = [{
            'rank':               i + 1,
            'candidate_id':       c['id'],
            'name':               c['name'],
            'rationale':          f"Score: {c['score']}%",
            'strengths':          c['skills_matched'][:3],
            'concerns':           c['skills_missing'][:2],
            'interview_priority': 'immediate' if i == 0 else 'standard',
        } for i, c in enumerate(sorted_c)]
        return {
            'ranked_candidates': ranked,
            'top_pick':          sorted_c[0]['name'] if sorted_c else None,
            'summary':           f'Score-based fallback ranking (AI unavailable: {e})',
            'hire_confidence':   'low',
            'recommendation':    'Interview all shortlisted',
        }
