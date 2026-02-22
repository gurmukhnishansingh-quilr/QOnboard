# QOnboard

> Quilr customer onboarding agent — fetches a Jira ticket, extracts user details with an LLM, and wires everything up across the Onboard API, PostgreSQL, and Neo4j in one guided run.

---

## How it works

```
Jira ticket (Customer Onboard)
        │
        ▼
  LLM extracts users          ← Azure OpenAI function calling
  (firstname, lastname, email)
        │
        ├─► [Step 1] POST /bff/auth/auth/onboard   (per new user, skips existing)
        │
        ├─► [Step 2] PostgreSQL SELECT tenant
        │
        ├─► [Step 3] PostgreSQL UPDATE tenant + subscriber
        │
        └─► [Step 4] Neo4j MERGE TENANT node
                │
                └─► Jira comment + transition to Tenant Ready
```

Each step shows a **syntax-highlighted preview** and asks **Y / N** before executing.
Progress is saved after every step — if the agent is interrupted, restarting it **resumes from where it left off**.

---

## Requirements

- Python **3.11+** (tested on 3.13)
- SSH access to `github.com:gurmukhnishansingh-quilr/QOnboard.git`
- Credentials for: Jira Cloud, Azure OpenAI, PostgreSQL, Neo4j (per environment)

---

## Setup

### 1. Clone and install

```bash
git clone git@github.com:gurmukhnishansingh-quilr/QOnboard.git
cd QOnboard
pip install -r requirements.txt
```

### 2. Configure the main `.env`

Copy the example and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `JIRA_URL` | ✅ | `https://your-org.atlassian.net` |
| `JIRA_USERNAME` | ✅ | Your Atlassian account email |
| `JIRA_API_TOKEN` | ✅ | [Create one here](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `JIRA_ISSUE_TYPE` | | Default: `Customer Onboard` |
| `JIRA_PENDING_STATUS` | | Default: `To Do` — JQL filter for open tickets |
| `JIRA_IN_PROGRESS_STATUS` | | Default: `New Tenant` — transition fired at start |
| `JIRA_DONE_STATUS` | | Default: `Tenant Ready` — transition fired on completion |
| `JIRA_FIELD_ENVIRONMENT` | | Custom field ID for the environment selector (e.g. `customfield_10479`) |
| `AZURE_OPENAI_API_KEY` | ✅ | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | ✅ | `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT` | ✅ | Deployment name (e.g. `gpt-4.1`) |
| `AZURE_OPENAI_API_VERSION` | | Default: `2024-02-01` |
| `ONBOARD_VENDOR` | | Default: `microsoft` |
| `API_TIMEOUT_SECONDS` | | Default: `30` |

> **Finding `JIRA_FIELD_ENVIRONMENT`**: Call `GET /rest/api/3/field` on your Jira instance and search for the field labelled *Environment*.

### 3. Configure per-environment databases

Each Quilr environment has its own PostgreSQL and Neo4j. Copy and fill in the example file for each environment you need:

| Jira environment value | File to create | Domains |
|---|---|---|
| `UAE POC` / `UAE PROD` | `.env_uae` | `trust.quilr.ai` |
| `IND POC` | `.env_ind` | `platform.quilr.ai` |
| `IND PROD` | `.env_ind_prod` | `platform.quilrai.com` |
| `USA POC` | `.env_us` | `app.quilr.ai` |
| `USA PROD` | `.env_us_prod` | `app.quilrai.com` |

```bash
cp envs/.env.uae-poc.example .env_uae
# edit .env_uae with your values
```

Each env file needs:

```ini
# PostgreSQL
PG_HOST=your-pg-host
PG_PORT=5432
PG_DBNAME=quilr_auth        # default, override if different
PG_USER=your-pg-user
PG_PASSWORD=your-pg-password
PG_SSLMODE=require           # default

# Neo4j
NEO4J_HOST=your-neo4j-host
NEO4J_PORT=7687              # default (bolt)
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-neo4j-password
NEO4J_DATABASE=neo4j         # default
```

> `.env_*` files are **gitignored** — credentials never leave your machine.

---

## Usage

### Process a specific ticket

```bash
python agent.py OPS-123
```

### Prompt for ticket ID interactively

```bash
python agent.py
# Enter Jira ticket ID (or press Enter for all open tickets):
```

### Process all open tickets

```bash
python agent.py
# (press Enter at the prompt)
```

---

## What happens during a run

```
╭─────────────────── Customer Onboarding ───────────────────╮
│ Ticket   PMM-4916 — Onboard Acme Corp                     │
│ Env      UAE POC                                          │
│ Users    User 1   Alice Smith    alice@acme.com           │
│          User 2   Bob Jones      bob@acme.com             │
╰───────────────────────────────────────────────────────────╯

╭── STEP 1/4 — Onboard API (2 new user(s)) ─────────────────╮
│  POST  https://trust.quilr.ai/bff/auth/auth/onboard       │
│                                                           │
│  [1]  alice@acme.com   Alice Smith                        │
│  [2]  bob@acme.com     Bob Jones                          │
╰───────────────────────────────────────────────────────────╯
  Proceed? [y/n]
```

- **Y** — executes the step and moves to the next
- **N** — skips the step, adds a pause comment to Jira, stops the ticket

On completion, a summary comment is posted to the Jira ticket and it is transitioned to **Tenant Ready**.

---

## Resume after interruption

Progress is saved to `.onboard_state.json` after each step. If the agent crashes or you press Ctrl-C mid-run, simply re-run the same command — already-completed steps are shown as:

```
─────  ✓  Step 1/4 — Onboard API — already completed  ─────
─────  ✓  Step 2/4 — PostgreSQL — Fetch Tenant — already completed  ─────
```

and the agent continues from the first incomplete step.

Once all four steps finish the ticket is marked **completed** in state and skipped on any future run.

---

## Supported environments

| Jira value | Onboard API domain |
|---|---|
| `UAE POC` | `trust.quilr.ai` |
| `UAE PROD` | `trust.quilr.ai` |
| `IND POC` | `platform.quilr.ai` |
| `IND PROD` | `platform.quilrai.com` |
| `USA POC` | `app.quilr.ai` |
| `USA PROD` | `app.quilrai.com` |

---

## Project structure

```
QOnboard/
├── agent.py               # Entry point — orchestrates all steps
├── config.py              # Main .env config (Jira, Azure OpenAI, API)
├── env_config.py          # Per-environment DB config loader
├── state.py               # Step-level progress persistence
├── logger_setup.py        # Rich logging setup
├── requirements.txt
├── .env.example           # Template — copy to .env
├── envs/
│   ├── .env.uae-poc.example
│   ├── .env.ind-poc.example
│   ├── .env.ind-prod.example
│   ├── .env.usa-poc.example
│   └── .env.usa-prod.example
└── clients/
    ├── jira_client.py     # Jira REST API v3 (ADF parsing + ADF comment writing)
    ├── extractor.py       # Azure OpenAI function calling — extracts users from description
    ├── onboard_api.py     # POST /bff/auth/auth/onboard
    ├── postgres_client.py # quilr_auth DB — tenant SELECT + UPDATE, user existence check
    ├── neo4j_client.py    # MERGE TENANT node
    └── env_registry.py    # Lazily wires DB clients per environment
```

---

## Troubleshooting

| Error | Fix |
|---|---|
| `Required environment variable 'X' is missing` | Copy `.env.example` → `.env` and fill in the value |
| `Environment config file not found: '.env_uae'` | Copy `envs/.env.uae-poc.example` → `.env_uae` and fill in DB credentials |
| `Unknown environment 'XYZ'` | The Jira environment field has an unexpected value — check `JIRA_FIELD_ENVIRONMENT` and the field value on the ticket |
| `No tenant found … with name = 'domain.com'` | The tenant row does not exist in PostgreSQL yet — check the `quilr_auth.public.tenant` table |
| `JiraError HTTP 400 — Comment body is not valid` | Jira API v3 requires ADF bodies — already handled; check you are on `jira>=3.8.0` |
| `No transition named 'X' found` | Run the agent with a test ticket and inspect the log for available transitions, then update `JIRA_IN_PROGRESS_STATUS` / `JIRA_DONE_STATUS` in `.env` |
