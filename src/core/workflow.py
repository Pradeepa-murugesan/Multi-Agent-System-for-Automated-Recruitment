import os
import sqlite3
import operator
from typing import TypedDict, Annotated, List, Dict, Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from src.agents.resume_screening_agent        import screen_resume_node
from src.agents.skills_intelligence_agent     import skills_intelligence_node
from src.agents.bias_detection_agent          import bias_detection_node
from src.agents.career_trajectory_agent       import career_trajectory_node
from src.agents.github_analysis_agent         import github_analysis_node
from src.agents.candidate_communication_agent import draft_email_node
from src.agents.rejection_email_agent         import draft_rejection_node
from src.agents.email_quality_agent           import draft_quality_checker_node
from src.agents.interview_question_agent      import interview_question_node
from src.agents.email_sending_agent           import send_email_node

# ─── Config ───────────────────────────────────────────────────────────────────
QUALITY_THRESHOLD = int(os.getenv('DRAFT_QUALITY_THRESHOLD', '8'))
MAX_REFINEMENTS   = int(os.getenv('MAX_DRAFT_REFINEMENTS',   '3'))

CHECKPOINT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'checkpoints.db'
)

# ─── State schema ─────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Core pipeline
    job_description:          str
    resume_content:           str
    refinement_instructions:  str
    # Screening
    screening_results:        dict
    score_variance:           int    # P3: multi-model consensus variance
    # P1 — Deep skills
    skills_matrix:            dict
    # P2 — Bias audit
    bias_score:               int
    bias_flags:               List[str]
    # P3 — Career trajectory
    career_analysis:          dict
    # P4 — GitHub analysis
    github_analysis:          dict
    # Email draft
    drafted_email:            Dict[str, str]
    final_status:             str
    # P1 — Quality loop
    refinement_count:         int
    draft_quality_score:      int
    quality_feedback:         str
    # P2 — Interview prep
    interview_questions:      List[dict]
    # Message history
    messages:                 Annotated[List[str], operator.add]

# ─── Graph ────────────────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

workflow.add_node("resume_screener",       screen_resume_node)
workflow.add_node("skills_intelligence",   skills_intelligence_node)
workflow.add_node("bias_detector",         bias_detection_node)
workflow.add_node("career_trajectory",     career_trajectory_node)
workflow.add_node("github_analysis",       github_analysis_node)
workflow.add_node("invitation_drafter",    draft_email_node)
workflow.add_node("rejection_drafter",     draft_rejection_node)
workflow.add_node("draft_quality_checker", draft_quality_checker_node)
workflow.add_node("interview_prep",        interview_question_node)
workflow.add_node("email_sender",          send_email_node)

workflow.set_entry_point("resume_screener")

# ─── Routing: after full screening pipeline ───────────────────────────────────

def route_after_screening_pipeline(state: AgentState):
    screening       = state.get("screening_results", {})
    match_score     = screening.get("matchScore", 0)
    candidate_email = screening.get("candidateEmail", "N/A")

    print(f"---ROUTE: score={match_score}% email={candidate_email}---")

    if not candidate_email or candidate_email == "N/A":
        return END

    return "invitation_drafter" if match_score >= 70 else "rejection_drafter"


# Linear enrichment chain before drafting
workflow.add_edge("resume_screener",     "skills_intelligence")
workflow.add_edge("skills_intelligence", "bias_detector")
workflow.add_edge("bias_detector",       "career_trajectory")
workflow.add_edge("career_trajectory",   "github_analysis")

workflow.add_conditional_edges(
    "github_analysis",
    route_after_screening_pipeline,
    {
        "invitation_drafter": "invitation_drafter",
        "rejection_drafter":  "rejection_drafter",
        END: END,
    },
)

# Both drafters → quality checker
workflow.add_edge("invitation_drafter", "draft_quality_checker")
workflow.add_edge("rejection_drafter",  "draft_quality_checker")

# ─── Routing: self-critique loop ──────────────────────────────────────────────

def route_after_quality_check(state: AgentState):
    score       = state.get("draft_quality_score", QUALITY_THRESHOLD)
    count       = state.get("refinement_count", 0)
    match_score = state.get("screening_results", {}).get("matchScore", 0)

    if score >= QUALITY_THRESHOLD or count >= MAX_REFINEMENTS:
        status = "passed" if score >= QUALITY_THRESHOLD else f"max-retries({count})"
        if match_score >= 70:
            print(f"---QUALITY ROUTE: {status} → interview_prep---")
            return "interview_prep"
        print(f"---QUALITY ROUTE: {status} → email_sender---")
        return "email_sender"

    target = "invitation_drafter" if match_score >= 70 else "rejection_drafter"
    print(f"---QUALITY ROUTE: score={score}<{QUALITY_THRESHOLD}, retry #{count} → {target}---")
    return target


workflow.add_conditional_edges(
    "draft_quality_checker",
    route_after_quality_check,
    {
        "invitation_drafter": "invitation_drafter",
        "rejection_drafter":  "rejection_drafter",
        "interview_prep":     "interview_prep",
        "email_sender":       "email_sender",
    },
)

workflow.add_edge("interview_prep", "email_sender")
workflow.add_edge("email_sender",   END)

# ─── Compile ──────────────────────────────────────────────────────────────────

_ckpt_conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
_ckpt_conn.execute("PRAGMA journal_mode=WAL")
_ckpt_conn.execute("PRAGMA synchronous=NORMAL")
_ckpt_conn.commit()

memory = SqliteSaver(_ckpt_conn)
app = workflow.compile(checkpointer=memory, interrupt_before=["email_sender"])
