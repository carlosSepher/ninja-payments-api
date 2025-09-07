# AGENTS.md — ninja-payments-api

## Setup
```bash
python -m venv .venv
source .venv/bin/activate || .\.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
cp -n .env.example .env 2>/dev/null || copy .env.example .env
