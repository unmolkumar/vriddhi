# Data Sources & Provenance Registry

This document provides full data provenance, licensing, and methodology documentation
for every dataset used in Module 1 (Career Intelligence & Forecasting Engine).

---

## Source Registry

### 1. O*NET 31.0 Database (U.S. Department of Labor)

| Field | Value |
|---|---|
| **Source** | National Center for O*NET Development |
| **URL** | https://www.onetcenter.org/database.html |
| **Download** | https://www.onetcenter.org/dl_files/database/db_31_0_csv.zip |
| **Access Date** | October 2026 |
| **License** | CC BY 4.0 (Creative Commons Attribution 4.0 International) |
| **Coverage** | ~1,000 occupations covering the entire U.S. economy |
| **Format** | CSV (zipped) |
| **Files Used** | `Occupation Data.csv`, `Task Statements.csv`, `Skills.csv`, `Technology Skills.csv`, `Knowledge.csv`, `Work Activities.csv` |
| **Purpose in M1** | Standard occupation taxonomy (SOC codes), task-level decomposition for AI exposure modeling, skill profiles per occupation |
| **Limitations** | U.S.-centric SOC codes; India-specific occupations may require manual mapping |

### 2. BLS Employment Projections 2024–2034

| Field | Value |
|---|---|
| **Source** | U.S. Bureau of Labor Statistics |
| **URL** | https://www.bls.gov/emp/ |
| **Download** | https://www.bls.gov/emp/tables.htm (Table 1.2, 1.3, 1.10) |
| **Access Date** | October 2026 |
| **License** | Public Domain (U.S. Government work) |
| **Coverage** | 832 detailed occupations with 10-year employment projections |
| **Format** | XLSX / CSV |
| **Fields Used** | SOC Code, Occupation Title, 2024 Employment (thousands), 2034 Projected Employment, Change (%), Median Annual Wage, Typical Education |
| **Purpose in M1** | Official employment growth forecasts, wage baselines, education requirements |
| **Limitations** | 10-year macro projections; does not capture short-term hiring velocity |

### 3. Kaggle: LinkedIn Job Postings 2023–2024

| Field | Value |
|---|---|
| **Source** | Kaggle (arshkon/linkedin-job-postings) |
| **URL** | https://www.kaggle.com/datasets/arshkon/linkedin-job-postings |
| **API Command** | `kaggle datasets download -d arshkon/linkedin-job-postings` |
| **Access Date** | October 2026 |
| **License** | CC0: Public Domain |
| **Coverage** | 124,000+ global job postings with titles, descriptions, skills, companies, locations |
| **Format** | CSV (zipped) |
| **Fields Used** | job_id, title, description, max_salary, min_salary, med_salary, pay_period, location, company_name, application_type, skills, listed_time, expiry |
| **Purpose in M1** | Global (primarily US/EU) job posting volume, skill demand extraction, salary signals |
| **Limitations** | LinkedIn platform bias; under-represents blue-collar and government roles |

### 4. Kaggle: 1.3M LinkedIn Jobs & Skills 2024

| Field | Value |
|---|---|
| **Source** | Kaggle (asaniczka/1-3m-linkedin-jobs-skills-2024) |
| **URL** | https://www.kaggle.com/datasets/asaniczka/1-3m-linkedin-jobs-skills-2024 |
| **API Command** | `kaggle datasets download -d asaniczka/1-3m-linkedin-jobs-skills-2024` |
| **Access Date** | October 2026 |
| **License** | CC BY 4.0 |
| **Coverage** | 1.3 million job listings with detailed skill tags, scraped from LinkedIn in 2024 |
| **Format** | CSV (zipped) |
| **Fields Used** | job_title, company, job_skills, job_location, listed_time |
| **Purpose in M1** | High-volume global skill demand frequency analysis, posting velocity, employer diversification |
| **Limitations** | Single-platform source; may duplicate across regions |

### 5. Naukri.com India Job Market Dataset (PromptCloud & Tech Scrapes)

| Field | Value |
|---|---|
| **Source** | PromptCloud / Hugging Face (`jason1966/PromptCloudHQ_jobs-on-naukricom` & `muhammetakkurt/naukri-jobs-dataset`) |
| **URL** | https://huggingface.co/datasets/jason1966/PromptCloudHQ_jobs-on-naukricom |
| **Access Date** | October 2026 |
| **License** | Open Data / CC BY 4.0 |
| **Coverage** | 55,691 real Indian job postings across Bengaluru, Mumbai, Delhi/NCR, Hyderabad, Chennai, Pune, Kolkata |
| **Format** | CSV + JSONL |
| **Fields Used** | `jobtitle`, `company`, `experience`, `payrate` (INR), `joblocation_address`, `skills`, `industry`, `postdate`, `tagsAndSkills` |
| **Purpose in M1** | India-specific job market demand, city-level distribution, salary benchmarks in INR (and converted to USD), localized tech hiring trends |
| **Limitations** | Salary fields require regex parsing for Indian numbering format (Lacs PA) |

---

## Live Database Registry (`data/career_intel.db`)

The ETL pipeline (`02_etl_clean_load.py`) processes and cleans all raw data into a 103.8 MB SQLite database:

| Table Name | Row Count | Source Datasets | Regional Coverage |
|---|---|---|---|
| `occupations` | 1,016 | O*NET 31.0 | Standardized SOC Taxonomy |
| `occupation_tasks` | 18,838 | O*NET 31.0 Task Statements | Granular task-level decomposition |
| `occupation_skills` | 18,200 | O*NET 31.0 Essential Skills | Standardized importance & level |
| `occupation_tech` | 31,821 | O*NET 31.0 Software Skills | Hot & in-demand tools/frameworks |
| `job_postings_global` | 115,000 | LinkedIn (arshkon) + Kaggle AI 2025 | Global (US, EU, Worldwide) |
| `job_postings_india` | 72,691 | Naukri (PromptCloud) + India Tech 2024-26 + Data Science India | India (Bengaluru, NCR, Hyd, Mum, Pune, etc.) |
| `salary_benchmarks` | 43,374 | Cleaned from Global & India postings | Side-by-side (USD and normalized INR) |
| `skill_demand` | 367,395 | Extracted from postings | Side-by-side (India vs Global demand) |

---

## Regional Strategy: India vs Global

The database maintains strict **region tagging** (`region = 'india' | 'global'`) across all tables,
enabling side-by-side analysis:

| Dimension | India Implementation | Global Implementation |
|---|---|---|
| Job Posting Volume | 55,691 postings (Naukri.com) | 100,000 postings (LinkedIn) |
| Salary Benchmarks | 6,544 salary data points (INR & USD) | 16,830 salary data points (USD) |
| Occupation Taxonomy | Mapped to O*NET SOC equivalents | O*NET SOC native |
| Skill Demand | 120,000+ extracted Indian skill mentions | 160,000+ extracted Global skill mentions |
| Views | `v_india_vs_global` | `v_skill_comparison` |
