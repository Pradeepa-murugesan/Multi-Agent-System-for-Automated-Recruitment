import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def analyze_talent_pool(candidates_data: list, job_description: str) -> dict:
    """
    Post-batch competitive talent intelligence.
    Benchmarks the candidate pool against market norms, identifies skill gaps,
    and provides hiring difficulty rating + actionable recommendations.

    candidates_data: list of result payloads from main.py
    Returns a dict with pool insights. Non-fatal: score-based fallback on error.
    """
    if not candidates_data:
        return {
            'hiring_difficulty': 'unknown',
            'pool_avg_score':    0,
            'summary':           'No candidates to analyse.',
            'recommendations':   [],
        }

    # Build pool digest
    scores      = []
    all_missing = []
    all_matched = []
    seniority_levels = []

    for c in candidates_data:
        state = c.get('state', {})
        sc    = state.get('screening_results', {})
        scores.append(sc.get('matchScore', 0))
        all_missing.extend(sc.get('skillsMissing', []))
        all_matched.extend(sc.get('skillsMatched', []))
        lvl = sc.get('seniorityLevel', 'unknown')
        if lvl and lvl != 'unknown':
            seniority_levels.append(lvl)

    pool_avg  = round(sum(scores) / len(scores)) if scores else 0
    shortlist = sum(1 for s in scores if s >= 70)
    rejected  = len(scores) - shortlist

    # Count skill frequencies
    from collections import Counter
    missing_counts = Counter(all_missing).most_common(8)
    matched_counts = Counter(all_matched).most_common(8)
    seniority_dist = Counter(seniority_levels)

    common_gaps  = [s for s, _ in missing_counts]
    top_skills   = [s for s, _ in matched_counts]

    # Hiring difficulty heuristic
    if pool_avg >= 75 and shortlist >= len(scores) * 0.4:
        difficulty = 'low'
    elif pool_avg >= 55 and shortlist >= len(scores) * 0.2:
        difficulty = 'medium'
    else:
        difficulty = 'high'

    llm = ChatGroq(
        model='llama-3.3-70b-versatile',
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.1,
    )

    prompt = f"""You are a talent acquisition strategist with deep market intelligence.

JOB DESCRIPTION (first 600 chars): {job_description[:600]}

CANDIDATE POOL STATISTICS:
- Total candidates: {len(scores)}
- Shortlisted (≥70%): {shortlist}
- Rejected: {rejected}
- Pool avg score: {pool_avg}%
- Score range: {min(scores) if scores else 0}% – {max(scores) if scores else 0}%
- Most common skill GAPS (what candidates are missing): {json.dumps(common_gaps)}
- Most common skill MATCHES (what candidates bring): {json.dumps(top_skills)}
- Seniority distribution: {dict(seniority_dist)}
- Estimated hiring difficulty: {difficulty}

Analyse this pool against typical market benchmarks for similar roles.
Be specific and actionable.

Return ONLY valid JSON. No markdown, no commentary.
{{
  "pool_quality": "above_average | average | below_average",
  "hiring_difficulty": "low | medium | high | very_high",
  "market_insight": "2-3 sentences comparing this pool to market norms for this type of role",
  "skill_gap_analysis": {{
    "critical_gaps": ["skills missing from most candidates that are actually important"],
    "nice_to_have_gaps": ["skills missing but likely overstated in JD"],
    "jd_may_be_overspecified": true
  }},
  "pool_strengths": ["What candidates in this pool are strong in"],
  "recommendations": [
    "Specific actionable recommendation 1",
    "Specific actionable recommendation 2"
  ],
  "time_to_fill_estimate": "1-2 weeks | 3-4 weeks | 1-2 months | >2 months",
  "summary": "2-3 sentence executive summary of the talent pool assessment"
}}

Be honest and specific. If the pool is strong, say so. If requirements need adjustment, say so."""

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

        # Attach computed stats for the frontend
        result['pool_avg_score']    = pool_avg
        result['total_candidates']  = len(scores)
        result['shortlisted_count'] = shortlist
        result['common_skill_gaps'] = common_gaps
        result['top_skills_in_pool'] = top_skills

        print(f"---TALENT INTEL: pool={pool_avg}% avg, difficulty={result.get('hiring_difficulty')}, "
              f"quality={result.get('pool_quality')}---")

        return result

    except Exception as e:
        print(f"---TALENT INTEL ERROR (non-fatal): {e}---")
        return {
            'pool_quality':       'average',
            'hiring_difficulty':  difficulty,
            'pool_avg_score':     pool_avg,
            'total_candidates':   len(scores),
            'shortlisted_count':  shortlist,
            'common_skill_gaps':  common_gaps,
            'top_skills_in_pool': top_skills,
            'recommendations':    ['Review JD requirements against common skill gaps.'],
            'summary':            f'Score-based analysis (AI unavailable: {e})',
            'market_insight':     '',
        }
