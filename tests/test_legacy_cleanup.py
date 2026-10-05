"""TICKET-012 AC3 — the legacy in-pipeline LLM path is gone.

``llm.summarize`` (and its helper ``parse_llm_response``) are removed, and the
pipeline carries no LLM parameter or branch: import enrichment is
deterministic by contract, AI runs only in stage 2 (``postprocess.enrich``).
"""

import inspect

from obsidianizer import llm as llm_mod
from obsidianizer import pipeline as pipeline_mod
from obsidianizer.llm import LLMClient


def test_no_summarize_on_client():
    assert not hasattr(LLMClient, "summarize")
    assert not hasattr(llm_mod, "parse_llm_response")


def test_pipeline_has_no_llm_parameter_or_branch():
    assert "llm" not in inspect.signature(pipeline_mod.run).parameters
    assert "llm" not in inspect.signature(pipeline_mod._process_one).parameters
    assert "summarize" not in inspect.getsource(pipeline_mod)
