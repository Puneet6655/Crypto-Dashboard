"""AI analyst (Milestone 14): a local LLM (Ollama) that explains an investigation result and answers questions.

It only sees the facts of the case it is given, plus what its read-only tools look up, and every answer is
checked for addresses, transaction IDs and amounts that are not in those facts (app/analyst/checks.py).
Nothing is sent to an outside AI service: Ollama runs on this computer.
"""
