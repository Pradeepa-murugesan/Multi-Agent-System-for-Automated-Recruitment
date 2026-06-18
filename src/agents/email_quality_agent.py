import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def draft_quality_checker_node(state: dict) -> dict:
    """
    Self-critique node — evaluates the drafted email quality.
    If score < DRAFT_QUALITY_THRESHOLD and refinement_count < MAX_DRAFT_REFINEMENTS,
    injects improvement feedback into refinement_instructions so the router
    can loop back to the appropriate drafter automatically.
    Non-fatal: passes with score=10 on any LLM / parse error.
    """
    drafted_email = state.get('drafted_email', {})
    screening     = state.get('screening_results', {})
    current_count = state.get('refinement_count', 0)

    quality_threshold  = int(os.getenv('DRAFT_QUALITY_THRESHOLD',  '8'))
    max_refinements    = int(os.getenv('MAX_DRAFT_REFINEMENTS',     '3'))

    subject        = drafted_email.get('subject', '')
    body           = drafted_email.get('body', '')
    candidate_name = screening.get('candidateName', 'Unknown')
    is_invitation  = screening.get('matchScore', 0) >= 70

    if not subject or not body:
        return {
            'draft_quality_score': 10,
            'quality_feedback':    'No draft to evaluate.',
            'refinement_count':    current_count,
        }

    llm = ChatGroq(
        model='llama-3.1-8b-instant',
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.1,
    )

    email_type = 'invitation' if is_invitation else 'rejection'

    prompt = f"""You are a hiring communications expert. Critically evaluate this {email_type} email.

Candidate: {candidate_name}
Subject: {subject}

Body:
{body[:1500]}

Score each dimension strictly (1 = terrible, 10 = perfect):
1. Tone — professional, warm, appropriate for a {email_type} email
2. Personalisation — uses candidate's name, references their specific skills/background
3. Clarity — clear next steps, no ambiguity, concise (under 250 words)

A score of 8+ on ALL dimensions means it is ready to send.
Be strict: generic templates should score 5-6, not 8+.

Return ONLY a valid JSON object. No markdown, no commentary.
{{
  "tone_score": 7,
  "personalisation_score": 5,
  "clarity_score": 8,
  "overall_score": 6,
  "issues": ["Too generic — no reference to candidate's specific skills", "Opening line is clichéd"],
  "improvement_instructions": "Specific rewrite instructions: reference the candidate's Python and AWS experience in the second paragraph. Remove the clichéd opening. Add a specific interview time slot."
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

        quality = json.loads(raw)

        overall_score = int(quality.get('overall_score', 8))
        feedback      = quality.get('improvement_instructions', '')
        issues        = quality.get('issues', [])
        new_count     = current_count + 1

        print(f"---QUALITY CHECK #{new_count}: overall={overall_score}/10 "
              f"(tone={quality.get('tone_score')}, "
              f"personal={quality.get('personalisation_score')}, "
              f"clarity={quality.get('clarity_score')}) "
              f"for {candidate_name}---")
        if issues:
            print(f"   Issues: {'; '.join(issues)}")

        result = {
            'draft_quality_score': overall_score,
            'quality_feedback':    feedback,
            'refinement_count':    new_count,
        }

        # If below threshold and retries remain, inject feedback as
        # refinement_instructions so the drafter nodes pick it up on re-run.
        if overall_score < quality_threshold and new_count < max_refinements:
            print(f"---QUALITY: score {overall_score} < {quality_threshold}, "
                  f"triggering auto-refinement #{new_count}---")
            result['refinement_instructions'] = (
                f"[Auto-refinement {new_count}/{max_refinements}] "
                f"Quality score: {overall_score}/10. "
                f"Issues: {'; '.join(issues)}. "
                f"Fix: {feedback}"
            )

        return result

    except Exception as e:
        print(f"---QUALITY CHECK ERROR (non-fatal): {e}---")
        # Pass silently — don't stall the pipeline over a quality check failure
        return {
            'draft_quality_score': 10,
            'quality_feedback':    f'Quality check skipped: {e}',
            'refinement_count':    current_count + 1,
        }
