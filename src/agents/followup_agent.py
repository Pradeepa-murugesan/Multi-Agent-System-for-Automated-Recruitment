import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def generate_followup_email(
    candidate_name: str,
    job_title: str,
    original_subject: str,
    original_body: str,
    days_since_sent: int,
) -> dict:
    """
    Generates a personalised follow-up email for a candidate who hasn't
    responded to an invitation email after `days_since_sent` days.
    Returns {'subject': str, 'body': str} or raises on total failure.
    """
    llm = ChatGroq(
        model='llama-3.1-8b-instant',
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.3,
    )

    prompt = f"""You are a friendly recruiter writing a follow-up email.

CONTEXT:
- Candidate: {candidate_name}
- Role: {job_title}
- Days since initial email: {days_since_sent}
- Original email subject: {original_subject}
- Original email body (first 400 chars): {original_body[:400]}

Write a SHORT, warm follow-up email (under 100 words).
- Friendly, not pushy
- Reference the original invitation naturally
- Ask if they are still interested and if they have questions
- Offer to reschedule or adjust if the proposed timing doesn't work
- Professional closing

Return ONLY valid JSON. No markdown.
{{
  "subject": "Re: {original_subject}",
  "body": "Full email body text here"
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

        data = json.loads(raw)
        return {
            'subject': data.get('subject', f'Re: {original_subject}'),
            'body':    data.get('body', ''),
        }

    except Exception as e:
        # Hard fallback — plain-text follow-up without LLM
        return {
            'subject': f'Following up: {original_subject}',
            'body': (
                f"Hi {candidate_name},\n\n"
                f"I wanted to follow up on the invitation I sent {days_since_sent} days ago "
                f"regarding the {job_title} position.\n\n"
                f"Are you still interested? Please let me know if you have any questions "
                f"or if you'd like to reschedule.\n\n"
                f"Looking forward to hearing from you.\n\nBest regards"
            ),
        }
