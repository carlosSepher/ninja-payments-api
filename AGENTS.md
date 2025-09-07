# AGENTS.md — ninja-payments-api

## Stack
- Python 3.11
- FastAPI, Uvicorn
- httpx, pydantic-settings, python-dotenv
- transbank-sdk
- pytest, ruff, mypy

## Setup
Run these commands to set up the environment:
```bash
python -m venv .venv
source .venv/bin/activate || .\.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
