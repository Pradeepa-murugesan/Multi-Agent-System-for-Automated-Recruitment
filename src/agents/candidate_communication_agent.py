from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from src.utils.helpers import clean_and_parse_json # We'll use our helper again!

def draft_email_node(state):
    """
    This agent node drafts a professional interview invitation email and returns
    it as a structured JSON object with separate 'subject' and 'body' fields.
    """
    print("---NODE: DRAFTING STRUCTURED CANDIDATE EMAIL---")

    screening_results = state.get("screening_results", {})
    job_description = state.get("job_description", "")
    
    candidate_name = screening_results.get("candidateName", "Candidate")
    summary = screening_results.get("summary", "No summary available.")

    instructions = state.get("refinement_instructions", "")
    current_draft = state.get("drafted_email", {})

    llm = ChatGroq(model_name="llama-3.3-70b-versatile", temperature=0.7)

    prompt_template = """
    You are a senior recruitment coordinator. Your task is to {action_type} a professional interview invitation email.

    {refinement_context}

    **CORE OBJECTIVE:**
    Generate a high-quality, professional email invitation. 
    1. Identify the **Job Title** and **Company Name** from the JD.
    2. Reference the candidate's background: "{summary}"
    3. State clearly that this is an invitation for a 30-45 minute interview.
    4. Sign off as "The [Extracted Company Name] Hiring Team".

    **CRITICAL OUTPUT FORMAT:**
    - Return ONLY a valid JSON object.
    - Keys: "subject" and "body".
    - "body" should be professionally formatted HTML.

    **Job Description context:**
    {job_description}

    **JSON Output:**
    """

    action_type = "generate"
    refinement_context = ""
    if instructions:
        action_type = "refine and update"
        refinement_context = f"""
    ### IMPORTANT: REFINEMENT Feedback ###
    The user is NOT happy with the previous draft and wants the following changes:
    "{instructions}"

    PROCEED BY:
    - Reviewing the previous draft:
      - Subject: {current_draft.get('subject')}
      - Body: {current_draft.get('body', '').replace('{', '{{').replace('}', '}}')}
    - Applying the requested changes while maintaining a professional tone.
    - Your new version MUST incorporate the feedback above.
    """

    prompt = ChatPromptTemplate.from_template(prompt_template)
    chain = prompt | llm

    response = chain.invoke({
        "action_type": action_type,
        "candidate_name": candidate_name,
        "summary": summary,
        "job_description": job_description,
        "refinement_context": refinement_context
    })

    email_data = clean_and_parse_json(response.content)

    if not email_data or "subject" not in email_data or "body" not in email_data:
        print("ERROR: AI failed to generate valid email JSON. Using fallback.")
        return {"drafted_email": {"subject": "Update on your application", "body": "There was an error generating the email content."}}

    return {"drafted_email": email_data}

