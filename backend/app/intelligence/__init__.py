"""Threat indicator lists: downloaded snapshots of public sanctions and ransomware address lists.

* sources.py: which lists we download, from where, and how each file is checked.
* snapshots.py: download, validate and save a snapshot with its provenance (refresh once a day).
* index.py: the loaded lists, for exact address matching.
* screening.py: turns matches into investigation indicators, findings and evidence.

Refresh by hand from backend/:  ..\\.venv\\Scripts\\python.exe -m app.intelligence refresh
"""
