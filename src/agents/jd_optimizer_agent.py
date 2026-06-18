import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def optimize_jd(job_description: str) -> dict:
    """
    Analyses a job description for quality, bias, clarity, and completeness.
    Returns a structured report with issues, suggestions, and a quality score.
    Non-fatal: returns a safe fallback on any error.
    """
    if not job_description or len(job_description.strip()) < 50:
        return {
            'quality_score': 0,
            'issues':        [{'severity': 'error', 'category': 'length', 'text': 'Job description is too short to analyse.'}],
            'suggestions':   [],
            'summary':       'Please provide a more detailed job description.',
        }

    llm = ChatGroq(
        model='llama-3.3-70b-versatile',
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.1,
    )

    prompt = f"""You are a senior Talent Acquisition expert and DEI (Diversity, Equity & Inclusion) specialist.
Analyse this job description for quality, inclusivity, clarity, and completeness.

JOB DESCRIPTION:
{job_description[:3000]}

Evaluate across these categories:
1. BIAS LANGUAGE — gendered words ("ninja", "rockstar", "dominant"), age proxies ("recent grad", "digital native"), cultural markers
2. VAGUE REQUIREMENTS — "strong communication skills", "fast-paced environment", "good team player" (unmeasurable)
3. OVERQUALIFIED BARRIERS — years of experience requirements, degree requirements for roles that don't need them
4. MISSING SECTIONS — compensation range, remote/hybrid policy, team size, growth opportunities
5. INCLUSIVITY — does it encourage diverse candidates? Does it use "we welcome applications from..."?
6. CLARITY — is the role's main responsibility clearly stated in the first paragraph?

Quality score guide: 90+ = excellent, 70-89 = good, 50-69 = needs improvement, <50 = significant issues

Return ONLY valid JSON. No markdown, no commentary.
{{
  "quality_score": 72,
  "issues": [
    {{"severity": "warning", "category": "bias_language", "text": "'Rockstar developer' — gendered/exclusionary language", "suggestion": "Replace with 'Skilled developer' or describe the specific qualities needed"}},
    {{"severity": "error", "category": "vague_requirement", "text": "'Strong communication skills' — unmeasurable", "suggestion": "Specify: 'Writes clear technical documentation and presents findings to non-technical stakeholders weekly'"}},
    {{"severity": "info", "category": "missing_section", "text": "No salary range mentioned", "suggestion": "Adding a salary range increases application rates by up to 30% and attracts better-matched candidates"}}
  ],
  "suggestions": [
    "Add a compensation range to attract more relevant candidates",
    "Replace vague adjectives with specific, measurable outcomes",
    "Add an inclusion statement encouraging candidates from underrepresented groups"
  ],
  "strength_highlights": ["Clear role title", "Specific technical stack listed"],
  "summary": "The JD is reasonably clear but contains 2 bias language issues and lacks salary information. Addressing these could improve candidate quality and diversity."
}}

Severity levels:
- "error": significant problem that may actively discourage qualified candidates
- "warning": moderate concern worth fixing
- "info": optional improvement

Return only issues that actually exist in the JD. Do not fabricate issues."""

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

        score  = result.get('quality_score', 0)
        issues = result.get('issues', [])
        errors = [i for i in issues if i.get('severity') == 'error']
        warns  = [i for i in issues if i.get('severity') == 'warning']

        print(f"---JD OPTIMIZER: score={score}/100, errors={len(errors)}, warnings={len(warns)}---")

        return result

    except Exception as e:
        print(f"---JD OPTIMIZER ERROR: {e}---")
        return {
            'quality_score':      0,
            'issues':             [{'severity': 'error', 'category': 'system', 'text': f'Analysis failed: {e}', 'suggestion': 'Please try again.'}],
            'suggestions':        [],
            'strength_highlights': [],
            'summary':            f'Analysis could not be completed: {e}',
        }
