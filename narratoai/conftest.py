"""Pytest collection rules for the repository.

These files are executable smoke-check scripts that live next to the LLM
implementation for convenience. They require live credentials or manual
execution semantics, so keep them out of the default automated test suite.
"""

collect_ignore = [
    "app/services/llm/test_llm_service.py",
    "app/services/llm/test_openai_compatible_integration.py",
]
