# LLM Route MLP Benchmark

Model artifact: `backend/models/llm_route_mlp/model.json`

## Summary

Training source: `hard_synthetic_llm_route_cases`

Synthetic samples: `3346` · telemetry samples: `0`

| Split | Accuracy | Macro precision | Macro recall | Macro F1 | Weighted F1 | Top-2 accuracy | Log loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0045 |
| test | 0.9973 | 0.9973 | 0.9973 | 0.9973 | 0.9973 | 1.0000 | 0.0103 |
| probe | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0305 |

## Test per-class metrics

| Class | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| `fast` | 0.9946 | 1.0000 | 0.9973 | 184 |
| `balanced` | 1.0000 | 0.9891 | 0.9945 | 184 |
| `strong` | 0.9946 | 1.0000 | 0.9973 | 184 |
| `vision` | 1.0000 | 1.0000 | 1.0000 | 183 |

## Test confusion matrix

Rows are true classes; columns are predicted classes.

| True \ Pred | `fast` | `balanced` | `strong` | `vision` |
| --- | ---: | ---: | ---: | ---: |
| `fast` | 184 | 0 | 0 | 0 |
| `balanced` | 1 | 182 | 1 | 0 |
| `strong` | 0 | 0 | 184 | 0 |
| `vision` | 0 | 0 | 0 | 183 |

## Probe per-class metrics

| Class | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| `fast` | 1.0000 | 1.0000 | 1.0000 | 11 |
| `balanced` | 1.0000 | 1.0000 | 1.0000 | 11 |
| `strong` | 1.0000 | 1.0000 | 1.0000 | 11 |
| `vision` | 1.0000 | 1.0000 | 1.0000 | 8 |

## Probe confusion matrix

Rows are true classes; columns are predicted classes.

| True \ Pred | `fast` | `balanced` | `strong` | `vision` |
| --- | ---: | ---: | ---: | ---: |
| `fast` | 11 | 0 | 0 | 0 |
| `balanced` | 0 | 11 | 0 | 0 |
| `strong` | 0 | 0 | 11 | 0 |
| `vision` | 0 | 0 | 0 | 8 |


## Training metadata

```json
{
  "created_at": "2026-07-13T06:49:27.894323+00:00",
  "training_source": "hard_synthetic_llm_route_cases",
  "samples_total": 3346,
  "train_samples": 2611,
  "test_samples": 735,
  "dataset_distribution": {
    "label": {
      "fast": 838,
      "balanced": 838,
      "vision": 832,
      "strong": 838
    },
    "difficulty": {
      "clean": 1389,
      "mixed": 1026,
      "adversarial": 552,
      "long_noise": 299,
      "deployment_wrapper": 80
    },
    "source": {
      "unknown": 3346
    }
  },
  "hidden_dim": 48,
  "epochs": 850,
  "learning_rate": 0.028,
  "l2": 0.0001,
  "seed": 20260710,
  "synthetic_samples": 3346,
  "telemetry_samples": 0,
  "telemetry_path": "backend/outputs/logs/events.jsonl"
}
```
