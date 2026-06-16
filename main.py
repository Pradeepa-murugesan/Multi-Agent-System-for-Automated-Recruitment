import os
import sys
import json
import uuid
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.pdf_parser import parse_pdf_from_path
from src.core.workflow import app as recruitment_app
from src.agents.job_posting_agent import generate_jd_from_notes
from src.agents.rejection_email_agent import draft_rejection_node 

load_dotenv()
app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate_jd', methods=['POST'])
def generate_jd():
    data = request.get_json()
    notes = data.get('notes')
    if not notes:
        return jsonify({"error": "No notes were provided."}), 400
    try:
        generated_jd = generate_jd_from_notes(notes)
        return jsonify({"job_description": generated_jd})
    except Exception as e:
        print(f"Error during JD generation: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/process', methods=['POST'])
def process():
    """
    Processes resumes and interrupts before sending emails to allow for review.
    """
    job_description_text = request.form.get('job_description_text')
    resume_files = request.files.getlist('resumes')

    if not job_description_text or not resume_files:
        return jsonify({"error": "Missing job description or resumes."}), 400

    resume_files.sort(key=lambda x: x.filename)
    
    all_results = []
    for resume_file in resume_files:
        if not resume_file.filename:
            continue

        thread_id = str(uuid.uuid4())
        resume_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{thread_id}_{resume_file.filename}")
        resume_file.save(resume_path)
        resume_text = parse_pdf_from_path(resume_path)
        
        initial_state = {
            "job_description": job_description_text,
            "resume_content": resume_text,
            "refinement_instructions": ""
        }
        
        config = {"configurable": {"thread_id": thread_id}}
        
        # This will run until the interrupt_before=['email_sender']
        recruitment_app.invoke(initial_state, config=config)
        
        # Get the state after interrupt
        state_snapshot = recruitment_app.get_state(config)
        
        result_for_frontend = {
            "filename": resume_file.filename,
            "thread_id": thread_id,
            "state": state_snapshot.values,
            "next_step": list(state_snapshot.next)
        }
        all_results.append(result_for_frontend)
        os.remove(resume_path)

    return jsonify(all_results)

@app.route('/refine_email', methods=['POST'])
def refine_email():
    data = request.get_json()
    thread_id = data.get('thread_id')
    instructions = data.get('instructions')
    
    if not thread_id or not instructions:
        return jsonify({"error": "Missing thread_id or instructions."}), 400
        
    config = {"configurable": {"thread_id": thread_id}}
    
    try:
        print(f"---REFINEMENT START: {thread_id}---")
        print(f"Instructions: {instructions}")

        # 1. Update state with instructions
        recruitment_app.update_state(config, {"refinement_instructions": instructions})
        
        # 2. Get current state
        state = recruitment_app.get_state(config).values
        print(f"Pre-refinement draft: {state.get('drafted_email', {}).get('subject')}")

        # 3. Re-run the relevant drafter node
        match_score = state.get("screening_results", {}).get("matchScore", 0)
        if match_score >= 70:
            from src.agents.candidate_communication_agent import draft_email_node
            new_state_part = draft_email_node(state)
        else:
            from src.agents.rejection_email_agent import draft_rejection_node
            new_state_part = draft_rejection_node(state)
            
        print(f"Post-refinement draft: {new_state_part.get('drafted_email', {}).get('subject')}")

        # 4. Update the state with the new draft
        recruitment_app.update_state(config, new_state_part)
        
        # 5. Clear instructions
        recruitment_app.update_state(config, {"refinement_instructions": ""})
        
        print(f"---REFINEMENT COMPLETE: {thread_id}---")

        return jsonify({
            "status": "refined",
            "new_state": recruitment_app.get_state(config).values
        })
    except Exception as e:
        print(f"Error during refinement: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/send_email', methods=['POST'])
def send_email():
    data = request.get_json()
    thread_id = data.get('thread_id')
    
    if not thread_id:
        return jsonify({"error": "Missing thread_id."}), 400
        
    config = {"configurable": {"thread_id": thread_id}}
    
    try:
        # Resume the graph (it will run the interrupted node 'email_sender')
        recruitment_app.invoke(None, config=config)
        
        final_state = recruitment_app.get_state(config).values
        
        return jsonify({
            "status": "sent",
            "final_status": final_state.get("final_status", "Sent")
        })
    except Exception as e:
        print(f"Error sending email: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(port=5001, debug=True)

