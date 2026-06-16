import json
import re
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from src.utils.helpers import clean_and_parse_json

def screen_resume_node(state) -> dict:
    print("---NODE: SCREENING RESUME (STRICT JSON MODE)---")

    llm = ChatGroq(model_name="llama-3.3-70b-versatile", temperature=0)
    
    resume_text = state["resume_content"]
    job_description_text = state["job_description"]

    prompt_template = """
    You are an expert AI recruitment assistant. Your task is to analyze the provided Resume against the Job Description and return a structured JSON object.

    **CRITICAL INSTRUCTIONS:**
    1.  **Analyze Content:** Extract the candidate's full name, their email address, calculate a "matchScore" (0-100), and write a 2-3 sentence "summary".
    2.  **JSON FORMATTING IS MANDATORY:** You MUST return ONLY a single, valid JSON object.
    3.  **USE DOUBLE QUOTES:** All keys and all string values in the JSON object MUST be enclosed in double quotes (").

    **EXAMPLE OF A PERFECT OUTPUT:**
    {{
        "candidateName": "Sanjay Kumar",
        "candidateEmail": "sanjay.k@example.com",
        "matchScore": 85,
        "summary": "Sanjay Kumar is a strong candidate with 5 years of Python experience, aligning well with the job requirements. His skills in AWS and Django are particularly relevant."
    }}

    **Job Description:**
    {job_description}

    **Full Resume Text:**
    {resume}

    **JSON Output (Must use double quotes):**
    """
    prompt = ChatPromptTemplate.from_template(prompt_template)
    chain = prompt | llm

    response = chain.invoke({
        "job_description": job_description_text,
        "resume": resume_text
    })

    results = clean_and_parse_json(response.content)

    if results and (not results.get("candidateEmail") or results.get("candidateEmail") == "N/A"):
        print("---AI failed to find email. Trying Regex fallback.---")
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        match = re.search(email_pattern, resume_text)
        if match:
            found_email = match.group(0)
            print(f"---Regex found an email: {found_email}---")
            results["candidateEmail"] = found_email

    if not results:
        print("ERROR: AI failed to produce valid JSON.")
        results = { "candidateName": "Error", "candidateEmail": "N/A", "matchScore": 0, "summary": "Critical error: The AI model failed to generate valid structured data." }
        
    return {"screening_results": results}

