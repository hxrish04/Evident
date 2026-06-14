# Evident decision-layer evaluation

- Cases: **13**
- Overall accuracy: **100.0%**
- False-recommend on insufficient evidence: **0** (rate 0.0%) - target 0
- Over-refusal rate: **0.0%**
- Injection cases: 3 | detected 3 | never recommended: True
- Confidence calibration ECE: **0.5687** (under_confident (conservative - safe direction), decisive decisions only)

## Per-class precision / recall / F1

| class | precision | recall | f1 | support |
|---|---|---|---|---|
| recommended | 1.0 | 1.0 | 1.0 | 3 |
| not_recommended | 1.0 | 1.0 | 1.0 | 5 |
| insufficient_evidence | 1.0 | 1.0 | 1.0 | 5 |

## Confusion matrix (rows = actual, cols = predicted)

| actual \ predicted | recommended | not_recommended | insufficient_evidence |
|---|---|---|---|
| recommended | 3 | 0 | 0 |
| not_recommended | 0 | 5 | 0 |
| insufficient_evidence | 0 | 0 | 5 |

## Calibration buckets

| confidence range | count | avg confidence | accuracy | gap |
|---|---|---|---|---|
| (0.2, 0.4] | 5 | 0.3 | 1.0 | 0.7 |
| (0.6, 0.8] | 3 | 0.65 | 1.0 | 0.35 |
