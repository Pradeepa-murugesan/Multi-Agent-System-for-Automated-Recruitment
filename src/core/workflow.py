from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated, List, Dict
import operator

from src.agents.resume_screening_agent import screen_resume_node
from src.agents.candidate_communication_agent import draft_email_node
from src.agents.email_sending_agent import send_email_node

from src.agents.rejection_email_agent import draft_rejection_node

class AgentState(TypedDict):
    job_description: str
    resume_content: str
    screening_results: dict
    drafted_email: Dict[str, str] 
    final_status: str
    refinement_instructions: str
    messages: Annotated[List[str], operator.add]

workflow = StateGraph(AgentState)

workflow.add_node("resume_screener", screen_resume_node)
workflow.add_node("invitation_drafter", draft_email_node) 
workflow.add_node("rejection_drafter", draft_rejection_node) 
workflow.add_node("email_sender", send_email_node)

workflow.set_entry_point("resume_screener")

def route_after_screening(state):
    """
    This router now has three possible paths based on the screening results:
    1. No Email Found -> END
    2. Good Score -> Go to 'invitation_drafter'
    3. Bad Score -> Go to 'rejection_drafter'
    """
    screening_results = state.get("screening_results", {})
    match_score = screening_results.get("matchScore", 0)
    candidate_email = screening_results.get("candidateEmail", "N/A")

    print(f"---DECISION: Match Score is {match_score}% for {candidate_email}---")

    if not candidate_email or candidate_email == "N/A":
        print("---DECISION: No email found. Ending workflow.---")
        return END

    if match_score >= 70:
        print(f"---DECISION: Score is sufficient. Proceeding to draft INVITATION.---")
        return "invitation_drafter"
    else:
        print(f"---DECISION: Score is too low. Proceeding to draft REJECTION.---")
        return "rejection_drafter"

workflow.add_conditional_edges(
    "resume_screener",
    route_after_screening,
    {
        "invitation_drafter": "invitation_drafter",
        "rejection_drafter": "rejection_drafter",
        END: END
    }
)

workflow.add_edge('invitation_drafter', 'email_sender')
workflow.add_edge('rejection_drafter', 'email_sender')

workflow.add_edge('email_sender', END)
from langgraph.checkpoint.memory import MemorySaver

memory = MemorySaver()
app = workflow.compile(checkpointer=memory, interrupt_before=["email_sender"])

