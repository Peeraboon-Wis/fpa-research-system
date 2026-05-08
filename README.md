# FP&A Multi-Agent Research System

## What it does

An AI-powered research pipeline for FP&A teams that orchestrates four specialist agents (Scout, Architect, Analyst, Visual) to produce market benchmarks, structural analysis, comparison tables, and CFO-ready slide deck outlines. Each run saves structured outputs directly to a Google Doc and Google Sheet, with full session memory persisted in a dedicated Google Sheets tab so every follow-up run builds on prior findings.

## Required Secrets (Streamlit Cloud)

Add the following to your app's **Secrets** panel in Streamlit Cloud (or to `.streamlit/secrets.toml` locally):

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
MEMORY_SHEET_ID   = "<Google Sheet ID used for task memory>"

[gcp_service_account]
type                        = "service_account"
project_id                  = "..."
private_key_id              = "..."
private_key                 = "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n"
client_email                = "...@....iam.gserviceaccount.com"
client_id                   = "..."
auth_uri                    = "https://accounts.google.com/o/oauth2/auth"
token_uri                   = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url        = "..."
universe_domain             = "googleapis.com"
```

## Google Sheet Setup

1. Create a new Google Sheet (or reuse an existing one).
2. Copy the Sheet ID from its URL (`https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`) and paste it as `MEMORY_SHEET_ID` in your secrets.
3. Share the sheet with your service account email (`client_email` value above) and grant **Editor** access.
4. The app will automatically create a `TaskMemory` tab with the correct headers on first run.

## Local Development

```bash
# Activate the virtual environment
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Fill in .streamlit/secrets.toml with real values, then run:
streamlit run app.py
```
