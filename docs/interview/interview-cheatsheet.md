# Interview Cheatsheet

---

## Mental Guardrails

**The vibe:** You are an engineer who cares about the *Why*.

**Three stories to anchor everything:**

| Topic | One-liner |
|---|---|
| Fine-tuning (NBA) | "I unfroze late-stage attention layers to capture domain-specific motion — early layers learn universal features like edges, late layers learn task-specific abstractions." |
| Latency (Lumen) | "Cloudflare eliminated 90% of origin traffic. For requests that did reach the server, P95 dropped from 4.8s to 900ms using an in-process LRU." |
| Scale (Ultra Mobile) | "I eliminated the engineering handoff entirely. Stakeholders configure promotions in QA, export to CSV, import at launch. 3 sprints → 1." |

---

## SQL: Deduplication + Join

**Scenario:** Dataset A (email, job_title, duplicates from promotions) + Dataset B (email, salary, duplicates from bonuses).

```sql
WITH latest_title AS (
    SELECT email, job_title,
        ROW_NUMBER() OVER (PARTITION BY email ORDER BY updated_at DESC) AS rn
    FROM dataset_a
),
total_compensation AS (
    SELECT email, SUM(salary) AS total_salary
    FROM dataset_b
    GROUP BY email
)
SELECT lt.email, lt.job_title, tc.total_salary
FROM latest_title lt
LEFT JOIN total_compensation tc ON lt.email = tc.email
WHERE lt.rn = 1
```

**Key points:**
- Clarify before writing: is there a timestamp? SUM or latest salary? LEFT or INNER JOIN?
- Clean each dataset independently in CTEs before joining — never join dirty data
- `ROW_NUMBER() OVER (PARTITION BY email ORDER BY updated_at DESC)` = rank per group, newest first
- `WHERE rn = 1` = keep only the latest

---

## SQL: Audit Log Performance Query

```sql
SELECT endpoint, COUNT(*) as calls,
    ROUND(AVG(response_ms)::numeric, 0) AS avg_ms,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY response_ms)::numeric, 0) AS p95_ms
FROM audit_logs
WHERE endpoint != '/metrics'
GROUP BY endpoint
ORDER BY avg_ms DESC
```

- **AVG** — mean, skewed by outliers
- **P95** — 95% of requests finished faster than this. The worst-case experience without being thrown off by a single spike
- `::numeric` cast required because `PERCENTILE_CONT` returns `double precision` which `ROUND()` doesn't accept directly in Postgres
- `ORDER BY avg_ms DESC` — worst offender is always row 1

---

## Python at Scale: Which Tool When

| Scale | Tool | Why |
|---|---|---|
| < 1M rows | SQL / Pandas | Fits in memory |
| 1M–50M rows | Pandas + chunking, or DB | One machine handles it |
| 50M–500M rows | Dask | Pandas API, parallelizes across cores |
| 500M+ rows | Spark | Distributed cluster, fault-tolerant |

**Real answer — not row count, ask three questions:**
1. Does it fit in RAM?
2. How long does it take? Is that acceptable?
3. Does it run once or repeatedly?

> "I don't think in row counts — I think in memory and time. Premature optimization to Spark on 5 million rows is just complexity for no gain."

**Distributed = split data across multiple machines, process in parallel, combine results.**

---

## OOM Problem: Median per Group on 10GB CSV / 4GB RAM

**The trap — say this first:**
> "Median is not a streaming aggregate. Sum and count you can accumulate chunk by chunk. Median you can't — median of two medians ≠ median of all values. I need all salaries per title before I can compute anything."

**Solution 1 — Chunked accumulation:**
```python
from collections import defaultdict
import pandas as pd
import numpy as np

salary_by_title = defaultdict(list)

for chunk in pd.read_csv('salaries.csv', chunksize=100_000,
                          usecols=['job_title', 'salary']):  # only load 2 cols
    for title, salary in zip(chunk['job_title'], chunk['salary']):
        salary_by_title[title].append(salary)

result = {title: np.median(salaries) for title, salaries in salary_by_title.items()}
```
`usecols` shrinks a 10GB file to ~1GB by dropping irrelevant columns.

**Solution 2 — DuckDB (production choice):**
```python
import duckdb
result = duckdb.sql("""
    SELECT job_title, MEDIAN(salary) AS median_salary
    FROM read_csv_auto('salaries.csv')
    GROUP BY job_title
""").df()
```
> "DuckDB reads in a streaming, vectorized fashion — never loads the whole file into RAM. One line, exact median."

**Solution 3 — Approximate (T-Digest):** Streaming probabilistic structure, bounded memory, ~1% error. Use when exact isn't required.

---

## Average vs Median: Decomposable vs Not

**Average — trivially streamable, O(1) memory:**
```python
total = 0
count = 0
for chunk in pd.read_csv('salaries.csv', chunksize=100_000, usecols=['salary']):
    total += chunk['salary'].sum()
    count += chunk['salary'].count()
average = total / count
```

**Median — O(n) memory, must see all values first.**

> "This is the difference between a decomposable and non-decomposable aggregate. Sum, count, min, max, average — decomposable, streamable. Median, percentiles, mode — not decomposable. This is also why databases treat AVG() as cheap and PERCENTILE_CONT() as expensive."

---

## Salary Normalization + Outlier Detection

**Normalize hourly → annual:**
```python
HOURS_PER_YEAR = 2080  # 40hrs * 52 weeks

df['annual_salary'] = np.where(
    df['pay_type'] == 'hourly',
    df['salary'] * HOURS_PER_YEAR,
    df['salary']
)
```
If no `pay_type` column, infer with heuristic (`salary < 500` = hourly). Flag it as an assumption.

**Outlier detection — IQR:**
```python
Q1 = df['annual_salary'].quantile(0.25)
Q3 = df['annual_salary'].quantile(0.75)
IQR = Q3 - Q1

lower = Q1 - 1.5 * IQR
upper = Q3 + 1.5 * IQR

outliers = df[(df['annual_salary'] < lower) | (df['annual_salary'] > upper)]
clean    = df[(df['annual_salary'] >= lower) & (df['annual_salary'] <= upper)]
```

**Why IQR over Z-score:**
> "Z-score assumes normal distribution. Salary data is right-skewed. IQR is non-parametric — no distribution assumption, more robust for this data."

**Flag, don't delete:**
> "A $500K salary might be a real VP. A $5M salary in a dataset of teachers is a data entry error. Present the list to a stakeholder before dropping anything."

---

## R² — What It Is

**One sentence:** R² is the proportion of variance in your target that your model explains.

- R² = 1.0 → perfect predictions
- R² = 0.7 → model explains 70% of why salaries differ, 30% is noise
- R² = 0.0 → model is useless, same as guessing the average every time

```
R² = 1 - (SS_residual / SS_total)
```

**Why missing data makes R² dishonest:**
- Dropping NaN rows biases your training sample → model looks good but fails on full population
- Filling with global mean injects fake signal → artificially tightens residuals → inflates R²
- Outliers inflate SS_total → R² becomes unreliable

**What's a good R²?**
> "Depends on the domain. Physics: 0.99. HR salary prediction with messy data: 0.6–0.7 is solid. Suspiciously high R² on training data usually means overfitting or data leakage."

---

## Missing Data: Preservation Over Deletion

**Three types of missingness:**

1. **Missing completely at random** → fill with median
2. **Missing not at random** → the absence is signal, add indicator column first
3. **Structurally missing** → expected null, fill with 0 or leave

```python
# Always add indicator before filling if missingness might correlate with target
df['salary_missing'] = df['salary'].isna().astype(int)
df['salary'] = df['salary'].fillna(df.groupby('job_title')['salary'].transform('median'))
```

**Why median over mean:** Salary is right-skewed. Mean is pulled up by outliers. Median is robust.

**Why grouped imputation:** Filling a junior developer's missing salary with the company-wide median (which includes executives) is wrong. Fill within job title group for contextual accuracy.

> "Never drop NaN as a first instinct. Audit with `df.isna().sum()` first. The missing indicator column is often one of your strongest features."

---

## Pandas: merge() vs join()

```python
# merge() — explicit, works on any columns — USE THIS
pd.merge(df_a, df_b, on='email', how='left')

# join() — shorthand, joins on index by default
df_a.set_index('email').join(df_b.set_index('email'), how='left')
```

> "`join()` is a convenience wrapper around `merge()`. I default to `merge()` because it's explicit. `join()` requires `set_index` first which is extra noise."

**Why Pandas over SQL:**
> "If the data is already in a database, I'd do the join in SQL. I reach for Pandas when data comes from multiple sources — CSVs, APIs, Excel files — that aren't in the same database."

---

## Fuzzy Job Title Matching: Embeddings + Cosine Similarity

> "I wouldn't rely on exact string matching. I'd generate text embeddings and use cosine similarity to map 'Software Eng' to 'Software Engineer' with a high confidence threshold."

**Why the threshold is high here (0.95) vs Lumen (0.25–0.3):**
- Lumen: cross-modal (text query → image embedding) — different modalities, naturally lower scores
- Job titles: text-to-text, same domain, same vocabulary → nearly identical vectors → 0.95+

> "Thresholds are relative to the task. You take known correct pairs, compute their similarity distribution, and set the threshold just below where true matches cluster. Never hardcode without measuring."

---

## LRU Cache: What It Is + Why Not Redis

**LRU = Least Recently Used.** When cache is full, drop the entry accessed least recently.

`OrderedDict` implementation:
- **get** → move key to end (mark as recently used)
- **set** → append to end; if over limit, `popitem(last=False)` drops oldest

**Why not Redis:**

| | In-process LRU | Redis |
|---|---|---|
| Latency | ~0ms (RAM) | 1–5ms (network) |
| Setup | Zero, stdlib only | New container, serialization |
| Shared state | No (per-process) | Yes (all workers) |
| Persistence | Lost on restart | Survives restarts |

> "Cloudflare handles the shared-state problem at the CDN layer. In-process LRU only needs to cover the gap between a Cloudflare cold start and the next identical request within the same process. Redis would add complexity with no benefit here."

**When to reach for Redis instead:**
- Multiple API replicas (need shared cache)
- Cache must survive restarts
- Need TTL expiry per entry

---

## Dimensionality: 256 vs 512 vs 768 dims

**Cosine similarity formula doesn't change with dimensionality.** What changes is what the vectors can represent.

- **More dims** → more capacity to encode nuance → cleaner separation between true and false matches → threshold more reliable
- **Fewer dims** → faster search, less memory → but scores cluster together, threshold less reliable

**Lumen uses 768-dim CLIP (ViT-L/14)** — justified by the quality requirement for cross-modal search across image, video, and text.

> "Dimensionality doesn't move the cosine similarity score up or down directly — it changes how much the number means."

---

## Phase Classifier: Numbers Deep Dive

**The setup**

7,642 labeled frames from two curated folders (Construction Timeline photos + DJI drone footage). Labels were auto-assigned by date window — each frame's capture date (parsed from filename, not mtime) was matched to a construction phase schedule. No manual labeling. 80/20 train/test split, stratified by class.

The model is a Random Forest (200 trees, max depth 12) sitting on top of frozen CLIP embeddings — 768-dimensional vectors already computed and stored in Qdrant. No fine-tuning, no retraining CLIP. Training took ~30 seconds.

**Per-class breakdown**

```
Phase 1: Site Mobilization   precision 0.92  recall 0.93  f1 0.93  support 315
Phase 2: Foundation          precision 0.92  recall 0.88  f1 0.90  support 324
Phase 3: Rough MEP & Framing precision 0.86  recall 0.93  f1 0.89  support 560
Phase 4: Exterior            precision 0.96  recall 0.65  f1 0.78  support 115
Phase 5: Final Completion    precision 0.87  recall 0.89  f1 0.88  support 215

accuracy                                             0.89  1529
macro avg                    0.91        0.86  0.87  1529
weighted avg                 0.89        0.89  0.89  1529
```

**Phase 3** is the largest class (560 samples) — MEP and framing were done simultaneously on-site, so both trades' footage was merged into one phase. High recall (0.93) means it rarely misses Phase 3 frames, but lower precision (0.86) means it occasionally pulls in frames from adjacent phases.

**Phase 4 Exterior** is the interesting one. Precision 0.96 means when it predicts Exterior, it's almost always right. But recall 0.65 means it's missing 35% of actual Exterior frames — likely misclassifying them as Phase 3 or Phase 5 because the visual boundary is subtle (exterior work starts while interior work is finishing). Small class size (115 samples, ~8% of data) is the main constraint — not enough examples to learn edge cases.

**Phase 1 Site Mobilization** is the cleanest class (f1 0.93) — site prep is visually distinct: bare dirt, excavation equipment, no structure yet.

**Macro vs weighted:**
- Macro F1 **0.87** — unweighted average, penalizes Phase 4's low recall equally with larger classes
- Weighted F1 **0.89** — weighted by support, Phase 3's strong performance pulls it up
- The gap between 0.87 and 0.89 is entirely Phase 4 being a small, hard class. Excluding Phase 4, macro F1 ≈ 0.90

**The honest framing for interviews:**

> "This is a zero-shot temporal classifier — no labeled training data was collected manually, labels came entirely from a construction schedule. The model generalizes to 89% accuracy purely from visual signal already encoded in CLIP, which was never trained on construction footage. The practical constraint isn't model architecture — it's data volume per class. Phase 4 recall jumps with more samples."

---

## Phase Classifier: Generalization Stress Test

After getting 89% on a random holdout, I ran three stricter evaluation strategies to find out if the number was real. Then iteratively refined phase boundaries using actual inspection approval dates to test whether finer granularity closes the gap.

**Initial results (5 phases):**

| Strategy | Accuracy | vs random | What it tests |
|---|---|---|---|
| Random (baseline) | 89.1% | — | Stratified 80/20, same as original |
| Boundary | 89.1% | 0.0% | Frames within ±14 days of phase transitions |
| Camera | 88.7% | -0.4% | Train on Pixel phone, test on DJI drone |
| Temporal | 49.6% | -39.5% | Train on early frames per phase, test on late frames |

**What each result means:**

**Camera holds at 88.7%** — CLIP embeddings are camera-agnostic. Pixel vs DJI (different sensor, angle, altitude) makes almost no difference. Rules out camera-specific overfitting entirely.

**Boundary holds at 89.1%** — Phase transition frames are no harder than random. The visual boundaries between phases are clean — the model doesn't struggle at the edges.

**Temporal drops to 49.6%** — The big finding. When trained on early frames and tested on late frames within the same phase, accuracy roughly halves.

**Root cause — visual drift within phases, not temporal artifact overfitting:**

The random split mixes early and late frames from each phase into both train and test, so the model sees the full visual range of each phase during training. The temporal split enforces a harder constraint: predict the end state of a phase from training only on its beginning.

Phase 3 (Rough MEP & Framing) spans Nov 7 → Feb 19 — over three months. Early Phase 3 looks like bare framing and MEP rough-in. Late Phase 3 looks like completed framing starting to close up. These are visually distinct enough that a model trained on the early period doesn't recognize the late period as the same phase.

**Splitting experiment — diminishing returns:**

I used actual inspection approval dates as ground-truth phase boundaries and split the former monolithic Phase 3 into sub-phases. Temporal accuracy after each split:

| Split | Phases | Random | Temporal | Gap |
|---|---|---|---|---|
| Original | 5 | 89.1% | 49.6% | -39.5% |
| Split at Feb 2 (structural shell complete) | 6 | 88.6% | 54.6% | -34.0% |
| Split at Jan 6 (shear wall begins) | 7 | 86.7% | 55.4% | -31.3% |

**Diminishing returns after two splits.** Each additional sub-phase gains ~5pp temporal accuracy but costs ~1pp random accuracy (sub-phases 3b and 3c have only 290 and 316 samples — too small). The boundary strategy broke entirely for the 7-phase model: Phase 3b spans 27 days and 3c spans 17 days, both shorter than the 28-day boundary window, so nearly the entire phase is a "boundary zone" and there's nothing left to train on.

**The constraint is data, not granularity.** Visual drift exists within any meaningful phase window. Splitting Phase 3c (Feb 3–19, 17 days) still shows 9% temporal recall — there's drift even within a 2-week window. No number of splits fixes that without more footage from the end of each phase.

**Interview answer:**

> "I ran three holdout strategies to stress-test the 89%. Random and boundary both held. Camera generalization was nearly perfect at 88.7% — CLIP is camera-agnostic. But temporal holdout dropped to 49.6%. That gap isn't temporal artifact overfitting — it's visual drift within long phases. Phase 3 spans three months; early framing looks nothing like late framing. A random split accidentally gives the model both ends of the visual range, which inflates the number. I then used actual inspection approval dates as ground-truth boundaries and split Phase 3 twice — temporal improved to 55%, but hit diminishing returns: the sub-phases became too small, and the boundary strategy broke because some phases were shorter than the boundary window itself. The honest accuracy for classifying new end-of-phase footage is ~55%. The right fix is time-aware training — ensuring the model always sees both the beginning and end of each phase during training, not just at eval time."

---

## The One-Liners to Close Any Answer

- **Caching:** "In-process LRU for single-host, Redis when you need shared state or TTL, CDN for public cacheable responses — layer them based on what the problem actually requires."
- **Scale:** "I don't think in row counts — I think in memory and time."
- **Trade-offs:** "I know both options, I reasoned about the constraints, and I know when to switch."
- **Missing data:** "Preservation over deletion. Audit the missingness, use grouped medians, keep R² honest."
- **Decomposable aggregates:** "Sum, count, average — streamable. Median, percentiles — you need all values first."
