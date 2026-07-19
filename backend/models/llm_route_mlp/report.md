# LLM Route MLP Benchmark

Model artifact: `backend/models/llm_route_mlp/model.json`

## Summary

Training source: `synthetic_plus_simulated_plus_real_telemetry`

Synthetic samples: `3346` · simulated production telemetry: `1800` · real telemetry: `65`

> Simulated production telemetry is an offline proxy dataset and is not real user traffic.

| Split | Accuracy | Macro precision | Macro recall | Macro F1 | Weighted F1 | Top-2 accuracy | Log loss | ECE | Brier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 0.9236 | 0.9310 | 0.9325 | 0.9310 | 0.9242 | 0.9936 | 0.1611 | 0.0160 | 0.1017 |
| calibration | 0.9108 | 0.9168 | 0.9192 | 0.9174 | 0.9111 | 0.9938 | 0.1966 | 0.0238 | 0.1251 |
| test | 0.8952 | 0.9043 | 0.9062 | 0.9042 | 0.8960 | 0.9913 | 0.2044 | 0.0124 | 0.1292 |
| probe | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0886 | 0.0739 | 0.0407 |

## Confidence calibration

Method: `temperature_scaling` · fitted temperature: `1.1784` · OOD threshold: `5.0887`

| Evaluation | ECE before | ECE after | Brier before | Brier after |
| --- | ---: | ---: | ---: | ---: |
| calibration split | 0.0232 | 0.0238 | 0.1255 | 0.1251 |
| frozen test | 0.0164 | 0.0124 | 0.1299 | 0.1292 |

## Test per-class metrics

| Class | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| `fast` | 0.8267 | 0.9219 | 0.8717 | 269 |
| `balanced` | 0.8185 | 0.8185 | 0.8185 | 325 |
| `strong` | 0.9721 | 0.8895 | 0.9290 | 353 |
| `vision` | 1.0000 | 0.9949 | 0.9975 | 198 |

## Test confusion matrix

Rows are true classes; columns are predicted classes.

| True \ Pred | `fast` | `balanced` | `strong` | `vision` |
| --- | ---: | ---: | ---: | ---: |
| `fast` | 248 | 21 | 0 | 0 |
| `balanced` | 50 | 266 | 9 | 0 |
| `strong` | 2 | 37 | 314 | 0 |
| `vision` | 0 | 1 | 0 | 197 |

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
  "created_at": "2026-07-19T10:31:15.626981+00:00",
  "training_source": "synthetic_plus_simulated_plus_real_telemetry",
  "samples_total": 5211,
  "train_samples": 3416,
  "calibration_samples": 650,
  "test_samples": 1145,
  "dataset_distribution": {
    "label": {
      "fast": 1225,
      "balanced": 1478,
      "vision": 902,
      "strong": 1606
    },
    "difficulty": {
      "clean": 1389,
      "mixed": 1026,
      "adversarial": 552,
      "long_noise": 299,
      "deployment_wrapper": 80,
      "simulated_technical_chat": 245,
      "simulated_simple_chat": 322,
      "simulated_lammps_repair_review": 154,
      "simulated_memory_summary": 147,
      "simulated_prompt_suggestion": 100,
      "simulated_supervisor": 166,
      "simulated_rag_answer": 209,
      "simulated_phase_review": 104,
      "simulated_phase_compute": 195,
      "simulated_lammps_parse": 102,
      "simulated_vision_recognition": 56,
      "telemetry_fallback": 1,
      "telemetry_success": 64
    },
    "source": {
      "unknown": 3346,
      "simulated_production_telemetry": 1800,
      "observability_telemetry": 65
    }
  },
  "hidden_dim": 48,
  "epochs": 850,
  "learning_rate": 0.028,
  "l2": 0.0001,
  "seed": 20260710,
  "synthetic_samples": 3346,
  "simulated_telemetry_samples": 1800,
  "simulated_telemetry_is_real": false,
  "telemetry_samples": 65,
  "telemetry_path": "backend/outputs/logs/events.jsonl"
}
```
