# RAG production configuration and evaluation

## Active retrieval stack

Both Materials RAG and Thermo RAG use a two-stage retrieval pipeline:

1. deterministic query rewrite/expansion;
2. structured lexical scoring + BM25 + dense vector retrieval;
3. remote reranking of the first-stage candidate pool.

The active workstation configuration uses:

- embedding API: `https://openrouter.ai/api/v1/embeddings`
- embedding model: `qwen/qwen3-embedding-8b`
- vector dimension: `4096`
- vector store: `sqlite-vec`
- rerank API: `https://openrouter.ai/api/v1/rerank`
- reranker model: `cohere/rerank-v3.5`
- rerank candidate pool: `20`

The query rewrite layer is local and deterministic: it expands Chinese or mixed-language user phrasing into retrieval terms such as canonical concepts, English synonyms, element symbols, compact alloy formulas, and thermodynamic system keys. It does not replace the user query for final answering; it only improves first-stage recall before reranking.

If the reranker is unavailable, the retriever preserves the first-stage hybrid order. Embedding retains the existing `local_hash` availability fallback, but production evaluation should always pass `--require-remote` so fallback results cannot be mistaken for real model results.

## Configuration

Supported environment variables:

```text
PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_BACKEND=llm_api
PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_API_BASE_URL=https://openrouter.ai/api/v1
PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_BACKEND=llm_api
PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_API_BASE_URL=https://openrouter.ai/api/v1
PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
PHASE_DIAGRAM_RAG_RERANKER_ENABLED=true
PHASE_DIAGRAM_RAG_RERANKER_API_BASE_URL=https://openrouter.ai/api/v1
PHASE_DIAGRAM_RAG_RERANKER_MODEL=cohere/rerank-v3.5
PHASE_DIAGRAM_RAG_RERANKER_CANDIDATE_POOL=20
```

`PHASE_DIAGRAM_RAG_RERANKER_API_KEY` is optional when an OpenRouter embedding key is already configured; the reranker reuses that key. Keep API keys in a local environment file or secret manager and do not commit them.

## Frozen blind evaluation

The frozen dataset contains 247 cross-model synthetic holdout queries:

- 106 curated materials cases;
- 112 Wikipedia-topic cases;
- 29 thermodynamic-system cases.

It was frozen before its first complete evaluation and must not be used to tune aliases, weights, or prompts. Run it with:

```bash
cd backend
conda run -n lammps_agent python benchmarks/run_rag_blind_eval.py \
  --workers 3 \
  --require-remote \
  --require-reranker
```

The June 2026 baseline after adding local query rewrite and a 20-document rerank pool is:

| Metric | Hybrid retrieval | After reranking |
|---|---:|---:|
| candidate-pool misses | 0 / 247 | 0 / 247 |
| Hit@1 | 76.52% | 95.14% |
| Hit@3 | 91.09% | 99.19% |
| Hit@5 | 93.93% | 99.60% |
| MRR | 0.8457 | 0.9719 |
| nDCG@5 | 0.8440 | 0.9680 |

Three cases missed the original 12-document first-stage candidate pool. A reranker cannot recover documents absent from its candidate pool; these misses motivated a general recall improvement: a wider 20-document candidate pool plus local query rewrite for multilingual synonyms, formula parsing, and materials/metallurgy encyclopedia-domain bridging. Do not repeatedly tune against this frozen set; use new human-labeled cases for the next unbiased evaluation.

Latest suite-level reranked Hit@5:

- curated materials blind cases: 100.00%;
- Wikipedia blind cases: 99.11%;
- thermodynamic database blind cases: 100.00%.

This is a synthetic holdout benchmark, not proof of equivalent performance on real user traffic. The next unbiased evaluation should come from newly collected, manually labeled production queries.
