from app.config import settings


# Network-backed reranking is opt-in per test. This keeps the unit suite
# deterministic even when the workstation runtime config enables OpenRouter.
settings.rag_reranker_enabled = False
