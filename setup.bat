@echo off
echo === Installing s2-autonomous-engineering ===
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
echo Done.
