"""Local LLM via Ollama. Model names come from .env (AGENT_MODEL)."""

from langchain_ollama import ChatOllama

from config import Settings

# The prompt is ~1k tokens, so 4k context is enough. Smaller context = less memory;
# on an 8 GB Apple Silicon machine llama3.1:8b still swaps (~0.6 tokens/s).
NUM_CTX = 4096
MAX_OUTPUT_TOKENS = 800  # cap so a runaway JSON generation can't hang the pipeline


def make_agent_llm(settings: Settings) -> ChatOllama:
    # temperature 0 + fixed seed: as repeatable as a local model gets.
    return ChatOllama(
        model=settings.agent_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        seed=42,
        num_ctx=NUM_CTX,
        num_predict=MAX_OUTPUT_TOKENS,
        client_kwargs={"timeout": settings.ollama_timeout_seconds},
    )
