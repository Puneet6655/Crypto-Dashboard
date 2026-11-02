# Model dossiers (Milestone 9)

One page per method from the project plan: Random Forest and XGBoost (the two models the Sensors
paper, Ashfaq et al. 2022, compares for Bitcoin transactions), logistic regression (the baseline
both must beat) and SMOTE (a resampling method the plan lists as a separate experiment). Numbers come
from `backend/reports/model_registry.md` (2 Nov 2026): train on Elliptic++ wallets of steps 1-34,
threshold on steps 31-34, test once on steps 35-49 (97,754 rows, 5.1% illicit).

Common to all models:
- **Question answered:** does this wallet's activity in a four-week window look like wallets
  Elliptic++ labelled illicit in 2016-2017? Positive class: Elliptic++ class 1. Class 3 (unknown) is
  never used as licit.
- **What a score is not:** proof of a crime, a probability that the owner is a criminal, or a
  statement about today's behaviour. It is an offline benchmark.
- **Metric:** PR-AUC first (illicit rows are rare, so ROC-AUC flatters every model), then precision,
  recall and F1 at the threshold chosen on validation, and the confusion matrix.
- **Leakage guards:** chronological split; exposure and hop features use only labels from earlier
  steps; a row's own label never counts toward its features; tuning used steps 1-34 only; an
  address-disjoint test (`reports/hop_features.md`) gives the same numbers.

---

## Logistic regression (baseline)

**What it is.** A linear model: the log-odds of "illicit" is a weighted sum of the features.

**Why it is here.** The plan requires both tree models to beat a simple, transparent baseline by
more than their run-to-run spread, so a good result cannot come from the split alone.

**Settings.** `training/model_registry.py`: median imputation, signed log1p on every feature (BTC
amounts span many orders of magnitude), standardisation, `LogisticRegression(class_weight="balanced",
max_iter=3000, C=1.0)`. Deterministic, so one seed.

**Results.** PR-AUC 0.381 (v2+exp) and 0.465 (with hops); F1 0.448 and 0.457. Thresholds are very
high (0.96, 0.88) because balanced class weights push scores up.

**Strengths.** Fast, stable, every weight is readable, SHAP values are exact (weight x standardised
value).

**Weaknesses.** Cannot model interactions (for example "many inputs AND round amounts") or
thresholds inside one feature, which is where most of the signal is. Sensitive to feature scaling
and outliers.

---

## Random Forest

**What it is.** Many decision trees, each trained on a bootstrap sample of rows and a random subset
of features at each split; the score is the share of trees voting illicit.

**Why it is here.** One of the two models in the Sensors paper; robust to unscaled, skewed features
and to irrelevant ones; few settings to tune.

**Settings.** Tuned on steps 1-34 only (`reports/tuning.md`): 300 trees, `min_samples_leaf=3`,
`max_features="sqrt"`, unlimited depth, `class_weight="balanced_subsample"`.

**Results.** PR-AUC 0.537 (v2+exp, mean 0.530 ± 0.005 over 3 seeds) and 0.629 with hops (0.629 ±
0.003). Beats the baseline on both sets. F1 0.506 and 0.592. Precision is lower than XGBoost at a
similar recall (0.59 vs 0.87 with hops).

**Strengths.** Hard to overfit badly; little sensitivity to its settings; SHAP TreeExplainer is
exact and its values are in probability units, which are easy to read.

**Weaknesses.** Large on disk (22-26 MB compressed, 150 MB uncompressed for the older model) and
slower to explain (about 2-3 s per address). Scores are vote shares, poorly calibrated near 0 and 1.
Lower precision means more false alerts at the same recall.

---

## XGBoost

**What it is.** Gradient-boosted trees: each new shallow tree corrects the errors of the trees
before it.

**Why it is here.** The second model in the Sensors paper, and usually the strongest on tabular
data with rare positives.

**Settings.** Tuned on steps 1-34 only: 600 trees, depth 4, learning rate 0.1, subsample 0.85,
column sample 0.9, `min_child_weight=5`, `reg_lambda=20`, `scale_pos_weight` = 0.25 x (licit/illicit
ratio). `hist` tree method, trained on the GPU when available.

**Results.** Best model on both feature sets: PR-AUC 0.666 (v2+exp, 0.664 ± 0.002) and 0.756 with
hops (0.750 ± 0.005); precision 0.85-0.87 at recall 0.47-0.53. Retrained before each test step it
reaches 0.753 and 0.833 (`reports/hop_features.md`).

**Strengths.** Best precision, small (about 0.3 MB), fast to score and explain (under 1 s).
Handles missing values natively.

**Weaknesses.** More settings to get wrong; scores drift lower on 2018-19 data (the 1%-of-random
threshold fell from 0.992 to 0.914), so thresholds need re-picking on recent data; catches few
ransomware addresses because Elliptic++ illicit behaviour is different.

---

## SMOTE (Synthetic Minority Over-sampling Technique)

**What it is.** Creates synthetic illicit rows by interpolating between an illicit row and its
nearest illicit neighbours, so the training set is less imbalanced.

**Why it was considered.** Illicit rows are about 5% of labelled wallets; the plan lists SMOTE as a
separate stretch experiment.

**Decision: not used, and not run yet.** Class weights (`class_weight`, `scale_pos_weight`) already
handle the imbalance without inventing rows, and all thresholds are chosen on real validation data.
Reasons to stay cautious with SMOTE here:
- Interpolating between two wallets' features (counts, BTC amounts, timing) can create wallets that
  could not exist, especially for count and ratio features.
- With time-ordered data, synthetic rows must be built from training steps only, or they leak.
- On strongly imbalanced tabular data, tree ensembles with class weights usually match SMOTE on
  PR-AUC, and SMOTE tends to inflate recall at the cost of precision, which matters for alerts.

**If someone runs it.** Install `imbalanced-learn`, oversample inside steps 1-30 only, choose the
threshold on steps 31-34, and add it to the registry as its own entry so it is compared on the same
test with the same 3-seed rule.

---

## Explanations (SHAP)

Every registered model can be explained for one address (Models page, or "Why Flagged?" for the
model methods). SHAP splits the model's output into a base value (the average training score) plus
one contribution per feature; the contributions add up exactly to the output (tested). A large bar
says what moved the model's score, not why an address is or is not criminal. The most influential
features in practice are the BTC share exchanged directly with known-illicit addresses, the size of
the 2-hop neighbourhood, input/output counts, fees and fee rate.
