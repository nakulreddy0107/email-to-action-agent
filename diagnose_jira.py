"""Jira API diagnostic — run from the project root:

    python diagnose_jira.py

Runs three independent checks:
  1. Can we authenticate at all? (POST /myself)
  2. Can we see the project? (POST /project/{KEY})
  3. Do we have permission to create issues in that project? (POST /mypermissions)

Each check tells us something different about what's wrong.
"""
from __future__ import annotations

import sys
import base64
import requests

sys.path.insert(0, ".")
from core.config import settings


def main() -> None:
    base = settings.jira_base_url.rstrip("/")
    email = settings.jira_email
    token = settings.jira_api_token
    project = settings.jira_default_project_key

    print("=" * 60)
    print("Jira Diagnostic")
    print("=" * 60)
    print(f"Base URL:      {base}")
    print(f"Email:         {email}")
    print(f"Token:         {token[:8]}...{token[-4:] if len(token) > 12 else '(too short)'}  (length: {len(token)})")
    print(f"Project key:   {project}")
    print()

    if not all([base, email, token]):
        print("❌ One or more required env vars is empty. Check your .env file.")
        return

    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
    }

    # ---------- 1. Can we auth at all? ----------
    print("--- 1. Auth check: GET /rest/api/3/myself ---")
    r = requests.get(f"{base}/rest/api/3/myself", headers=headers, timeout=10)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        me = r.json()
        print(f"✅ Authenticated as: {me.get('displayName')} ({me.get('emailAddress')})")
        print(f"   accountId: {me.get('accountId')}")
        print(f"   accountType: {me.get('accountType')}")
    elif r.status_code == 401:
        print("❌ Token is invalid or expired, OR email/token combination is wrong.")
        print("   → Double-check JIRA_EMAIL matches the account that created the token.")
        print("   → Double-check JIRA_API_TOKEN has no stray spaces or line breaks.")
        return
    else:
        print(f"❌ Unexpected: {r.text[:300]}")
        return
    print()

    # ---------- 2. Can we see the project? ----------
    print(f"--- 2. Project check: GET /rest/api/3/project/{project} ---")
    r = requests.get(f"{base}/rest/api/3/project/{project}", headers=headers, timeout=10)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        p = r.json()
        print(f"✅ Project found: '{p.get('name')}' (key: {p.get('key')})")
        print(f"   Style: {p.get('style')}   (team-managed if 'next-gen', company-managed otherwise)")
    elif r.status_code == 404:
        print(f"❌ Project '{project}' does not exist for this account.")
        print("   → Check the exact project key in Jira → Projects list.")
        # Also list what projects they CAN see
        r2 = requests.get(f"{base}/rest/api/3/project/search", headers=headers, timeout=10)
        if r2.status_code == 200:
            projs = r2.json().get("values", [])
            print(f"   Projects visible to this token: {[p['key'] for p in projs]}")
        return
    else:
        print(f"❌ Unexpected: {r.text[:300]}")
        return
    print()

    # ---------- 3. Create-issue permission ----------
    print(f"--- 3. Permission check: GET /rest/api/3/mypermissions?projectKey={project}&permissions=CREATE_ISSUES ---")
    r = requests.get(
        f"{base}/rest/api/3/mypermissions",
        params={"projectKey": project, "permissions": "CREATE_ISSUES"},
        headers=headers,
        timeout=10,
    )
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        perms = r.json().get("permissions", {})
        create = perms.get("CREATE_ISSUES", {})
        has_perm = create.get("havePermission", False)
        if has_perm:
            print(f"✅ Account HAS permission to create issues in '{project}'.")
            print(f"   → If creates are still 401-ing, the issue is in the payload fields.")
        else:
            print(f"❌ Account does NOT have CREATE_ISSUES permission in '{project}'.")
            print(f"   → Go to Jira → Project settings → Access → add your user as a member/admin.")
    else:
        print(f"❌ Could not check permissions: {r.text[:300]}")
    print()

    # ---------- 4. Try the actual create ----------
    print("--- 4. Minimal create-issue attempt ---")
    payload = {
        "fields": {
            "project": {"key": project},
            "summary": "Diagnostic test — safe to delete",
            "issuetype": {"name": "Task"},
        }
    }
    headers["Content-Type"] = "application/json"
    r = requests.post(f"{base}/rest/api/3/issue", headers=headers, json=payload, timeout=15)
    print(f"Status: {r.status_code}")
    if r.status_code in (200, 201):
        data = r.json()
        print(f"✅ Issue created: {data.get('key')}")
        print(f"   URL: {base}/browse/{data.get('key')}")
        print(f"   → You can safely delete it in Jira.")
    else:
        try:
            err = r.json()
            print(f"❌ Create failed: {err}")
        except Exception:
            print(f"❌ Create failed: {r.text[:500]}")


if __name__ == "__main__":
    main()