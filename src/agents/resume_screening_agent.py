import os
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from src.utils.helpers import clean_and_parse_json

# ─── Shared prompt ────────────────────────────────────────────────────────────

_PROMPT_TEMPLATE = """
You are an expert AI recruitment assistant. Your task is to analyse the provided Resume against
the Job Description and return a structured JSON object.

CRITICAL INSTRUCTIONS:
1. Extract the candidate's full name, email address, calculate a matchScore (0-100), write a 2-3 sentence summary.
2. Identify skillsMatched (array of strings — specific skills from the JD the candidate clearly demonstrates)
   and skillsMissing (array of strings — key JD skills the candidate appears to lack).
3. Return ONLY a single valid JSON object with double-quoted keys and values.

EXAMPLE OUTPUT:
{{
    "candidateName": "Sanjay Kumar",
    "candidateEmail": "sanjay.k@example.com",
    "matchScore": 85,
    "summary": "Sanjay Kumar is a strong candidate with 5 years of Python experience, aligning well with the job requirements.",
    "skillsMatched": ["Python", "AWS", "Django", "REST APIs"],
    "skillsMissing": ["Kubernetes", "GraphQL"]
}}

Job Description:
{job_description}

Full Resume Text:
{resume}

JSON Output (Must use double quotes):
"""

def _run_model(model_name: str, job_description: str, resume_text: str) -> dict | None:
    """Runs a single screening model and returns parsed JSON or None on failure."""
    try:
        llm    = ChatGroq(model=model_name, api_key=os.getenv('GROQ_API_KEY'), temperature=0)
        prompt = ChatPromptTemplate.from_template(_PROMPT_TEMPLATE)
        chain  = prompt | llm
        resp   = chain.invoke({'job_description': job_description, 'resume': resume_text})
        return clean_and_parse_json(resp.content)
    except Exception as e:
        print(f"---SCREEN MODEL {model_name} ERROR: {e}---")
        return None


def _merge_results(r1: dict, r2: dict) -> tuple[dict, int]:
    """
    Merges two screening results into one.
    Returns (merged_result, variance_score).
    variance = absolute difference between the two match scores.
    """
    s1 = r1.get('matchScore', 0)
    s2 = r2.get('matchScore', 0)
    avg_score = round((s1 + s2) / 2)
    variance  = abs(s1 - s2)

    # Union skills (more inclusive)
    matched = list(set(r1.get('skillsMatched', []) + r2.get('skillsMatched', [])))
    # Intersection of missing (only flag gaps both models agree on)
    missing1 = set(r1.get('skillsMissing', []))
    missing2 = set(r2.get('skillsMissing', []))
    missing  = list(missing1 & missing2) or list(missing1)   # fallback to r1 if no overlap

    merged = {
        **r1,                        # base: name, email, summary from primary
        'matchScore':    avg_score,
        'skillsMatched': matched,
        'skillsMissing': missing,
        'model1Score':   s1,
        'model2Score':   s2,
        'scoreVariance': variance,
        'consensusFlag': variance > 15,  # significant disagreement
    }
    return merged, variance


def screen_resume_node(state) -> dict:
    print("---NODE: SCREENING RESUME---")

    job_description_text = state['job_description']
    resume_text          = state['resume_content']

    primary_model   = 'llama-3.3-70b-versatile'
    consensus_on    = os.getenv('ENABLE_CONSENSUS_SCORING', 'false').lower() == 'true'
    secondary_model = os.getenv('CONSENSUS_MODEL_2', 'gemma2-9b-it')

    score_variance = 0

    if consensus_on:
        print(f"---CONSENSUS MODE: {primary_model} + {secondary_model}---")
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut1 = ex.submit(_run_model, primary_model, job_description_text, resume_text)
            fut2 = ex.submit(_run_model, secondary_model, job_description_text, resume_text)
            r1 = fut1.result()
            r2 = fut2.result()

        if r1 and r2:
            results, score_variance = _merge_results(r1, r2)
            print(f"---CONSENSUS: model1={r1.get('matchScore')}%, model2={r2.get('matchScore')}%, "
                  f"avg={results.get('matchScore')}%, variance={score_variance}---")
        else:
            # Fall back to whichever succeeded
            results = r1 or r2
            if results:
                print(f"---CONSENSUS: one model failed, using fallback---")
    else:
        results = _run_model(primary_model, job_description_text, resume_text)

    # ── Email regex fallback ──────────────────────────────────────────────────
    if results and (not results.get('candidateEmail') or results.get('candidateEmail') == 'N/A'):
        print("---AI failed to find email. Trying regex fallback.---")
        match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', resume_text)
        if match:
            found = match.group(0)
            print(f"---Regex found email: {found}---")
            results['candidateEmail'] = found

    # ── Hard fallback ─────────────────────────────────────────────────────────
    if not results:
        print("ERROR: All models failed to produce valid JSON.")
        results = {
            'candidateName':  'Error',
            'candidateEmail': 'N/A',
            'matchScore':     0,
            'summary':        'Critical error: AI model failed to generate structured data.',
            'skillsMatched':  [],
            'skillsMissing':  [],
        }

    results.setdefault('skillsMatched', [])
    results.setdefault('skillsMissing', [])

    return {
        'screening_results': results,
        'score_variance':    score_variance,
    }
