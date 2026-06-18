import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def career_trajectory_node(state: dict) -> dict:
    """
    Analyses the resume text for career patterns:
    timeline, employment gaps, promotion trajectory, job-hop risk.
    Produces career_analysis dict + career_health_score.
    Non-fatal: returns empty analysis on any error.
    """
    resume_text = state.get('resume_content', '')
    jd_text     = state.get('job_description', '')
    screening   = state.get('screening_results', {})

    candidate_name = screening.get('candidateName', 'Unknown')

    llm = ChatGroq(
        model='llama-3.1-8b-instant',
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.1,
    )

    prompt = f"""You are a career analyst. Extract career trajectory data from this resume.

RESUME (first 3500 chars):
{resume_text[:3500]}

JOB REQUIREMENT SENIORITY HINT (first 300 chars): {jd_text[:300]}

Analyse the career timeline carefully. Extract dates, job titles, companies, and durations.

Return ONLY a valid JSON object. No markdown, no commentary.
{{
  "career_health_score": 78,
  "total_experience_years": 6.5,
  "job_count": 4,
  "avg_tenure_months": 19,
  "longest_tenure_months": 36,
  "shortest_tenure_months": 8,
  "employment_gaps": [
    {{"start": "2022-03", "end": "2022-10", "duration_months": 7, "severity": "moderate"}}
  ],
  "promotion_trajectory": "upward",
  "job_hopping_risk": "low",
  "career_progression": [
    {{"title": "Junior Developer", "company": "Acme", "duration_months": 18, "move_type": "step_up"}}
  ],
  "green_flags": ["Consistent promotions", "Long tenure at last 2 roles"],
  "red_flags": ["One 7-month gap in 2022"],
  "career_summary": "2-3 sentence career narrative summary"
}}

Rules:
- career_health_score: 0–100. 70+ = strong, 50–69 = average, <50 = concerns
- Penalise: gaps >6 months (-15), avg tenure <12 months (-20), >5 jobs in 5 years (-15)
- Reward: consistent promotions (+15), tenure >24 months (+10), progressive scope (+10)
- promotion_trajectory: "upward" | "lateral" | "downward" | "mixed" | "unknown"
- job_hopping_risk: "low" | "moderate" | "high"
- gap severity: "minor" (<3mo) | "moderate" (3-9mo) | "significant" (>9mo)
- move_type: "step_up" | "lateral" | "step_down" | "pivot"
- If dates are missing or unclear, make reasonable estimates and note "unknown" where needed
- Return ONLY the JSON object"""

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

        career_data = json.loads(raw)

        score      = career_data.get('career_health_score', 0)
        hop_risk   = career_data.get('job_hopping_risk', 'unknown')
        trajectory = career_data.get('promotion_trajectory', 'unknown')
        gaps       = career_data.get('employment_gaps', [])

        print(f"---CAREER TRAJ: health={score}/100, hop_risk={hop_risk}, "
              f"trajectory={trajectory}, gaps={len(gaps)} for {candidate_name}---")

        return {'career_analysis': career_data}

    except Exception as e:
        print(f"---CAREER TRAJ ERROR (non-fatal): {e}---")
        return {
            'career_analysis': {
                'career_health_score':  0,
                'job_hopping_risk':     'unknown',
                'promotion_trajectory': 'unknown',
                'employment_gaps':      [],
                'green_flags':          [],
                'red_flags':            [],
                'career_summary':       f'Analysis skipped: {e}',
            }
        }
