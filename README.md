# AI Email-to-Action Automation Agent

A multi-agent system that reads enterprise emails, extracts actionable requests, and triggers actions across business tools such as **Asana/Jira**, **Slack**, and **Google Calendar**.

This project is designed to convert unstructured email requests into structured actions like:
- scheduling meetings
- creating tasks
- sending team notifications
- drafting replies for approval

## Architecture

```text
Raw Email
   ↓
Ingestion / Cleanup
   ↓
LLM Intent Detection
   ↓
Specialist Agents
   ├─ Calendar Agent
   ├─ Task Agent
   ├─ Slack Agent
   └─ Reply Agent
   ↓
Policy / Approval Check
   ↓
Executor
   ↓
External Integrations
   ├─ Google Calendar
   ├─ Asana / Jira
   └─ Slack
```

## Project Flow

1. An email is received by the pipeline.
2. The LLM detects intents and extracts key entities.
3. Specialist agents convert each intent into a structured action.
4. Policy checks decide whether the action can run automatically or needs approval.
5. The executor triggers the appropriate integration.
6. Results are logged for audit and review.

## Run Commands

### 1. Create and activate virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Then update `.env` with your local values.

### 4. Run demo emails

```bash
python3 main.py --demo
```

### 5. Run one email from JSON

```bash
python3 main.py --email data/my_email.json
```

### 6. Start API server

```bash
python3 main.py --serve
```

