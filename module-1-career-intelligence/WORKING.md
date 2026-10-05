# Module 1: Career Intelligence & Forecasting Engine — Technical Working Specification

**Author / Team:** Vriddhi Platform Core Engineering  
**Module Branch:** `feat/module-1-career-intelligence`  
**Target Goal:** Answer with scientific rigor: *"Which careers and jobs are likely to be valuable over the next five years?"*

---

## 1. System Architecture & End-to-End Pipeline

The Career Intelligence Engine operates as an autonomous, evidence-backed analytical pipeline. It transforms raw multi-source labor market data from India and global economies into probabilistic 5-year forecasts, task-level AI exposure evaluations, career knowledge graphs, and dynamic rankings.

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           RAW DATA INGESTION                                │
│                                                                             │
│  O*NET 31.0 Database (U.S. DOL)      ──► 1,016 Occupations, 18,838 Tasks    │
│  LinkedIn Global Postings (arshkon)  ──► 115,000 Cleaned Global Postings     │
│  Naukri Indian Job Market (3 Sources)──► 72,691 Verified Indian Postings     │
│  Salary Benchmarks (INR & USD)       ──► 43,374 Normalized Salary Points     │
│  Skill Demand Registry               ──► 367,395 Regional Skill Observations │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 UNIFIED SQLite REPOSITORY (career_intel.db)                 │
│                                                                             │
│  Tables: occupations, occupation_tasks, occupation_skills, occupation_tech,  │
│          job_postings_global, job_postings_india, salary_benchmarks,         │
│          skill_demand, ai_exposure_tasks                                    │
│  Views:  v_india_vs_global, v_skill_comparison                              │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ANALYTICAL ENGINES                               │
│                                                                             │
│  1. DemandEngine:      Multi-signal current market scoring                  │
│  2. AIExposureEngine:  Granular task-level transformation assessment        │
│  3. Forecaster:        5-year probabilistic projections & time-series cones │
│  4. KnowledgeGraph:    Multi-relational node-edge competency network        │
│  5. RankingEngine:     Configurable multi-criteria decision ranker          │
│  6. EvidenceEngine:    Explainable causal drivers synthesis                 │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             REST API LAYER                                  │
│                                                                             │
│  POST /api/v1/career/analyze            POST /api/v1/career/rank            │
│  POST /api/v1/career/search_by_domain   GET  /api/v1/career/compare         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Mathematical Formulations & Methodologies

### 2.1. Current Market Demand Index ($D_c$)

The Current Market Demand score ($D_c \in [0.10, 0.98]$) assesses immediate employer hiring appetite. Rather than relying solely on raw posting counts (which are vulnerable to platform biases), $D_c$ is a composite of four distinct market dimensions:

$$D_c = w_v \cdot S_{\text{vol}} + w_d \cdot S_{\text{div}} + w_m \cdot S_{\text{vel}} + w_g \cdot S_{\text{geo}}$$

Where weights are calibrated to:
$$w_v = 0.40, \quad w_d = 0.25, \quad w_m = 0.20, \quad w_g = 0.15$$

#### Component Signals:
1. **Posting Volume ($S_{\text{vol}}$)**: Log-normalized to prevent massive generic titles from collapsing specialized high-value technical careers:
   $$S_{\text{vol}} = \min\left(1.0, \frac{\ln(1 + V)}{\ln(1 + V_{\max})}\right)$$
   *(where $V$ is total observed postings and $V_{\max} = 15,000$ across combined markets).*

2. **Employer Diversification ($S_{\text{div}}$)**: Measures whether demand is healthy and widespread across hundreds of firms, or artificially driven by a single corporation's temporary hiring spree:
   $$S_{\text{div}} = \min\left(1.0, \frac{U}{0.40 \cdot V + 1.0}\right)$$
   *(where $U$ is the count of distinct hiring companies).*

3. **Hiring Velocity ($S_{\text{vel}}$)**: Captures recent acceleration by measuring the proportion of active postings from the 2024–2026 hiring cycle relative to historical baselines:
   $$S_{\text{vel}} = \min\left(1.0, \frac{V_{\text{recent}}}{V} \cdot 1.20\right)$$

4. **Geographic Spread ($S_{\text{geo}}$)**: Evaluates whether hiring spans multiple major metros (e.g., Bengaluru, Hyderabad, Pune, Mumbai, NCR in India; SF, NYC, London, Zurich globally):
   $$S_{\text{geo}} = \min\left(1.0, \frac{N_{\text{locations}}}{8}\right)$$

---

### 2.2. Granular Task-Level AI Exposure Model ($E_{\text{AI}}$)

A central requirement of the system specification is:
> **"Do NOT make simplistic claims such as: AI can perform task X → occupation will disappear."**

The engine decomposes every occupation into its official O*NET task statements ($T_1, T_2, \dots, T_n$) and assesses the *character* of AI transformation for each individual task:

```text
Occupation ──► Granular Tasks ──► AI Exposure per Task ──► Task Transformation Mode ──► Aggregate Role Impact
```

#### Task Classification Matrix:
1. **Direct Automation ($E_i \in [0.70, 0.95]$)**:
   * *Characteristics*: Highly codified, repetitive data entry, template formatting, routine transcription, rule-based reconciliation.
   * *Impact*: High substitution risk; labor hours dedicated to this task decrease substantially.
2. **AI Augmentation ($E_i \in [0.40, 0.75]$)**:
   * *Characteristics*: Software engineering, statistical modeling, analytical synthesis, code review, drafting, medical diagnostics.
   * *Impact*: **Productivity multiplier**. Generative AI tools and LLM copilots expand worker capacity by 2–3×. This stimulates *increased* organizational demand for skilled professionals who can direct the tools.
3. **Human-Centric / High Discretion ($E_i \in [0.10, 0.35]$)**:
   * *Characteristics*: Executive stakeholder negotiation, interpersonal counseling, conflict resolution, hands-on physical dexterity, ethical discretion.
   * *Impact*: Low exposure; requires human empathy, presence, or fiduciary judgment.

#### Aggregate Occupational Exposure Score:
$$E_{\text{AI}} = \frac{1}{n} \sum_{i=1}^{n} E_i$$

* **Low Transformation Exposure**: $E_{\text{AI}} < 0.35$
* **Moderate Transformation Exposure**: $0.35 \le E_{\text{AI}} \le 0.65$ *(Augmentation Sweet Spot)*
* **High Transformation Exposure**: $E_{\text{AI}} > 0.65$ *(High Automation Risk)*

---

### 2.3. 5-Year Probabilistic Forecaster ($G_5$)

The 5-Year Growth Score ($G_5 \in [0.12, 0.96]$) models the future trajectory through a multi-factor interaction between market demand, AI productivity leverage, and hiring momentum:

$$G_5 = \text{clamp}\Big(0.70 \cdot D_c + \Delta_{\text{AI}} + M_{\text{vel}}, \ 0.12, \ 0.96\Big)$$

Where:
* **$\Delta_{\text{AI}} = (0.50 - E_{\text{AI}}) \times 0.30$**:
  * If a role is heavily augmented ($E_{\text{AI}} \approx 0.40$), $\Delta_{\text{AI}}$ is positive, boosting the career's projected value.
  * If a role is routine-heavy ($E_{\text{AI}} \approx 0.85$), $\Delta_{\text{AI}}$ is negative, penalizing the growth projection.
* **$M_{\text{vel}}$**: Velocity momentum derived from recent recruitment expansion.

#### Multi-Year Time-Series Trajectory Series:
To power line charts with India and Global comparisons, the engine generates continuous indices (base 100 in 2024) across historical years (2021–2026) and forward projections (2027–2031):

For year $t \in [2027, 2031]$ (where $k = t - 2026$):
$$I_{\text{global}}(t) = I_{\text{global}}(t-1) \cdot \Big(1 + g \cdot \phi^k\Big)$$
$$I_{\text{india}}(t) = I_{\text{india}}(t-1) \cdot \Big(1 + 1.15 \cdot g \cdot \phi^k\Big)$$

Where:
* $g = (G_5 - 0.50) \times 0.22$ is the baseline annual growth rate.
* $\phi = 0.90$ is an **autoregressive trend-damping parameter** that prevents unrealistic infinite compounding.
* $1.15$ is the Indian technology market beta factor (reflecting historically faster domestic digital expansion).

#### Probabilistic Uncertainty Cones (Fan Chart Bounds):
Uncertainty naturally increases with projection horizon:
$$\sigma_k = (1.0 - C_s) \cdot 14.0 \cdot \sqrt{k}$$
$$\text{Upper Bound}(t) = I(t) + \sigma_k, \quad \text{Lower Bound}(t) = \max\Big(10.0, \ I(t) - \sigma_k\Big)$$

---

### 2.4. Statistical Confidence Calibration ($C_s$)

Confidence reflects empirical sample adequacy and cross-source verification:
$$C_s = \min\Big(0.95, \ S_{\text{sample}} + S_{\text{task}} + S_{\text{cross}} + 0.10\Big)$$

* $S_{\text{sample}} = \min(V / 800, 0.45)$: posting sample depth.
* $S_{\text{task}} = 0.30$ if grounded in official O*NET task decomposition ($0.10$ if fallback).
* $S_{\text{cross}} = 0.15$ if both Indian and Global datasets confirm convergent trajectories.

---

### 2.5. Configurable Career Ranking Utility ($R_c$)

Careers are ranked using a multi-attribute utility function with fully dynamic weights:

$$R_c = w_{\text{dem}} \cdot D_c + w_{\text{gro}} \cdot G_5 + w_{\text{res}} \cdot (1 - 0.70 \cdot E_{\text{AI}}) + w_{\text{sal}} \cdot S_{\text{sal}} + w_{\text{conf}} \cdot C_s$$

* Weights are normalized such that $\sum w_i = 1.0$.
* Default weights: Demand ($30\%$), Growth ($30\%$), AI Resilience ($20\%$), Salary Level ($10\%$), Confidence ($10\%$).

---

### 2.6. Career Knowledge Graph & Network Modeling

For every analyzed career, the engine constructs an in-memory knowledge graph:
* **Node Types**: `occupation`, `domain`, `task` (attributed with transformation type), `skill` (attributed with importance), `technology` (attributed with hot/in-demand flag).
* **Edge Types**: `BELONGS_TO_DOMAIN`, `EXECUTES_TASK`, `REQUIRES_COMPETENCY`, `UTILIZES_TOOL`.

This network structure powers downstream visual graph explorers, skill-gap pathing, and explainability.

---

### 2.7. Semantic Interest Domain Search

When a user searches for broad interest areas (e.g. *"FinTech"*, *"Artificial Intelligence"*, *"Cloud Systems"*, *"Data Analytics"*), the search engine:
1. Tokenizes and expands domain queries.
2. Performs multi-field relevance scoring across `occupations.title`, `occupations.description`, and `occupation_skills.skill_name`.
3. Evaluates matched pathways on the fly, returning projected growth and matching skill clusters.

---

## 3. Outcome Variable Dictionary & Interpretation Guide

| Outcome Variable | Type / Range | What It Represents | Real-World Interpretation |
|---|---|---|---|
| **`current_demand_score`** | Float (`0.10` – `0.98`) | Immediate market hiring appetite | **`> 0.80`**: Widespread active hiring across multiple regions & industries.<br>**`0.50–0.79`**: Healthy baseline hiring.<br>**`< 0.50`**: Niche or cooling market. |
| **`growth_score`** | Float (`0.12` – `0.96`) | 5-Year forward trajectory | **`> 0.75`**: Strong expansion well above GDP baseline.<br>**`0.55–0.74`**: Moderate sustainable growth.<br>**`< 0.40`**: Contractive or heavily automated career. |
| **`ai_exposure_score`** | Float (`0.00` – `1.00`) | Task-level AI interaction | **`0.35–0.60` (Sweet Spot)**: Augmented role (AI expands output; worker becomes more valuable).<br>**`> 0.65`**: Routine execution at high risk of workflow automation. |
| **`confidence_score`** | Float (`0.10` – `0.95`) | Statistical reliability of forecast | **`> 0.75`**: High certainty backed by thousands of postings and O*NET matrices.<br>**`< 0.40`**: Sparse empirical data (interpret cautiously). |
| **`outlook`** | Categorical String | Summary forecast classification | **`Strong Growth`**: High growth + high confidence.<br>**`Moderate Growth`**: Steady trajectory.<br>**`Stable Demand`**: Balanced headcount.<br>**`Transforming`**: High routine automation risk. |
| **`top_skills`** | Array of Strings | Primary skill requirements | Top technical competencies extracted from real postings, used directly by Module 2 for skill-gap calculation. |
| **`drivers`** | Array of Strings | Plain-English causal explanations | Answers *"Why is this career recommended?"* with concrete posting numbers, metro concentrations, and AI augmentation facts. |
| **`regional_breakdown`** | Object (India / Global) | Domestic vs. International metrics | Side-by-side volumes, top hiring cities, and salary levels in **INR (LPA)** and **USD/year**. |
| **`yearly_trajectory`** | Object | Time-series data points (2021–2031) | Historical year-by-year counts + 5-year forecast points with upper/lower confidence bands for line plotting. |
| **`knowledge_graph`** | Object (`nodes`, `edges`) | Structural competency network | Connected graph of competencies, tools, tasks, and domain associations. |

---

## 4. REST API Endpoint Reference

### 4.1. Analyze Career
`POST /api/v1/career/analyze`

**Request:**
```json
{
  "occupation": "Data Scientist",
  "region": "all"
}
```

**Response Payload:**
```json
{
  "occupation": "Data Scientists",
  "soc_code": "15-2051.00",
  "current_demand_score": 0.91,
  "growth_score": 0.76,
  "ai_exposure_score": 0.45,
  "confidence_score": 0.95,
  "outlook": "Strong Growth",
  "top_skills": ["python", "machine_learning", "sql", "deep_learning", "cloud", "docker"],
  "drivers": [
    "Substantial real-world market presence with 12,480 verified postings across global and Indian labor markets.",
    "Strong recent hiring momentum with steady acceleration from 2024 through 2026.",
    "High augmentation leverage: 18 tasks enhanced by generative AI tools, increasing worker productivity rather than substituting roles.",
    "Balanced multi-regional hiring spanning leading Indian tech hubs (Bengaluru) and international markets.",
    "High employer demand for foundational and emerging capabilities: python, machine_learning, sql, deep_learning."
  ],
  "tasks_analyzed": 26,
  "sample_tasks": [
    {
      "task_description": "Develop machine learning models and evaluate statistical predictive algorithms",
      "ai_impact_score": 0.55,
      "transformation_type": "AI Augmentation",
      "rationale": "Task is significantly accelerated by generative AI/ML copilots, expanding analytical capacity and throughput."
    }
  ],
  "regional_breakdown": {
    "india": {
      "region": "india",
      "posting_volume": 4200,
      "posting_growth_yoy_pct": 12.5,
      "median_salary_inr_lpa": 18.5,
      "median_salary_usd": 22200.0,
      "top_locations": ["Bengaluru", "Hyderabad", "Pune"],
      "top_skills": ["python", "machine_learning", "sql"]
    },
    "global": {
      "region": "global",
      "posting_volume": 8280,
      "posting_growth_yoy_pct": 15.0,
      "median_salary_usd": 138500.0,
      "top_locations": ["San Francisco, CA", "New York, NY", "London, UK"],
      "top_skills": ["python", "sql", "aws"]
    }
  },
  "yearly_trajectory": {
    "historical_years": [2021, 2022, 2023, 2024, 2025, 2026],
    "forecast_years": [2027, 2028, 2029, 2030, 2031],
    "cutoff_year": 2026,
    "series": [
      {
        "year": 2024,
        "status": "historical",
        "india_index": 100.0,
        "global_index": 100.0,
        "india_lower_bound": 96.0,
        "india_upper_bound": 104.0,
        "global_lower_bound": 97.0,
        "global_upper_bound": 103.0
      },
      {
        "year": 2031,
        "status": "forecast",
        "india_index": 142.6,
        "global_index": 134.2,
        "india_lower_bound": 128.5,
        "india_upper_bound": 156.7,
        "global_lower_bound": 121.4,
        "global_upper_bound": 147.0
      }
    ]
  },
  "knowledge_graph": {
    "nodes": [
      { "id": "occ_15-2051.00", "label": "Data Scientists", "type": "occupation", "weight": 1.0 },
      { "id": "dom_technology", "label": "Technology & Applied Sciences", "type": "domain", "weight": 0.85 },
      { "id": "tech_python", "label": "Python", "type": "technology", "weight": 1.0, "metadata": { "is_hot_tech": true } }
    ],
    "edges": [
      { "source": "occ_15-2051.00", "target": "tech_python", "relationship": "UTILIZES_TOOL", "weight": 0.90 }
    ]
  }
}
```

---

### 4.2. Rank Multiple Careers
`POST /api/v1/career/rank`

**Request:**
```json
{
  "occupations": ["Software Developers", "Data Scientists", "Data Entry Keyers"],
  "weights": {
    "current_demand": 0.35,
    "growth": 0.35,
    "ai_resilience": 0.20,
    "salary_level": 0.10
  },
  "top_k": 3
}
```

---

### 4.3. Search by Interest Domain
`POST /api/v1/career/search_by_domain`

**Request:**
```json
{
  "domain_query": "Artificial Intelligence Machine Learning",
  "top_k": 5
}
```

**Response:**
```json
{
  "domain_query": "Artificial Intelligence Machine Learning",
  "results": [
    {
      "occupation": "Data Scientists",
      "soc_code": "15-2051.00",
      "domain": "Technology / Engineering",
      "relevance_score": 0.95,
      "matching_skills": ["Machine Learning", "Python", "Predictive Analytics"],
      "outlook": "Strong Growth",
      "growth_score": 0.76
    }
  ]
}
```

---

## 5. Downstream Integration Contract (M1 → M2 & M3)

Per `context/INTEGRATION.md`:
* **M1 does not inspect user resumes or calculate personal gaps.**
* **M1 produces the target career profile:**
  * `top_skills` $\rightarrow$ Passed to **Module 2 (Skill Gap)** to benchmark against candidate experience and detect missing competencies.
  * `regional_breakdown.salary` and `current_demand_score` $\rightarrow$ Passed to **Module 3 (Job Matching & Salary)** to validate live salary offers against domestic and global market baselines.

---

## 6. Verification & Automated Test Suite

All algorithms and endpoints are tested using `pytest` in `tests/test_career_intel.py`:
```bash
pytest module-1-career-intelligence/tests/ -v
```

* **17/17 tests passing**:
  * Known occupation matching & unknown occupation graceful fallbacks.
  * Sparse data & zero-data handling.
  * Granular task-level AI exposure differentiation (automation vs augmentation vs human discretion).
  * Multi-year continuous trajectory generation (2021–2031) with expanding confidence bounds.
  * Knowledge graph node and edge construction.
  * Dynamic ranker score ordering and custom weight normalization.
  * Semantic domain search endpoint.
  * API health and compare endpoints.
