import os
import re
import json
import requests


def _extract_github_username(text: str) -> str | None:
    """Extracts GitHub username from resume text via URL pattern."""
    patterns = [
        r'github\.com/([a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38})',
        r'github:\s*([a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38})',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            username = m.group(1).rstrip('/')
            # Filter obvious non-usernames
            if username.lower() not in ('login', 'users', 'orgs', 'repos', 'blob', 'tree'):
                return username
    return None


def _github_headers() -> dict:
    token = os.getenv('GITHUB_TOKEN', '')
    h = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    return h


def _score_github(profile: dict, repos: list) -> dict:
    """Compute a github_score (0-100) from profile + repo data."""
    score = 0

    # Profile completeness (up to 20)
    for field in ['bio', 'company', 'blog', 'location']:
        if profile.get(field):
            score += 5

    # Public repos volume (up to 15)
    pub_repos = profile.get('public_repos', 0)
    score += min(pub_repos, 15)

    # Followers signal (up to 10)
    followers = profile.get('followers', 0)
    score += min(followers // 5, 10)

    # Repo quality (up to 35)
    if repos:
        total_stars = sum(r.get('stargazers_count', 0) for r in repos)
        top_stars   = max((r.get('stargazers_count', 0) for r in repos), default=0)
        score += min(total_stars // 2, 15)
        score += min(top_stars * 2, 10)

        # README presence (1 point per repo with description, up to 10)
        described = sum(1 for r in repos if r.get('description'))
        score += min(described, 10)

    # Language diversity (up to 10)
    languages = set(r.get('language') for r in repos if r.get('language'))
    score += min(len(languages) * 2, 10)

    # Recent activity (up to 10)
    if repos:
        from datetime import datetime, timezone
        latest = max(
            (r.get('pushed_at') or r.get('updated_at') or '' for r in repos),
            default=''
        )
        if latest:
            try:
                dt     = datetime.fromisoformat(latest.replace('Z', '+00:00'))
                months_ago = (datetime.now(timezone.utc) - dt).days / 30
                if months_ago < 3:
                    score += 10
                elif months_ago < 12:
                    score += 5
            except Exception:
                pass

    return min(score, 100)


def github_analysis_node(state: dict) -> dict:
    """
    Extracts GitHub profile from the resume and fetches public API data.
    Produces github_analysis dict with profile summary and scores.
    Only runs when ENABLE_GITHUB_ANALYSIS=true. Non-fatal.
    """
    if os.getenv('ENABLE_GITHUB_ANALYSIS', 'false').lower() != 'true':
        return {'github_analysis': {}}

    resume_text    = state.get('resume_content', '')
    candidate_name = state.get('screening_results', {}).get('candidateName', 'Unknown')

    username = _extract_github_username(resume_text)
    if not username:
        print(f"---GITHUB: No GitHub URL found for {candidate_name}---")
        return {'github_analysis': {'found': False}}

    print(f"---GITHUB: Fetching profile for @{username} ({candidate_name})---")

    timeout = 8
    headers = _github_headers()

    try:
        profile_resp = requests.get(
            f'https://api.github.com/users/{username}',
            headers=headers, timeout=timeout
        )
        if profile_resp.status_code == 404:
            return {'github_analysis': {'found': False, 'username': username, 'error': 'User not found'}}
        if profile_resp.status_code == 403:
            print(f"---GITHUB: Rate limited — set GITHUB_TOKEN to increase quota---")
            return {'github_analysis': {'found': False, 'error': 'rate_limited'}}
        profile_resp.raise_for_status()
        profile = profile_resp.json()

        repos_resp = requests.get(
            f'https://api.github.com/users/{username}/repos?sort=updated&per_page=15',
            headers=headers, timeout=timeout
        )
        repos_resp.raise_for_status()
        repos = repos_resp.json()

        languages  = list(set(r.get('language') for r in repos if r.get('language')))
        top_repos  = sorted(repos, key=lambda r: r.get('stargazers_count', 0), reverse=True)[:5]
        total_stars = sum(r.get('stargazers_count', 0) for r in repos)

        github_score = _score_github(profile, repos)

        analysis = {
            'found':          True,
            'username':       username,
            'github_url':     f'https://github.com/{username}',
            'github_score':   github_score,
            'public_repos':   profile.get('public_repos', 0),
            'followers':      profile.get('followers', 0),
            'total_stars':    total_stars,
            'languages':      languages[:8],
            'bio':            profile.get('bio') or '',
            'company':        profile.get('company') or '',
            'top_repos': [{
                'name':        r.get('name'),
                'description': r.get('description') or '',
                'stars':       r.get('stargazers_count', 0),
                'language':    r.get('language') or 'Unknown',
                'url':         r.get('html_url'),
            } for r in top_repos],
        }

        print(f"---GITHUB: @{username} score={github_score}/100, "
              f"repos={profile.get('public_repos')}, stars={total_stars}, "
              f"langs={len(languages)}---")

        return {'github_analysis': analysis}

    except requests.Timeout:
        print(f"---GITHUB: Timeout fetching @{username}---")
        return {'github_analysis': {'found': False, 'username': username, 'error': 'timeout'}}
    except Exception as e:
        print(f"---GITHUB ERROR (non-fatal): {e}---")
        return {'github_analysis': {'found': False, 'error': str(e)}}
