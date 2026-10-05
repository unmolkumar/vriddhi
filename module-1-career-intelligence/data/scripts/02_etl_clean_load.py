"""
ETL Pipeline: Cleans, normalizes, and loads all raw datasets into a unified
SQLite database (career_intel.db) optimized for the Career Intelligence Engine.

Handles:
  - O*NET 31.0 occupation taxonomy, tasks, skills, software tools
  - LinkedIn global job postings (124k+ real postings)
  - Indian job market postings (Naukri dataset + software/data science roles)
  - Real salary benchmarks across India and Global markets
  - Region tagging (India vs Global) across all tables
  - Skill taxonomy normalization and demand aggregation
  - Time-series alignment for posting velocity analysis

Usage:
  python data/scripts/02_etl_clean_load.py

Produces:
  data/career_intel.db (SQLite database)
"""
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent  # data/
RAW_DIR = BASE_DIR / "raw"
DB_PATH = BASE_DIR / "career_intel.db"

# INR to USD approximate conversion (for salary normalization)
INR_TO_USD = 0.012

# -------------------------------------------------------------------------
# Database Schema
# -------------------------------------------------------------------------

SCHEMA_SQL = """
-- Core occupation taxonomy (O*NET)
CREATE TABLE IF NOT EXISTS occupations (
    soc_code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    onet_title TEXT,
    domain TEXT DEFAULT 'General',
    job_zone INTEGER,
    education_typical TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Task-level decomposition per occupation (O*NET tasks)
CREATE TABLE IF NOT EXISTS occupation_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    soc_code TEXT NOT NULL,
    task_id TEXT,
    task_description TEXT NOT NULL,
    task_type TEXT,
    importance REAL,
    relevance REAL,
    frequency REAL,
    FOREIGN KEY (soc_code) REFERENCES occupations(soc_code)
);

-- Skill requirements per occupation (O*NET skills)
CREATE TABLE IF NOT EXISTS occupation_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    soc_code TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    skill_category TEXT,
    importance REAL,
    level REAL,
    FOREIGN KEY (soc_code) REFERENCES occupations(soc_code)
);

-- Technology / Software skills per occupation (O*NET software skills)
CREATE TABLE IF NOT EXISTS occupation_tech (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    soc_code TEXT NOT NULL,
    technology_name TEXT NOT NULL,
    hot_technology INTEGER DEFAULT 0,
    in_demand INTEGER DEFAULT 0,
    FOREIGN KEY (soc_code) REFERENCES occupations(soc_code)
);

-- Global job postings (LinkedIn etc.)
CREATE TABLE IF NOT EXISTS job_postings_global (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    job_id TEXT,
    title TEXT NOT NULL,
    title_normalized TEXT,
    company TEXT,
    location TEXT,
    country TEXT,
    soc_code_mapped TEXT,
    salary_min_usd REAL,
    salary_max_usd REAL,
    salary_median_usd REAL,
    experience_level TEXT,
    employment_type TEXT,
    remote_ratio REAL,
    skills TEXT,
    listed_date TEXT,
    listed_year INTEGER,
    listed_month INTEGER,
    region TEXT DEFAULT 'global',
    FOREIGN KEY (soc_code_mapped) REFERENCES occupations(soc_code)
);

-- India job postings (Naukri etc.)
CREATE TABLE IF NOT EXISTS job_postings_india (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    job_id TEXT,
    title TEXT NOT NULL,
    title_normalized TEXT,
    company TEXT,
    location TEXT,
    city TEXT,
    soc_code_mapped TEXT,
    salary_min_inr REAL,
    salary_max_inr REAL,
    salary_min_usd REAL,
    salary_max_usd REAL,
    experience_min REAL,
    experience_max REAL,
    skills TEXT,
    industry TEXT,
    listed_date TEXT,
    listed_year INTEGER,
    listed_month INTEGER,
    region TEXT DEFAULT 'india',
    FOREIGN KEY (soc_code_mapped) REFERENCES occupations(soc_code)
);

-- Salary benchmarks (multi-source, multi-region)
CREATE TABLE IF NOT EXISTS salary_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    title_normalized TEXT,
    soc_code_mapped TEXT,
    region TEXT NOT NULL,
    country TEXT,
    salary_usd REAL,
    experience_level TEXT,
    employment_type TEXT,
    remote_ratio REAL,
    company_size TEXT,
    year INTEGER,
    FOREIGN KEY (soc_code_mapped) REFERENCES occupations(soc_code)
);

-- Skill demand frequency by region and time period
CREATE TABLE IF NOT EXISTS skill_demand (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    skill_normalized TEXT NOT NULL,
    region TEXT NOT NULL,
    source TEXT NOT NULL,
    year INTEGER,
    month INTEGER,
    frequency INTEGER DEFAULT 1,
    occupation_context TEXT
);

-- AI exposure scoring (per occupation task)
CREATE TABLE IF NOT EXISTS ai_exposure_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    soc_code TEXT NOT NULL,
    task_description TEXT NOT NULL,
    time_share REAL,
    exposure_type TEXT,
    ai_impact_score REAL,
    rationale TEXT,
    FOREIGN KEY (soc_code) REFERENCES occupations(soc_code)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_postings_global_soc ON job_postings_global(soc_code_mapped);
CREATE INDEX IF NOT EXISTS idx_postings_global_year ON job_postings_global(listed_year);
CREATE INDEX IF NOT EXISTS idx_postings_global_title ON job_postings_global(title_normalized);
CREATE INDEX IF NOT EXISTS idx_postings_india_soc ON job_postings_india(soc_code_mapped);
CREATE INDEX IF NOT EXISTS idx_postings_india_year ON job_postings_india(listed_year);
CREATE INDEX IF NOT EXISTS idx_postings_india_city ON job_postings_india(city);
CREATE INDEX IF NOT EXISTS idx_skill_demand_skill ON skill_demand(skill_normalized);
CREATE INDEX IF NOT EXISTS idx_skill_demand_region ON skill_demand(region);
CREATE INDEX IF NOT EXISTS idx_salary_region ON salary_benchmarks(region);
CREATE INDEX IF NOT EXISTS idx_occ_skills_soc ON occupation_skills(soc_code);
CREATE INDEX IF NOT EXISTS idx_occ_tasks_soc ON occupation_tasks(soc_code);

-- Materialized-style views
CREATE VIEW IF NOT EXISTS v_india_vs_global AS
SELECT
    'india' AS region,
    COALESCE(listed_year, 2024) AS year,
    COUNT(*) AS total_postings,
    COUNT(DISTINCT company) AS unique_employers,
    AVG(salary_min_usd) AS avg_salary_min_usd,
    AVG(salary_max_usd) AS avg_salary_max_usd
FROM job_postings_india
GROUP BY COALESCE(listed_year, 2024)

UNION ALL

SELECT
    'global' AS region,
    COALESCE(listed_year, 2024) AS year,
    COUNT(*) AS total_postings,
    COUNT(DISTINCT company) AS unique_employers,
    AVG(salary_min_usd) AS avg_salary_min_usd,
    AVG(salary_max_usd) AS avg_salary_max_usd
FROM job_postings_global
GROUP BY COALESCE(listed_year, 2024);

CREATE VIEW IF NOT EXISTS v_skill_comparison AS
SELECT
    skill_normalized,
    region,
    SUM(frequency) AS total_demand,
    COUNT(DISTINCT year) AS years_active
FROM skill_demand
GROUP BY skill_normalized, region
ORDER BY total_demand DESC;
"""


def normalize_title(title: str) -> str:
    """Normalize job title for fuzzy matching."""
    if not title:
        return ""
    t = title.lower().strip()
    t = re.sub(r'\s*[\(\[].*?[\)\]]', '', t)  # Remove parenthetical qualifiers
    t = re.sub(r'\s*(sr\.?|jr\.?|senior|junior|lead|principal|staff|chief)\s*', ' ', t)
    t = re.sub(r'\s*(i|ii|iii|iv|v|1|2|3)\s*$', '', t)
    t = re.sub(r'[^a-z0-9\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def normalize_skill(skill: str) -> str:
    """Normalize skill string for taxonomy unification."""
    if not skill:
        return ""
    s = skill.lower().strip()
    # Common alias mappings
    aliases = {
        "javascript": "javascript", "js": "javascript", "node.js": "nodejs", "nodejs": "nodejs",
        "react.js": "react", "reactjs": "react", "vue.js": "vue", "vuejs": "vue",
        "python3": "python", "py": "python",
        "machine learning": "machine_learning", "ml": "machine_learning",
        "deep learning": "deep_learning", "dl": "deep_learning",
        "artificial intelligence": "ai", "a.i.": "ai", "genai": "generative_ai",
        "generative ai": "generative_ai", "large language models": "llm", "llms": "llm",
        "amazon web services": "aws", "google cloud": "gcp",
        "google cloud platform": "gcp", "microsoft azure": "azure",
        "postgres": "postgresql", "mongo": "mongodb", "k8s": "kubernetes",
        "c#": "csharp", "c++": "cpp", "golang": "go",
    }
    cleaned = re.sub(r'[^a-z0-9_]', '_', s).strip('_')
    return aliases.get(s, aliases.get(cleaned, cleaned))


def parse_salary_inr(salary_str: str) -> Tuple[Optional[float], Optional[float]]:
    """Parse Indian salary strings like '3-6 Lacs PA', '1,50,000 - 2,25,000 P.A'."""
    if not salary_str or str(salary_str).strip() in ('', 'Not disclosed', 'Not Disclosed', '-', 'nan'):
        return None, None
    s = str(salary_str).replace(',', '').replace('₹', '').strip()

    # Pattern: "X-Y Lacs" or "X.XX-Y.YY Lakhs P.A."
    match = re.search(r'(\d+\.?\d*)\s*[-–to]+\s*(\d+\.?\d*)\s*(lacs?|lakhs?|lpa)', s, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1)) * 100000, float(match.group(2)) * 100000
        except ValueError:
            pass

    # Pattern: raw numbers in Indian notation "150000 - 225000"
    match2 = re.search(r'(\d{5,})\s*[-–to]+\s*(\d{5,})', s)
    if match2:
        try:
            return float(match2.group(1)), float(match2.group(2))
        except ValueError:
            pass

    return None, None


def find_csv_files(directory: Path, pattern: str = "*.csv") -> List[Path]:
    """Recursively find CSV files matching pattern."""
    if not directory.exists():
        return []
    return sorted(directory.rglob(pattern))


def _find_onet_file(data_root: Path, candidate_names: List[str]) -> Optional[Path]:
    """Find an O*NET file by candidate names (case-insensitive, space/underscore agnostic)."""
    normalized_candidates = {re.sub(r'[\s_\-]+', '', name.lower()) for name in candidate_names}
    for file in data_root.rglob("*"):
        if file.is_file():
            norm_file = re.sub(r'[\s_\-]+', '', file.stem.lower())
            if norm_file in normalized_candidates:
                return file
    return None


# -------------------------------------------------------------------------
# ETL Functions
# -------------------------------------------------------------------------

def load_onet(conn: sqlite3.Connection) -> int:
    """Load O*NET 31.0 occupation taxonomy, tasks, skills, and technology."""
    onet_dir = RAW_DIR / "onet"
    if not onet_dir.exists():
        print("  [SKIP] O*NET directory not found")
        return 0

    total = 0
    cursor = conn.cursor()

    # --- Occupations ---
    occ_file = _find_onet_file(onet_dir, ["occupation_data", "occupation data"])
    if occ_file and occ_file.exists():
        print(f"  Loading occupations from {occ_file.name} ...")
        with open(occ_file, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.DictReader(f, delimiter='\t' if occ_file.suffix == '.txt' else ',')
            rows = []
            for row in reader:
                soc = row.get('O*NET-SOC Code', '').strip()
                title = row.get('Title', '').strip()
                desc = row.get('Description', '').strip()
                if soc and title:
                    rows.append((soc, title, desc, title, 'General', None, None))
            cursor.executemany(
                "INSERT OR IGNORE INTO occupations (soc_code, title, description, onet_title, domain, job_zone, education_typical) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows
            )
            total += len(rows)
            print(f"    Loaded {len(rows)} occupations")

    # --- Tasks ---
    task_file = _find_onet_file(onet_dir, ["task_statements", "task statements"])
    if task_file and task_file.exists():
        print(f"  Loading tasks from {task_file.name} ...")
        with open(task_file, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.DictReader(f, delimiter='\t' if task_file.suffix == '.txt' else ',')
            rows = []
            for row in reader:
                soc = row.get('O*NET-SOC Code', '').strip()
                task_id = row.get('Task ID', '').strip()
                task = row.get('Task', '').strip()
                task_type = row.get('Task Type', '').strip()
                if soc and task:
                    rows.append((soc, task_id, task, task_type, None, None, None))
            cursor.executemany(
                "INSERT INTO occupation_tasks (soc_code, task_id, task_description, task_type, importance, relevance, frequency) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows
            )
            total += len(rows)
            print(f"    Loaded {len(rows)} tasks")

    # --- Skills ---
    skill_file = _find_onet_file(onet_dir, ["essential_skills", "skills", "transferable_skills"])
    if skill_file and skill_file.exists():
        print(f"  Loading skills from {skill_file.name} ...")
        with open(skill_file, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.DictReader(f, delimiter='\t' if skill_file.suffix == '.txt' else ',')
            rows = []
            for row in reader:
                soc = row.get('O*NET-SOC Code', '').strip()
                name = row.get('Element Name', '').strip()
                cat = row.get('Scale ID', '')
                try:
                    val = float(row.get('Data Value', 0))
                except (ValueError, TypeError):
                    val = 0.0
                if soc and name and cat in ('IM', 'LV', ''):  # Importance scale
                    rows.append((soc, name, 'onet_skill', val, 0.0))
            cursor.executemany(
                "INSERT INTO occupation_skills (soc_code, skill_name, skill_category, importance, level) VALUES (?, ?, ?, ?, ?)",
                rows
            )
            total += len(rows)
            print(f"    Loaded {len(rows)} skill records")

    # --- Software Skills ---
    tech_file = _find_onet_file(onet_dir, ["software_skills", "technology_skills", "technology skills"])
    if tech_file and tech_file.exists():
        print(f"  Loading technology skills from {tech_file.name} ...")
        with open(tech_file, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.DictReader(f, delimiter='\t' if tech_file.suffix == '.txt' else ',')
            rows = []
            for row in reader:
                soc = row.get('O*NET-SOC Code', '').strip()
                tech = (row.get('Workplace Example') or row.get('Example') or row.get('Element Name') or row.get('Commodity Title', '')).strip()
                hot = 1 if str(row.get('Hot Technology', '')).strip().upper() == 'Y' else 0
                in_demand = 1 if str(row.get('In Demand', '')).strip().upper() == 'Y' else 0
                if soc and tech:
                    rows.append((soc, tech, hot, in_demand))
            cursor.executemany(
                "INSERT INTO occupation_tech (soc_code, technology_name, hot_technology, in_demand) VALUES (?, ?, ?, ?)",
                rows
            )
            total += len(rows)
            print(f"    Loaded {len(rows)} technology skill records")

    conn.commit()
    return total


def _load_linkedin_csv(cursor: sqlite3.Cursor, filepath: Path, source: str, limit: int = 100000) -> int:
    """Parse LinkedIn 2023-24 format CSV."""
    count = 0
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                if count >= limit:
                    break
                title = row.get('title', row.get('job_title', '')).strip()
                if not title:
                    continue
                company = row.get('company_name', row.get('company', '')).strip()
                location = row.get('location', row.get('job_location', '')).strip()

                # Salary parsing
                sal_min = _safe_float(row.get('min_salary', row.get('salary_min', '')))
                sal_max = _safe_float(row.get('max_salary', row.get('salary_max', '')))
                sal_med = _safe_float(row.get('med_salary', row.get('salary_median', '')))
                if not sal_med and sal_min and sal_max:
                    sal_med = (sal_min + sal_max) / 2.0

                # Date parsing
                listed = row.get('listed_time', row.get('list_date', row.get('posted_date', '')))
                year, month = _parse_date_to_ym(listed)
                if not year:
                    year = 2024

                # Skills
                skills = row.get('skills_desc', row.get('skills', row.get('job_skills', '')))
                exp_lvl = row.get('formatted_experience_level', '')

                batch.append((
                    source, str(row.get('job_id', '')), title, normalize_title(title),
                    company, location, _guess_country(location), None,
                    sal_min, sal_max, sal_med, exp_lvl, row.get('formatted_work_type', ''), None,
                    skills, listed, year, month, 'global'
                ))
                count += 1

                if len(batch) >= 5000:
                    cursor.executemany(
                        """INSERT INTO job_postings_global
                        (source, job_id, title, title_normalized, company, location, country, soc_code_mapped,
                         salary_min_usd, salary_max_usd, salary_median_usd, experience_level, employment_type, remote_ratio,
                         skills, listed_date, listed_year, listed_month, region)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        batch
                    )
                    batch = []

            if batch:
                cursor.executemany(
                    """INSERT INTO job_postings_global
                    (source, job_id, title, title_normalized, company, location, country, soc_code_mapped,
                     salary_min_usd, salary_max_usd, salary_median_usd, experience_level, employment_type, remote_ratio,
                     skills, listed_date, listed_year, listed_month, region)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    batch
                )
    except Exception as e:
        print(f"    [WARN] Error reading {filepath.name}: {e}")
    return count


def load_linkedin_global(conn: sqlite3.Connection) -> int:
    """Load and clean LinkedIn global job postings."""
    total = 0
    cursor = conn.cursor()

    src1_dir = RAW_DIR / "linkedin_2023_24"
    csv_files = find_csv_files(src1_dir)
    for csv_file in csv_files:
        if 'postings.csv' in csv_file.name.lower():
            print(f"  Loading global postings from {csv_file.name} ...")
            loaded = _load_linkedin_csv(cursor, csv_file, source="linkedin_2023_24", limit=100000)
            total += loaded
            print(f"    Loaded {loaded} global postings from {csv_file.name}")
            break

    conn.commit()
    return total


def load_india_jobs(conn: sqlite3.Connection) -> int:
    """Load and clean Indian job market postings from multiple real sources."""
    total = 0
    cursor = conn.cursor()

    # Source 1: Naukri promptcloud CSV (22,000 real job postings)
    naukri_sample = RAW_DIR / "naukri_com-job_sample.csv"
    if naukri_sample.exists():
        print(f"  Loading India postings from {naukri_sample.name} ...")
        count = 0
        try:
            with open(naukri_sample, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f)
                batch = []
                for row in reader:
                    title = row.get('jobtitle', '').strip()
                    if not title:
                        continue
                    company = row.get('company', '').strip()
                    location = row.get('joblocation_address', '').strip()
                    city = location.split(',')[0].strip() if location else 'India'

                    # Salary parsing
                    payrate = row.get('payrate', '').strip()
                    sal_min_inr, sal_max_inr = parse_salary_inr(payrate)
                    sal_min_usd = round(sal_min_inr * INR_TO_USD, 2) if sal_min_inr else None
                    sal_max_usd = round(sal_max_inr * INR_TO_USD, 2) if sal_max_inr else None

                    # Experience parsing
                    exp_min, exp_max = _parse_experience(row.get('experience', ''))

                    # Skills & Industry
                    skills = row.get('skills', '').strip()
                    industry = row.get('industry', '').strip()
                    postdate = row.get('postdate', '').strip()
                    year, month = _parse_date_to_ym(postdate)
                    if not year:
                        year = 2024

                    batch.append((
                        'naukri_promptcloud', str(row.get('uniq_id', row.get('jobid', ''))),
                        title, normalize_title(title), company, location, city, None,
                        sal_min_inr, sal_max_inr, sal_min_usd, sal_max_usd,
                        exp_min, exp_max, skills, industry,
                        postdate, year, month, 'india'
                    ))
                    count += 1

                    if len(batch) >= 5000:
                        cursor.executemany(
                            """INSERT INTO job_postings_india
                            (source, job_id, title, title_normalized, company, location, city, soc_code_mapped,
                             salary_min_inr, salary_max_inr, salary_min_usd, salary_max_usd,
                             experience_min, experience_max, skills, industry,
                             listed_date, listed_year, listed_month, region)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            batch
                        )
                        batch = []

                if batch:
                    cursor.executemany(
                        """INSERT INTO job_postings_india
                        (source, job_id, title, title_normalized, company, location, city, soc_code_mapped,
                         salary_min_inr, salary_max_inr, salary_min_usd, salary_max_usd,
                         experience_min, experience_max, skills, industry,
                         listed_date, listed_year, listed_month, region)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        batch
                    )
            total += count
            print(f"    Loaded {count} India postings from {naukri_sample.name}")
        except Exception as e:
            print(f"    [WARN] Error reading {naukri_sample.name}: {e}")

    # Source 2: Supplementary tech & data science JSONL
    for jsonl_name in ["naukri_software_engineer.jsonl", "naukri_data_scientist.jsonl"]:
        jsonl_path = RAW_DIR / jsonl_name
        if jsonl_path.exists():
            print(f"  Loading India tech postings from {jsonl_name} ...")
            count = 0
            limit = 20000
            batch = []
            try:
                with open(jsonl_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        if count >= limit:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        title = (d.get('title') or '').strip()
                        if not title:
                            continue
                        company = (d.get('companyName') or '').strip()
                        location = (d.get('location') or '').strip()
                        city = location.split(',')[0].strip() if location else 'India'

                        sal_min_inr, sal_max_inr = parse_salary_inr(d.get('salary', ''))
                        sal_min_usd = round(sal_min_inr * INR_TO_USD, 2) if sal_min_inr else None
                        sal_max_usd = round(sal_max_inr * INR_TO_USD, 2) if sal_max_inr else None

                        exp_min, exp_max = _parse_experience(d.get('experience', ''))
                        skills = d.get('tagsAndSkills') or ''

                        batch.append((
                            'naukri_tech_hub', str(d.get('jobId', '')),
                            title, normalize_title(title), company, location, city, None,
                            sal_min_inr, sal_max_inr, sal_min_usd, sal_max_usd,
                            exp_min, exp_max, skills, 'IT / Software',
                            str(d.get('createdDate', '')), 2024, 6, 'india'
                        ))
                        count += 1

                        if len(batch) >= 5000:
                            cursor.executemany(
                                """INSERT INTO job_postings_india
                                (source, job_id, title, title_normalized, company, location, city, soc_code_mapped,
                                 salary_min_inr, salary_max_inr, salary_min_usd, salary_max_usd,
                                 experience_min, experience_max, skills, industry,
                                 listed_date, listed_year, listed_month, region)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                batch
                            )
                            batch = []

                if batch:
                    cursor.executemany(
                        """INSERT INTO job_postings_india
                        (source, job_id, title, title_normalized, company, location, city, soc_code_mapped,
                         salary_min_inr, salary_max_inr, salary_min_usd, salary_max_usd,
                         experience_min, experience_max, skills, industry,
                         listed_date, listed_year, listed_month, region)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        batch
                    )
                total += count
                print(f"    Loaded {count} India tech postings from {jsonl_name}")
            except Exception as e:
                print(f"    [WARN] Error reading {jsonl_name}: {e}")

    # Source 3: Kaggle India Tech Jobs 2024-2026 (sridipbasu)
    india_tech_csv = RAW_DIR / "india_tech_2026" / "india_job_market_2024_2026.csv"
    if india_tech_csv.exists():
        print(f"  Loading Kaggle India 2024-2026 postings from {india_tech_csv.name} ...")
        count = 0
        try:
            with open(india_tech_csv, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f)
                batch = []
                for row in reader:
                    title = (row.get('Job_Title') or '').strip()
                    if not title:
                        continue
                    company = (row.get('Company') or '').strip()
                    city = (row.get('City') or '').strip()
                    loc = f"{city}, India" if city else "India"

                    lpa = _safe_float(row.get('Salary_LPA'))
                    sal_inr = lpa * 100000 if lpa else None
                    sal_usd = round(sal_inr * INR_TO_USD, 2) if sal_inr else None

                    exp_min, exp_max = _parse_experience(row.get('Experience_Level', ''))
                    skills = row.get('Skills_Required', '')
                    industry = row.get('Industry', '')
                    date_posted = row.get('Date_Posted', '')
                    year, month = _parse_date_to_ym(date_posted)
                    if not year:
                        year = 2025

                    batch.append((
                        'kaggle_india_tech_2024_2026', str(row.get('Job_ID', '')),
                        title, normalize_title(title), company, loc, city, None,
                        sal_inr, sal_inr, sal_usd, sal_usd,
                        exp_min, exp_max, skills, industry,
                        date_posted, year, month, 'india'
                    ))
                    count += 1

                    if len(batch) >= 5000:
                        cursor.executemany(
                            """INSERT INTO job_postings_india
                            (source, job_id, title, title_normalized, company, location, city, soc_code_mapped,
                             salary_min_inr, salary_max_inr, salary_min_usd, salary_max_usd,
                             experience_min, experience_max, skills, industry,
                             listed_date, listed_year, listed_month, region)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            batch
                        )
                        batch = []

                if batch:
                    cursor.executemany(
                        """INSERT INTO job_postings_india
                        (source, job_id, title, title_normalized, company, location, city, soc_code_mapped,
                         salary_min_inr, salary_max_inr, salary_min_usd, salary_max_usd,
                         experience_min, experience_max, skills, industry,
                         listed_date, listed_year, listed_month, region)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        batch
                    )
            total += count
            print(f"    Loaded {count} India postings from {india_tech_csv.name}")
        except Exception as e:
            print(f"    [WARN] Error reading {india_tech_csv.name}: {e}")

    # Source 4: Kaggle India Data Science Jobs (anandhuh)
    india_ds_csv = RAW_DIR / "india_ds" / "naukri_data_science_jobs_india.csv"
    if india_ds_csv.exists():
        print(f"  Loading Kaggle India Data Science postings from {india_ds_csv.name} ...")
        count = 0
        try:
            with open(india_ds_csv, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f)
                batch = []
                for idx, row in enumerate(reader):
                    title = (row.get('Job_Role') or '').strip()
                    if not title:
                        continue
                    company = (row.get('Company') or '').strip()
                    loc = (row.get('Location') or '').strip()
                    city = loc.split('/')[0].split(',')[0].strip() if loc else 'India'

                    exp_min, exp_max = _parse_experience(row.get('Job Experience', ''))
                    skills = row.get('Skills/Description', '')

                    batch.append((
                        'kaggle_india_data_science', f"DS_IND_{idx}",
                        title, normalize_title(title), company, loc, city, None,
                        None, None, None, None,
                        exp_min, exp_max, skills, 'Data Science / Analytics',
                        '2024-01-01', 2024, 1, 'india'
                    ))
                    count += 1

                    if len(batch) >= 5000:
                        cursor.executemany(
                            """INSERT INTO job_postings_india
                            (source, job_id, title, title_normalized, company, location, city, soc_code_mapped,
                             salary_min_inr, salary_max_inr, salary_min_usd, salary_max_usd,
                             experience_min, experience_max, skills, industry,
                             listed_date, listed_year, listed_month, region)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            batch
                        )
                        batch = []

                if batch:
                    cursor.executemany(
                        """INSERT INTO job_postings_india
                        (source, job_id, title, title_normalized, company, location, city, soc_code_mapped,
                         salary_min_inr, salary_max_inr, salary_min_usd, salary_max_usd,
                         experience_min, experience_max, skills, industry,
                         listed_date, listed_year, listed_month, region)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        batch
                    )
            total += count
            print(f"    Loaded {count} India postings from {india_ds_csv.name}")
        except Exception as e:
            print(f"    [WARN] Error reading {india_ds_csv.name}: {e}")

    conn.commit()
    return total


def build_salary_benchmarks(conn: sqlite3.Connection) -> int:
    """Build unified multi-region salary benchmarks from India and Global postings."""
    print("  Populating salary benchmarks table ...")
    cursor = conn.cursor()
    total = 0

    # From Global postings
    cursor.execute("""
        INSERT INTO salary_benchmarks
        (source, title, title_normalized, soc_code_mapped, region, country, salary_usd, experience_level, employment_type, remote_ratio, company_size, year)
        SELECT
            source, title, title_normalized, soc_code_mapped, 'global', country,
            COALESCE(salary_median_usd, (salary_min_usd + salary_max_usd) / 2.0, salary_min_usd),
            experience_level, employment_type, remote_ratio, NULL, listed_year
        FROM job_postings_global
        WHERE (salary_min_usd IS NOT NULL OR salary_median_usd IS NOT NULL)
          AND COALESCE(salary_median_usd, salary_min_usd) > 5000
    """)
    global_salaries = cursor.rowcount
    total += max(global_salaries, 0)

    # From Kaggle Global AI Salary Dataset
    ai_csv = RAW_DIR / "ai_salary_2025" / "ai_job_dataset.csv"
    ai_count = 0
    if ai_csv.exists():
        try:
            with open(ai_csv, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f)
                batch_sal = []
                batch_postings = []
                for row in reader:
                    title = (row.get('job_title') or '').strip()
                    if not title:
                        continue
                    sal = _safe_float(row.get('salary_usd'))
                    exp = (row.get('experience_level') or '').strip()
                    emp = (row.get('employment_type') or '').strip()
                    loc = (row.get('company_location') or '').strip()
                    size = (row.get('company_size') or '').strip()
                    remote = _safe_float(row.get('remote_ratio'))
                    skills = row.get('required_skills', '')
                    company = (row.get('company_name') or '').strip()
                    date_p = row.get('posting_date', '')
                    year, month = _parse_date_to_ym(date_p)
                    if not year:
                        year = 2025

                    reg = 'india' if 'india' in loc.lower() else 'global'

                    batch_sal.append((
                        'kaggle_ai_salary_2025', title, normalize_title(title), None, reg, loc,
                        sal, exp, emp, remote, size, year
                    ))

                    batch_postings.append((
                        'kaggle_ai_jobs_2025', str(row.get('job_id', '')), title, normalize_title(title),
                        company, loc, loc, None, sal, sal, sal, exp, emp, remote,
                        skills, date_p, year, month, reg
                    ))
                    ai_count += 1

                if batch_sal:
                    cursor.executemany(
                        """INSERT INTO salary_benchmarks
                        (source, title, title_normalized, soc_code_mapped, region, country,
                         salary_usd, experience_level, employment_type, remote_ratio, company_size, year)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        batch_sal
                    )
                if batch_postings:
                    cursor.executemany(
                        """INSERT INTO job_postings_global
                        (source, job_id, title, title_normalized, company, location, country, soc_code_mapped,
                         salary_min_usd, salary_max_usd, salary_median_usd, experience_level, employment_type, remote_ratio,
                         skills, listed_date, listed_year, listed_month, region)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        batch_postings
                    )
        except Exception as e:
            print(f"    [WARN] Error reading {ai_csv.name}: {e}")

    # From India postings
    cursor.execute("""
        INSERT INTO salary_benchmarks
        (source, title, title_normalized, soc_code_mapped, region, country, salary_usd, experience_level, employment_type, remote_ratio, company_size, year)
        SELECT
            source, title, title_normalized, soc_code_mapped, 'india', 'India',
            COALESCE((salary_min_usd + salary_max_usd) / 2.0, salary_min_usd, salary_max_usd),
            CASE
                WHEN experience_min >= 8 THEN 'Senior'
                WHEN experience_min >= 3 THEN 'Mid-Level'
                WHEN experience_min IS NOT NULL THEN 'Entry-Level'
                ELSE NULL
            END,
            'Full-time', NULL, NULL, listed_year
        FROM job_postings_india
        WHERE (salary_min_usd IS NOT NULL OR salary_max_usd IS NOT NULL)
          AND COALESCE(salary_min_usd, salary_max_usd) > 500
    """)
    india_salaries = cursor.rowcount
    total += max(india_salaries, 0)

    total += ai_count
    conn.commit()
    print(f"    Populated {total} salary benchmark records (Global: {global_salaries}, India: {india_salaries}, Kaggle AI: {ai_count})")
    return total


def extract_skill_demand(conn: sqlite3.Connection) -> int:
    """Extract and aggregate skill demand from both India and Global job postings."""
    cursor = conn.cursor()
    total = 0

    # Global skills
    print("  Extracting global skill demand ...")
    cursor.execute("SELECT skills, listed_year, listed_month, title_normalized FROM job_postings_global WHERE skills IS NOT NULL AND skills != '' LIMIT 60000")
    batch = []
    for row in cursor.fetchall():
        skills_raw, year, month, title = row
        for skill in _split_skills(skills_raw):
            ns = normalize_skill(skill)
            if ns and (len(ns) > 1 or ns in ('c', 'r')) and len(ns) < 50:
                batch.append((skill, ns, 'global', 'linkedin', year or 2024, month or 1, 1, title))
                total += 1
        if len(batch) >= 10000:
            cursor.executemany(
                "INSERT INTO skill_demand (skill_name, skill_normalized, region, source, year, month, frequency, occupation_context) VALUES (?,?,?,?,?,?,?,?)",
                batch
            )
            batch = []
    if batch:
        cursor.executemany(
            "INSERT INTO skill_demand (skill_name, skill_normalized, region, source, year, month, frequency, occupation_context) VALUES (?,?,?,?,?,?,?,?)",
            batch
        )

    # India skills
    print("  Extracting India skill demand ...")
    cursor.execute("SELECT skills, listed_year, listed_month, title_normalized FROM job_postings_india WHERE skills IS NOT NULL AND skills != '' LIMIT 60000")
    batch = []
    for row in cursor.fetchall():
        skills_raw, year, month, title = row
        for skill in _split_skills(skills_raw):
            ns = normalize_skill(skill)
            if ns and (len(ns) > 1 or ns in ('c', 'r')) and len(ns) < 50:
                batch.append((skill, ns, 'india', 'naukri', year or 2024, month or 1, 1, title))
                total += 1
        if len(batch) >= 10000:
            cursor.executemany(
                "INSERT INTO skill_demand (skill_name, skill_normalized, region, source, year, month, frequency, occupation_context) VALUES (?,?,?,?,?,?,?,?)",
                batch
            )
            batch = []
    if batch:
        cursor.executemany(
            "INSERT INTO skill_demand (skill_name, skill_normalized, region, source, year, month, frequency, occupation_context) VALUES (?,?,?,?,?,?,?,?)",
            batch
        )

    conn.commit()
    print(f"    Extracted {total} skill demand records")
    return total


# -------------------------------------------------------------------------
# Helper Utilities
# -------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        v = float(str(val).replace(',', '').replace('$', '').replace('₹', '').strip())
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_date_to_ym(date_str: str) -> Tuple[Optional[int], Optional[int]]:
    """Extract year and month from various date formats."""
    if not date_str or not str(date_str).strip():
        return None, None
    s = str(date_str).strip()

    # Unix timestamp (milliseconds)
    if s.isdigit() and len(s) >= 10:
        try:
            ts = int(s)
            if ts > 1e12:
                ts = ts / 1000
            dt = datetime.fromtimestamp(ts)
            return dt.year, dt.month
        except (ValueError, OSError):
            pass

    # ISO format: 2024-01-15
    match = re.match(r'(\d{4})-(\d{1,2})', s)
    if match:
        return int(match.group(1)), int(match.group(2))

    # DD/MM/YYYY or MM/DD/YYYY
    match2 = re.match(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', s)
    if match2:
        return int(match2.group(3)), int(match2.group(2))

    return None, None


def _parse_experience(exp_str: str) -> Tuple[Optional[float], Optional[float]]:
    """Parse experience strings like '3-6 Yrs', '2+ years'."""
    if not exp_str or str(exp_str).strip() in ('', 'nan'):
        return None, None
    s = str(exp_str)
    match = re.search(r'(\d+\.?\d*)\s*[-–to]+\s*(\d+\.?\d*)', s)
    if match:
        try:
            return float(match.group(1)), float(match.group(2))
        except ValueError:
            pass
    match2 = re.search(r'(\d+\.?\d*)\+?', s)
    if match2:
        try:
            return float(match2.group(1)), None
        except ValueError:
            pass
    return None, None


def _guess_country(location: str) -> str:
    """Rough country detection from location string."""
    if not location:
        return 'Unknown'
    loc = location.lower()
    india_markers = ['india', 'mumbai', 'bangalore', 'bengaluru', 'delhi', 'hyderabad',
                     'pune', 'chennai', 'kolkata', 'noida', 'gurgaon', 'gurugram']
    if any(m in loc for m in india_markers):
        return 'India'
    if 'united states' in loc or ', us' in loc or any(st in loc for st in [', ca', ', ny', ', tx', ', wa']):
        return 'United States'
    if 'united kingdom' in loc or ', uk' in loc or 'london' in loc:
        return 'United Kingdom'
    if 'germany' in loc or 'berlin' in loc or 'munich' in loc:
        return 'Germany'
    if 'canada' in loc or 'toronto' in loc or 'vancouver' in loc:
        return 'Canada'
    return 'Other'


def _split_skills(skills_raw: str) -> List[str]:
    """Split skill strings by common delimiters."""
    if not skills_raw or str(skills_raw).strip() in ('', 'nan'):
        return []
    s = str(skills_raw)
    # Handle JSON-like arrays
    if s.startswith('['):
        try:
            parsed = json.loads(s.replace("'", '"'))
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if x]
        except (json.JSONDecodeError, ValueError):
            pass
    # Common delimiters
    for delim in [',', '|', ';', '\n']:
        if delim in s:
            return [x.strip() for x in s.split(delim) if x.strip()]
    return [s.strip()] if s.strip() else []


# -------------------------------------------------------------------------
# Main ETL Orchestrator
# -------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  MODULE 1: ETL PIPELINE -- Clean, Transform & Load")
    print("  Target Database: career_intel.db")
    print("=" * 70)

    if not RAW_DIR.exists():
        print(f"\n  [ERROR] Raw data directory not found: {RAW_DIR}")
        print(f"  Run 01_download_raw.py first.")
        sys.exit(1)

    # Create/reset database
    if DB_PATH.exists():
        print(f"\n  [INFO] Removing existing database: {DB_PATH.name}")
        DB_PATH.unlink()

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache

    print("\n  Creating schema ...")
    conn.executescript(SCHEMA_SQL)

    results = {}

    # 1. O*NET Taxonomy
    print("\n[1/5] Loading O*NET 31.0 Taxonomy ...")
    results["O*NET Taxonomy"] = load_onet(conn)

    # 2. Global LinkedIn Postings
    print("\n[2/5] Loading Global Job Postings (LinkedIn) ...")
    results["Global Postings"] = load_linkedin_global(conn)

    # 3. India Job Postings
    print("\n[3/5] Loading India Job Postings (Naukri) ...")
    results["India Postings"] = load_india_jobs(conn)

    # 4. Multi-Region Salary Benchmarks
    print("\n[4/5] Building Salary Benchmarks ...")
    results["Salary Benchmarks"] = build_salary_benchmarks(conn)

    # 5. Skill Demand Extraction
    print("\n[5/5] Extracting Skill Demand Trends ...")
    results["Skill Demand"] = extract_skill_demand(conn)

    conn.close()

    # ---------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  ETL PIPELINE SUMMARY")
    print("=" * 70)
    for stage, count in results.items():
        status = "[DONE]" if count > 0 else "[WARN]"
        print(f"  {status}  {stage}: {count:,} records loaded")

    db_size = DB_PATH.stat().st_size / (1024 * 1024) if DB_PATH.exists() else 0
    print(f"\n  Database: {DB_PATH}")
    print(f"  Size: {db_size:.1f} MB")

    # Quick verification queries
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    print("\n  Verification Queries:")
    for table in ['occupations', 'occupation_tasks', 'occupation_skills', 'occupation_tech',
                  'job_postings_global', 'job_postings_india', 'salary_benchmarks', 'skill_demand']:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"    {table}: {count:,} rows")

    # India vs Global comparison
    print("\n  India vs Global Posting Distribution:")
    cursor.execute("SELECT region, year, total_postings FROM v_india_vs_global")
    for row in cursor.fetchall():
        print(f"    Region: {row[0]}, Year: {row[1]}, Postings: {row[2]:,}")

    conn.close()
    print("\n  [SUCCESS] ETL pipeline complete!")


if __name__ == "__main__":
    main()
