import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def interview_question_node(state: dict) -> dict:
    """
    Generates tailored interview questions for shortlisted candidates.
    Only runs on the invitation track (matchScore >= 70).
    Produces 8 questions: 3 technical, 2 behavioral, 2 skills-gap, 1 culture-fit.
    Non-fatal: returns empty list on any error.
    """
    screening     = state.get('screening_results', {})
    skills_matrix = state.get('skills_matrix', {})
    jd_text       = state.get('job_description', '')
    resume_text   = state.get('resume_content', '')

    candidate_name  = screening.get('candidateName', 'Unknown')
    skills_matched  = screening.get('skillsMatched', [])
    skills_missing  = screening.get('skillsMissing', [])
    seniority_level = screening.get('seniorityLevel', 'unknown')
    summary         = screening.get('summary', '')
    match_score     = screening.get('matchScore', 0)

    # Only generate for invitation-track candidates
    if match_score < 70:
        return {'interview_questions': []}

    # Gather deep evidence for each matched skill from skills_matrix
    skills_analysis = skills_matrix.get('skills_analysis', {})
    skill_evidence = []
    for skill, data in list(skills_analysis.items())[:5]:
        if data.get('present') and data.get('evidence'):
            skill_evidence.append(f"{skill}: {data['evidence']}")
    evidence_str = '; '.join(skill_evidence) if skill_evidence else 'Not available'

    llm = ChatGroq(
        model='llama-3.1-8b-instant',
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.4,  # some creativity in question phrasing
    )

    prompt = f"""You are a senior technical interviewer preparing for a candidate interview.

CANDIDATE: {candidate_name}
SENIORITY: {seniority_level}
MATCH SCORE: {match_score}%
SKILLS PRESENT: {json.dumps(skills_matched)}
SKILLS MISSING: {json.dumps(skills_missing)}
SKILL EVIDENCE: {evidence_str}
AI SUMMARY: {summary[:400]}

JOB DESCRIPTION (first 600 chars): {jd_text[:600]}

Generate exactly 8 interview questions personalised to THIS candidate.
- 3 TECHNICAL: Test their stated skills — reference the evidence snippets, probe depth
- 2 BEHAVIORAL: Use STAR-format prompts linked to the role's key challenges
- 2 SKILLS-GAP: Friendly probes on the missing skills (not gotchas — explore how they'd bridge the gap)
- 1 CULTURE: One open-ended question about values, growth, or team dynamics

Return ONLY valid JSON. No markdown, no commentary.
{{
  "questions": [
    {{
      "type": "technical",
      "question": "Can you walk me through your experience with...",
      "rationale": "Probes depth in Python based on evidence",
      "difficulty": "medium"
    }}
  ]
}}

Rules:
- Make each question specific to THIS candidate's background — not generic templates
- Difficulty: "easy" | "medium" | "hard"
- No yes/no questions
- Keep each question under 30 words"""

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

        data = json.loads(raw)
        questions = data.get('questions', [])

        print(f"---INTERVIEW PREP: generated {len(questions)} questions for {candidate_name}---")

        return {'interview_questions': questions}

    except Exception as e:
        print(f"---INTERVIEW PREP ERROR (non-fatal): {e}---")
        return {'interview_questions': []}
