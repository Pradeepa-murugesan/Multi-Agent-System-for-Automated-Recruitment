import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def skills_intelligence_node(state: dict) -> dict:
    """
    Runs after resume_screener. Produces a skills_matrix with per-skill
    confidence, recency, and level — plus seniority signals and inferred skills.
    Non-fatal: returns empty matrix on any failure so the main pipeline continues.
    """
    resume_content    = state.get('resume_content', '')
    job_description   = state.get('job_description', '')
    screening_results = state.get('screening_results', {})

    matched = json.dumps(screening_results.get('skillsMatched', []))
    missing = json.dumps(screening_results.get('skillsMissing', []))

    llm = ChatGroq(
        model='llama-3.3-70b-versatile',
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.1,
    )

    prompt = f"""You are a senior technical recruiter performing deep skills analysis.

JOB DESCRIPTION (first 1500 chars):
{job_description[:1500]}

RESUME (first 3000 chars):
{resume_content[:3000]}

ALREADY IDENTIFIED — Matched: {matched} | Missing: {missing}

Return ONLY a valid JSON object. No commentary, no markdown fences.

{{
  "skills_analysis": {{
    "SkillName": {{
      "present": true,
      "confidence": 0.95,
      "recency": "current",
      "level": "senior",
      "evidence": "one-line quote or reasoning"
    }}
  }},
  "transferable_skills": ["skill1"],
  "inferred_skills": {{
    "InferredSkill": {{"inferred_from": "KnownSkill", "confidence": 0.75}}
  }},
  "seniority_signals": {{
    "level": "senior",
    "indicators": ["led team of 8", "5 years exp"]
  }},
  "red_flags": []
}}

Rules:
- Analyse the 8 most relevant skills only (present AND missing).
- recency: "current" <1yr, "recent" 1-3yr, "dated" >3yr, "unknown" if unclear.
- level: "beginner" | "intermediate" | "senior" | "expert" | "unknown".
- seniority level: "junior" | "mid" | "senior" | "lead" | "unknown".
- Return ONLY the JSON object."""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip()

        # Strip accidental markdown fences
        if '```json' in raw:
            raw = raw.split('```json')[1].split('```')[0].strip()
        elif '```' in raw:
            raw = raw.split('```')[1].split('```')[0].strip()

        # Handle case where model wraps in extra text
        start = raw.find('{')
        end   = raw.rfind('}') + 1
        if start != -1 and end > start:
            raw = raw[start:end]

        skills_data = json.loads(raw)

        seniority = skills_data.get('seniority_signals', {})
        updated_screening = {
            **screening_results,
            'seniorityLevel':      seniority.get('level', 'unknown'),
            'seniorityIndicators': seniority.get('indicators', []),
        }

        print(f"---SKILLS INTEL: seniority={seniority.get('level','unknown')}, "
              f"analysed={len(skills_data.get('skills_analysis', {}))} skills---")

        return {
            'skills_matrix':    skills_data,
            'screening_results': updated_screening,
        }

    except Exception as e:
        print(f"---SKILLS INTEL ERROR (non-fatal): {e}---")
        return {
            'skills_matrix': {},
            'screening_results': {
                **screening_results,
                'seniorityLevel':      'unknown',
                'seniorityIndicators': [],
            },
        }
