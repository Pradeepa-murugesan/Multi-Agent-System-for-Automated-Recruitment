from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from src.utils.helpers import clean_and_parse_json

def draft_rejection_node(state):
    """
    This agent node drafts a polite and professional rejection email for
    candidates who did not meet the minimum score.

    Args:
        state (AgentState): The current state of the graph.

    Returns:
        dict: A dictionary containing the structured rejection email
              (subject and body) to be added to the state.
    """
    print("---NODE: DRAFTING REJECTION EMAIL---")

    instructions = state.get("refinement_instructions", "")
    current_draft = state.get("drafted_email", {})

    llm = ChatGroq(model_name="llama-3.3-70b-versatile", temperature=0.6)

    prompt_template = """
    You are a senior recruitment coordinator. Your task is to {action_type} a polite and professional rejection email.

    {refinement_context}

    **CORE OBJECTIVES:**
    1. Analyze the Job Description to identify the **Job Title** and **Company Name**.
    2. Thank the candidate ({candidate_name}) for their interest and time.
    3. State clearly but respectfully that the team is moving forward with other candidates.
    4. Wish them success in their future search.
    5. Sign off as "The [Extracted Company Name] Hiring Team".

    **CRITICAL OUTPUT FORMAT:**
    - Return ONLY a valid JSON object.
    - Keys: "subject" and "body".
    - "body" should be professionally formatted HTML.

    **Job Description Context:**
    {job_description}

    **JSON Output:**
    """

    action_type = "generate"
    refinement_context = ""
    if instructions:
        action_type = "refine"
        refinement_context = f"""
    ### REFINEMENT REQUEST ###
    The user wants to adjust the previous draft:
    "{instructions}"

    **PREVIOUS DRAFT:**
    Subject: {current_draft.get('subject')}
    Body: {current_draft.get('body', '').replace('{', '{{').replace('}', '}}')}

    Please update the email to reflect these specific requests.
    """

    prompt = ChatPromptTemplate.from_template(prompt_template)
    chain = prompt | llm

    response = chain.invoke({
        "action_type": action_type,
        "candidate_name": candidate_name,
        "job_description": job_description,
        "refinement_context": refinement_context
    })

    email_data = clean_and_parse_json(response.content)

    if not email_data or "subject" not in email_data or "body" not in email_data:
        return {"drafted_email": {"subject": "Update on your application", "body": "Thank you for your interest. We have decided to move forward with other candidates at this time."}}

    return {"drafted_email": email_data}
