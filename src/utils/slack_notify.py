import os
import json
import urllib.request
import urllib.error

def notify_slack(candidate_name, score, job_title, email):
    webhook_url = os.getenv('SLACK_WEBHOOK_URL', '').strip()
    if not webhook_url:
        return

    color = '#059669' if score >= 85 else '#4f46e5'
    message = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🎯 High-Score Candidate Alert"}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Candidate:*\n{candidate_name}"},
                    {"type": "mrkdwn", "text": f"*Score:*\n`{score}/100`"},
                    {"type": "mrkdwn", "text": f"*Role:*\n{job_title}"},
                    {"type": "mrkdwn", "text": f"*Email:*\n{email}"}
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Review and approve their invitation email in the *AI Recruitment Assistant*."
                }
            },
            {"type": "divider"}
        ]
    }

    try:
        data = json.dumps(message).encode('utf-8')
        req = urllib.request.Request(
            webhook_url, data=data,
            headers={'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[Slack] Notification failed: {e}")
