"""Módulo 15 — Assistente de IA (RAG fundamentado, Sprint 17).

O provedor de LLM fica atrás da porta :class:`app.modules.assistant.llm.LLMProvider`
(adaptador Google Gemini isolado e trocável). O domínio — recuperação (RAG),
guardrails e ``source_ref`` — não importa o SDK.
"""
