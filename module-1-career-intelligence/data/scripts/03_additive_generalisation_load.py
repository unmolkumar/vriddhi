"""
Additive Generalisation ETL: Loads full O*NET 31.0 tables, Indian title aliases,
cross-table SOC mapping, salary quality flags, clean skill demand views, and
unified requirements view into career_intel.db.

GROUND RULE: ADDITIVE ONLY.
Does not drop or alter any existing tables/columns.
"""
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

BASE_DIR = Path(__file__).resolve().parent.parent  # data/
RAW_DIR = BASE_DIR / "raw"
ONET_DIR = RAW_DIR / "onet" / "db_31_0_csv"
DB_PATH = BASE_DIR / "career_intel.db"

# -------------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------------

def clean_title_for_matching(t: str) -> str:
    if not t:
        return ""
    t = t.lower().strip()
    t = re.sub(r'\s*[\(\[].*?[\)\]]', '', t)
    t = re.sub(r'\s*(sr\.?|jr\.?|senior|junior|lead|principal|staff|chief|head|associate|assistant)\s*', ' ', t)
    t = re.sub(r'\s*(i|ii|iii|iv|v|1|2|3)\s*$', '', t)
    t = re.sub(r'[^a-z0-9\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def parse_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None and str(val).strip() != '' else default
    except (ValueError, TypeError):
        return default


def parse_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(float(val)) if val is not None and str(val).strip() != '' else default
    except (ValueError, TypeError):
        return default


# -------------------------------------------------------------------------
# 1. Full O*NET Ingestion (P0.1)
# -------------------------------------------------------------------------

def load_onet_skills(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            element_id TEXT NOT NULL,
            element_name TEXT NOT NULL,
            importance REAL NOT NULL,
            level REAL NOT NULL,
            importance_norm REAL NOT NULL,
            level_norm REAL NOT NULL,
            n INTEGER,
            recommend_suppress TEXT DEFAULT 'N',
            not_relevant TEXT DEFAULT 'N',
            UNIQUE(soc_code, element_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_skills_soc ON onet_skills(soc_code)")

    data = defaultdict(dict)
    for fname in ['essential_skills.csv', 'transferable_skills.csv']:
        path = ONET_DIR / fname
        if not path.exists():
            continue
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                soc = r.get('O*NET-SOC Code', '').strip()
                eid = r.get('Element ID', '').strip()
                ename = r.get('Element Name', '').strip()
                scale = r.get('Scale ID', '').strip()
                val = parse_float(r.get('Data Value'))
                if not soc or not eid:
                    continue
                k = (soc, eid)
                data[k]['element_name'] = ename
                data[k][scale] = val
                if r.get('N'):
                    data[k]['n'] = parse_int(r['N'])
                if r.get('Recommend Suppress') == 'Y':
                    data[k]['suppress'] = 'Y'
                if r.get('Not Relevant') == 'Y':
                    data[k]['not_relevant'] = 'Y'

    rows = []
    for (soc, eid), info in data.items():
        imp = info.get('IM', 1.0)
        lvl = info.get('LV', 0.0)
        imp_norm = round(max(0.0, min(1.0, (imp - 1.0) / 4.0)), 4)
        lvl_norm = round(max(0.0, min(1.0, lvl / 7.0)), 4)
        rows.append((
            soc, eid, info['element_name'], imp, lvl, imp_norm, lvl_norm,
            info.get('n'), info.get('suppress', 'N'), info.get('not_relevant', 'N')
        ))

    cur.executemany("""
        INSERT OR REPLACE INTO onet_skills 
        (soc_code, element_id, element_name, importance, level, importance_norm, level_norm, n, recommend_suppress, not_relevant)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"  [onet_skills] {len(rows):,} rows ({time.time()-t0:.2f}s)")


def load_onet_element_table(conn: sqlite3.Connection, table_name: str, csv_name: str):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            element_id TEXT NOT NULL,
            element_name TEXT NOT NULL,
            importance REAL NOT NULL,
            level REAL NOT NULL,
            importance_norm REAL NOT NULL,
            level_norm REAL NOT NULL,
            n INTEGER,
            recommend_suppress TEXT DEFAULT 'N',
            not_relevant TEXT DEFAULT 'N',
            UNIQUE(soc_code, element_id)
        )
    """)
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_soc ON {table_name}(soc_code)")

    path = ONET_DIR / csv_name
    if not path.exists():
        print(f"  [SKIP] {csv_name} not found")
        return

    data = defaultdict(dict)
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for r in csv.DictReader(f):
            soc = r.get('O*NET-SOC Code', '').strip()
            eid = r.get('Element ID', '').strip()
            ename = r.get('Element Name', '').strip()
            scale = r.get('Scale ID', '').strip()
            val = parse_float(r.get('Data Value'))
            if not soc or not eid:
                continue
            k = (soc, eid)
            data[k]['element_name'] = ename
            data[k][scale] = val
            if r.get('N'):
                data[k]['n'] = parse_int(r['N'])
            if r.get('Recommend Suppress') == 'Y':
                data[k]['suppress'] = 'Y'
            if r.get('Not Relevant') == 'Y':
                data[k]['not_relevant'] = 'Y'

    rows = []
    for (soc, eid), info in data.items():
        imp = info.get('IM', 1.0)
        lvl = info.get('LV', 0.0)
        imp_norm = round(max(0.0, min(1.0, (imp - 1.0) / 4.0)), 4)
        lvl_norm = round(max(0.0, min(1.0, lvl / 7.0)), 4)
        rows.append((
            soc, eid, info['element_name'], imp, lvl, imp_norm, lvl_norm,
            info.get('n'), info.get('suppress', 'N'), info.get('not_relevant', 'N')
        ))

    cur.executemany(f"""
        INSERT OR REPLACE INTO {table_name} 
        (soc_code, element_id, element_name, importance, level, importance_norm, level_norm, n, recommend_suppress, not_relevant)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"  [{table_name}] {len(rows):,} rows ({time.time()-t0:.2f}s)")


def load_onet_task_ratings(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_task_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            task_id TEXT NOT NULL,
            task_description TEXT NOT NULL,
            importance REAL,
            relevance REAL,
            frequency REAL,
            UNIQUE(soc_code, task_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_task_ratings_soc ON onet_task_ratings(soc_code)")

    path = ONET_DIR / "task_ratings.csv"
    if not path.exists():
        return

    data = defaultdict(dict)
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for r in csv.DictReader(f):
            soc = r.get('O*NET-SOC Code', '').strip()
            tid = r.get('Task ID', '').strip()
            task = r.get('Task', '').strip()
            scale = r.get('Scale ID', '').strip()
            cat = r.get('Category', '').strip()
            val = parse_float(r.get('Data Value'))
            if not soc or not tid:
                continue
            k = (soc, tid)
            data[k]['task'] = task
            if scale == 'IM':
                data[k]['importance'] = val
            elif scale == 'RT':
                data[k]['relevance'] = val
            elif scale == 'FT':
                if 'ft_sum' not in data[k]:
                    data[k]['ft_sum'] = 0.0
                cat_num = parse_float(cat, 1.0)
                data[k]['ft_sum'] += cat_num * (val / 100.0)

    rows = []
    for (soc, tid), info in data.items():
        rows.append((
            soc, tid, info['task'],
            info.get('importance'),
            info.get('relevance'),
            info.get('ft_sum')
        ))

    cur.executemany("""
        INSERT OR REPLACE INTO onet_task_ratings
        (soc_code, task_id, task_description, importance, relevance, frequency)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"  [onet_task_ratings] {len(rows):,} rows ({time.time()-t0:.2f}s)")


def load_onet_dwa(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_dwa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            task_id TEXT NOT NULL,
            dwa_id TEXT NOT NULL,
            dwa_title TEXT NOT NULL,
            iwa_id TEXT,
            iwa_title TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_dwa_soc ON onet_dwa(soc_code)")

    iwa_map = {}
    path_iwa = ONET_DIR / "gwas_to_iwas_to_dwas.csv"
    if path_iwa.exists():
        with open(path_iwa, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                dwa_id = r.get('DWA Element ID', '').strip()
                if dwa_id:
                    iwa_map[dwa_id] = (r.get('IWA Element ID', '').strip(), r.get('IWA Element Name', '').strip())

    path_tasks = ONET_DIR / "tasks_to_dwas.csv"
    if not path_tasks.exists():
        return

    rows = []
    with open(path_tasks, 'r', encoding='utf-8-sig', errors='replace') as f:
        for r in csv.DictReader(f):
            soc = r.get('O*NET-SOC Code', '').strip()
            tid = r.get('Task ID', '').strip()
            dwa_id = r.get('DWA Element ID', '').strip()
            dwa_name = r.get('DWA Element Name', '').strip()
            if soc and tid and dwa_id:
                iwa_id, iwa_name = iwa_map.get(dwa_id, (None, None))
                rows.append((soc, tid, dwa_id, dwa_name, iwa_id, iwa_name))

    cur.executemany("""
        INSERT INTO onet_dwa (soc_code, task_id, dwa_id, dwa_title, iwa_id, iwa_title)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"  [onet_dwa] {len(rows):,} rows ({time.time()-t0:.2f}s)")


def load_onet_tech_and_tools(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_tech_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            example TEXT NOT NULL,
            commodity_code TEXT,
            commodity_title TEXT,
            hot_technology INTEGER DEFAULT 0,
            in_demand INTEGER DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_tech_soc ON onet_tech_skills(soc_code)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_tools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            example TEXT NOT NULL,
            commodity_code TEXT,
            commodity_title TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_tools_soc ON onet_tools(soc_code)")

    path = ONET_DIR / "software_skills.csv"
    if not path.exists():
        return

    tech_rows = []
    tool_rows = []
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for r in csv.DictReader(f):
            soc = r.get('O*NET-SOC Code', '').strip()
            ex = (r.get('Workplace Example') or r.get('Example') or '').strip()
            cid = r.get('Element ID', '').strip()
            ctitle = r.get('Element Name', '').strip()
            hot = 1 if str(r.get('Hot Technology', '')).strip().upper() == 'Y' else 0
            indem = 1 if str(r.get('In Demand', '')).strip().upper() == 'Y' else 0
            if soc and ex:
                tech_rows.append((soc, ex, cid, ctitle, hot, indem))
                if any(w in ex.lower() for w in ['pliers', 'scanner', 'printer', 'meter', 'welder', 'tester', 'kit', 'oscilloscope', 'wrench', 'drill', 'saw']):
                    tool_rows.append((soc, ex, cid, ctitle))

    cur.executemany("""
        INSERT INTO onet_tech_skills (soc_code, example, commodity_code, commodity_title, hot_technology, in_demand)
        VALUES (?, ?, ?, ?, ?, ?)
    """, tech_rows)

    cur.executemany("""
        INSERT INTO onet_tools (soc_code, example, commodity_code, commodity_title)
        VALUES (?, ?, ?, ?)
    """, tool_rows)

    conn.commit()
    print(f"  [onet_tech_skills / onet_tools] {len(tech_rows):,} tech, {len(tool_rows):,} tools ({time.time()-t0:.2f}s)")


def load_onet_job_zones(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_job_zones (
            soc_code TEXT PRIMARY KEY,
            job_zone INTEGER NOT NULL,
            name TEXT,
            experience_text TEXT,
            education_text TEXT,
            training_text TEXT,
            svp_range TEXT
        )
    """)

    ref_map = {}
    path_ref = ONET_DIR / "job_zone_reference.csv"
    if path_ref.exists():
        with open(path_ref, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                jz = parse_int(r.get('Job Zone'))
                if jz is not None:
                    ref_map[jz] = {
                        'name': r.get('Name', ''),
                        'exp': r.get('Experience', ''),
                        'edu': r.get('Education', ''),
                        'train': r.get('Job Training', ''),
                        'svp': r.get('SVP Range', '')
                    }

    path_jz = ONET_DIR / "job_zones.csv"
    if not path_jz.exists():
        return

    rows = []
    with open(path_jz, 'r', encoding='utf-8-sig', errors='replace') as f:
        for r in csv.DictReader(f):
            soc = r.get('O*NET-SOC Code', '').strip()
            jz = parse_int(r.get('Job Zone'))
            if soc and jz is not None:
                info = ref_map.get(jz, {})
                rows.append((
                    soc, jz, info.get('name'), info.get('exp'),
                    info.get('edu'), info.get('train'), info.get('svp')
                ))

    cur.executemany("""
        INSERT OR REPLACE INTO onet_job_zones
        (soc_code, job_zone, name, experience_text, education_text, training_text, svp_range)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"  [onet_job_zones] {len(rows):,} rows ({time.time()-t0:.2f}s)")


def load_onet_education(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_education (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            element_id TEXT NOT NULL,
            element_name TEXT NOT NULL,
            scale_id TEXT,
            category INTEGER,
            category_description TEXT,
            percent REAL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_edu_soc ON onet_education(soc_code)")

    cat_map = {}
    path_cat = ONET_DIR / "education_categories.csv"
    if path_cat.exists():
        with open(path_cat, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                eid = r.get('Element ID', '').strip()
                cat = parse_int(r.get('Category'))
                desc = r.get('Category Description', '').strip()
                if eid and cat is not None:
                    cat_map[(eid, cat)] = desc

    path_edu = ONET_DIR / "education.csv"
    if not path_edu.exists():
        return

    rows = []
    with open(path_edu, 'r', encoding='utf-8-sig', errors='replace') as f:
        for r in csv.DictReader(f):
            soc = r.get('O*NET-SOC Code', '').strip()
            eid = r.get('Element ID', '').strip()
            ename = r.get('Element Name', '').strip()
            scale = r.get('Scale ID', '').strip()
            cat = parse_int(r.get('Category'))
            val = parse_float(r.get('Data Value'))
            desc = cat_map.get((eid, cat), '')
            if soc and eid:
                rows.append((soc, eid, ename, scale, cat, desc, val))

    cur.executemany("""
        INSERT INTO onet_education
        (soc_code, element_id, element_name, scale_id, category, category_description, percent)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"  [onet_education] {len(rows):,} rows ({time.time()-t0:.2f}s)")


def load_onet_metadata_and_titles(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_alternate_titles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            title TEXT NOT NULL,
            title_normalized TEXT NOT NULL,
            short_title TEXT,
            source TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_alt_titles_soc ON onet_alternate_titles(soc_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_alt_titles_norm ON onet_alternate_titles(title_normalized)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_related_occupations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            related_soc_code TEXT NOT NULL,
            related_title TEXT NOT NULL,
            relatedness_tier INTEGER,
            index_val INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_related_soc ON onet_related_occupations(soc_code)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_content_model (
            element_id TEXT PRIMARY KEY,
            element_name TEXT NOT NULL,
            description TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS onet_interests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            element_id TEXT NOT NULL,
            interest_name TEXT NOT NULL,
            score REAL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_onet_interests_soc ON onet_interests(soc_code)")

    alt_rows = []
    path_titles = ONET_DIR / "job_titles.csv"
    if path_titles.exists():
        with open(path_titles, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                soc = r.get('O*NET-SOC Code', '').strip()
                t = r.get('Job Title', '').strip()
                st = r.get('Short Title', '').strip()
                src = r.get('Source(s)', '').strip()
                if soc and t:
                    alt_rows.append((soc, t, clean_title_for_matching(t), st, f"job_titles_{src}"))

    path_rep = ONET_DIR / "sample_of_reported_titles.csv"
    if path_rep.exists():
        with open(path_rep, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                soc = r.get('O*NET-SOC Code', '').strip()
                t = r.get('Reported Job Title', '').strip()
                if soc and t:
                    alt_rows.append((soc, t, clean_title_for_matching(t), '', 'sample_reported'))

    cur.executemany("""
        INSERT INTO onet_alternate_titles (soc_code, title, title_normalized, short_title, source)
        VALUES (?, ?, ?, ?, ?)
    """, alt_rows)

    rel_rows = []
    path_rel = ONET_DIR / "related_occupations.csv"
    if path_rel.exists():
        with open(path_rel, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                soc = r.get('O*NET-SOC Code', '').strip()
                rsoc = r.get('Related O*NET-SOC Code', '').strip()
                rt = r.get('Related Title', '').strip()
                tier = parse_int(r.get('Relatedness Tier'))
                idx = parse_int(r.get('Index'))
                if soc and rsoc:
                    rel_rows.append((soc, rsoc, rt, tier, idx))

    cur.executemany("""
        INSERT INTO onet_related_occupations (soc_code, related_soc_code, related_title, relatedness_tier, index_val)
        VALUES (?, ?, ?, ?, ?)
    """, rel_rows)

    cm_rows = []
    path_cm = ONET_DIR / "content_model_reference.csv"
    if path_cm.exists():
        with open(path_cm, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                eid = r.get('Element ID', '').strip()
                ename = r.get('Element Name', '').strip()
                desc = r.get('Description', '').strip()
                if eid and ename:
                    cm_rows.append((eid, ename, desc))

    cur.executemany("""
        INSERT OR REPLACE INTO onet_content_model (element_id, element_name, description)
        VALUES (?, ?, ?)
    """, cm_rows)

    int_rows = []
    path_int = ONET_DIR / "career_interest_types.csv"
    if path_int.exists():
        with open(path_int, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                soc = r.get('O*NET-SOC Code', '').strip()
                eid = r.get('Element ID', '').strip()
                ename = r.get('Element Name', '').strip()
                val = parse_float(r.get('Data Value'))
                if soc and eid:
                    int_rows.append((soc, eid, ename, val))

    cur.executemany("""
        INSERT INTO onet_interests (soc_code, element_id, interest_name, score)
        VALUES (?, ?, ?, ?)
    """, int_rows)

    conn.commit()
    print(f"  [onet_alternate_titles / related / content] {len(alt_rows):,} titles, {len(rel_rows):,} related ({time.time()-t0:.2f}s)")


# -------------------------------------------------------------------------
# 2. Occupation Domains & City Aliases (P0.2, P0.6)
# -------------------------------------------------------------------------

SOC_MAJOR_GROUPS = {
    "11": ("Management Occupations", "Leadership & Management", "Corporate / IT / Operations"),
    "13": ("Business and Financial Operations", "Finance & Banking", "Banking / Financial Services / Broking"),
    "15": ("Computer and Mathematical", "Information Technology", "IT-Software / Software Services"),
    "17": ("Architecture and Engineering", "Engineering & Design", "Engineering / Construction / Design"),
    "19": ("Life, Physical, and Social Science", "Sciences & Research", "Pharma / Biotech / Clinical Research"),
    "21": ("Community and Social Service", "Community Services", "NGO / Social Services"),
    "23": ("Legal Occupations", "Legal & Regulatory", "Legal / Law Firm"),
    "25": ("Educational Instruction and Library", "Education & Training", "Education / Teaching / Training"),
    "27": ("Arts, Design, Entertainment, Sports, and Media", "Creative & Media", "Media / Entertainment / Advertising"),
    "29": ("Healthcare Practitioners and Technical", "Healthcare & Medicine", "Medical / Healthcare / Hospital"),
    "31": ("Healthcare Support Occupations", "Healthcare Support", "Medical / Healthcare / Diagnostics"),
    "33": ("Protective Service Occupations", "Security & Protection", "Security / Law Enforcement"),
    "35": ("Food Preparation and Serving Related", "Hospitality & Food", "Hotels / Restaurants / Airlines / Travel"),
    "37": ("Building and Grounds Cleaning and Maintenance", "Facilities & Maintenance", "Facility Management"),
    "39": ("Personal Care and Service", "Personal Services & Wellness", "Wellness / Fitness / Beauty"),
    "41": ("Sales and Related Occupations", "Sales & Marketing", "Sales / Retail / Business Development"),
    "43": ("Office and Administrative Support", "Administration & Support", "BPO / Call Centre / ITES / Admin"),
    "45": ("Farming, Fishing, and Forestry", "Agriculture & Natural Resources", "Agriculture / Dairy"),
    "47": ("Construction and Extraction", "Construction & Infrastructure", "Construction / Engineering / Real Estate"),
    "49": ("Installation, Maintenance, and Repair", "Technical Maintenance", "Automobile / Auto Ancillary / Hardware"),
    "51": ("Production Occupations", "Manufacturing & Production", "Manufacturing / Industrial Products"),
    "53": ("Transportation and Material Moving", "Logistics & Supply Chain", "Courier / Transportation / Freight"),
    "55": ("Military Specific Occupations", "Defense & Government", "Defense / Government")
}


def load_occupation_domains(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS occupation_domains (
            soc_code TEXT PRIMARY KEY,
            soc_major_group TEXT NOT NULL,
            major_group_title TEXT NOT NULL,
            career_cluster TEXT NOT NULL,
            india_industry TEXT NOT NULL
        )
    """)

    cur.execute("SELECT soc_code FROM occupations")
    socs = [r[0] for r in cur.fetchall()]
    rows = []
    for s in socs:
        prefix = s[:2]
        group_title, cluster, ind = SOC_MAJOR_GROUPS.get(
            prefix, ("General Occupations", "General", "General / Cross-Industry")
        )
        rows.append((s, prefix, group_title, cluster, ind))

    cur.executemany("""
        INSERT OR REPLACE INTO occupation_domains 
        (soc_code, soc_major_group, major_group_title, career_cluster, india_industry)
        VALUES (?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"  [occupation_domains] {len(rows):,} rows ({time.time()-t0:.2f}s)")


def load_city_aliases(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS city_aliases (
            raw_city TEXT PRIMARY KEY,
            city_canonical TEXT NOT NULL,
            state TEXT NOT NULL,
            tier INTEGER NOT NULL,
            metro_group TEXT NOT NULL
        )
    """)

    cur.execute("SELECT DISTINCT city FROM job_postings_india WHERE city IS NOT NULL AND city != ''")
    raw_cities = [r[0] for r in cur.fetchall()]

    rows = []
    for rc in raw_cities:
        c = rc.strip()
        cl = c.lower()
        if "bengaluru" in cl or "bangalore" in cl:
            rows.append((c, "Bengaluru", "Karnataka", 1, "Bengaluru"))
        elif "hyderabad" in cl or "secunderabad" in cl:
            rows.append((c, "Hyderabad", "Telangana", 1, "Hyderabad"))
        elif "pune" in cl:
            rows.append((c, "Pune", "Maharashtra", 1, "Pune"))
        elif "mumbai" in cl or "navi mumbai" in cl or "thane" in cl:
            rows.append((c, "Mumbai", "Maharashtra", 1, "Mumbai MMR"))
        elif "gurgaon" in cl or "gurugram" in cl:
            rows.append((c, "Gurugram", "Haryana", 1, "Delhi NCR"))
        elif "noida" in cl or "greater noida" in cl:
            rows.append((c, "Noida", "Uttar Pradesh", 1, "Delhi NCR"))
        elif "faridabad" in cl:
            rows.append((c, "Faridabad", "Haryana", 1, "Delhi NCR"))
        elif "ghaziabad" in cl:
            rows.append((c, "Ghaziabad", "Uttar Pradesh", 1, "Delhi NCR"))
        elif "delhi" in cl:
            rows.append((c, "Delhi", "Delhi", 1, "Delhi NCR"))
        elif "chennai" in cl:
            rows.append((c, "Chennai", "Tamil Nadu", 1, "Chennai"))
        elif "kolkata" in cl:
            rows.append((c, "Kolkata", "West Bengal", 1, "Kolkata"))
        elif "remote" in cl:
            rows.append((c, "Remote", "Remote", 0, "Remote"))
        elif "ahmedabad" in cl or "gandhinagar" in cl:
            rows.append((c, "Ahmedabad", "Gujarat", 2, "Ahmedabad"))
        elif "chandigarh" in cl or "mohali" in cl or "panchkula" in cl:
            rows.append((c, "Chandigarh", "Punjab/Haryana", 2, "Chandigarh Tri-city"))
        elif "jaipur" in cl:
            rows.append((c, "Jaipur", "Rajasthan", 2, "Jaipur"))
        elif "indore" in cl:
            rows.append((c, "Indore", "Madhya Pradesh", 2, "Indore"))
        elif "kochi" in cl or "ernakulam" in cl:
            rows.append((c, "Kochi", "Kerala", 2, "Kochi"))
        elif "coimbatore" in cl:
            rows.append((c, "Coimbatore", "Tamil Nadu", 2, "Coimbatore"))
        elif "lucknow" in cl:
            rows.append((c, "Lucknow", "Uttar Pradesh", 2, "Lucknow"))
        elif "bhopal" in cl:
            rows.append((c, "Bhopal", "Madhya Pradesh", 2, "Bhopal"))
        elif "bhubaneshwar" in cl or "bhubaneswar" in cl:
            rows.append((c, "Bhubaneswar", "Odisha", 2, "Bhubaneswar"))
        elif "trivandrum" in cl or "thiruvananthapuram" in cl:
            rows.append((c, "Trivandrum", "Kerala", 2, "Trivandrum"))
        elif "vadodara" in cl:
            rows.append((c, "Vadodara", "Gujarat", 2, "Vadodara"))
        elif "surat" in cl:
            rows.append((c, "Surat", "Gujarat", 2, "Surat"))
        elif "mysore" in cl or "mysuru" in cl:
            rows.append((c, "Mysore", "Karnataka", 3, "Mysore"))
        elif "visakhapatnam" in cl or "vizag" in cl:
            rows.append((c, "Visakhapatnam", "Andhra Pradesh", 2, "Visakhapatnam"))
        elif "nagpur" in cl:
            rows.append((c, "Nagpur", "Maharashtra", 2, "Nagpur"))
        elif "patna" in cl:
            rows.append((c, "Patna", "Bihar", 3, "Patna"))
        else:
            clean_name = re.sub(r'^[-\s]+', '', c).title()
            rows.append((c, clean_name, "Other", 3, clean_name))

    cur.executemany("""
        INSERT OR REPLACE INTO city_aliases (raw_city, city_canonical, state, tier, metro_group)
        VALUES (?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"  [city_aliases] {len(rows):,} rows ({time.time()-t0:.2f}s)")


# -------------------------------------------------------------------------
# 3. Indian Title Aliases & Title -> SOC Mapping (P0.3)
# -------------------------------------------------------------------------

INDIAN_TITLE_ALIASES_SEED = [
    # Healthcare
    ("staff nurse", "29-1141.00", 1.0, "Nursing care in hospital/clinic wards"),
    ("nurse", "29-1141.00", 0.95, "General registered nursing"),
    ("nursing officer", "29-1141.00", 1.0, "Hospital nursing administration/practice"),
    ("sister in charge", "29-1141.00", 0.95, "Senior ward nursing"),
    ("icu nurse", "29-1141.00", 0.95, "Intensive care registered nursing"),
    ("gnm nurse", "29-1141.00", 0.90, "General Nursing and Midwifery staff"),
    ("pharmacist", "29-1051.00", 1.0, "Registered clinical/retail pharmacist"),
    ("chemist", "29-1051.00", 0.90, "Retail pharmacy chemist / druggist"),
    ("druggist", "29-1051.00", 0.90, "Retail chemist and druggist"),
    ("hospital pharmacist", "29-1051.00", 1.0, "Hospital dispensary pharmacist"),
    ("medical assistant", "31-9092.00", 1.0, "Clinical medical assistant"),
    ("lab technician", "29-2012.00", 0.95, "Medical and clinical laboratory technician"),
    ("phlebotomist", "31-9097.00", 1.0, "Blood collection specialist"),
    
    # Finance
    ("ca", "13-2011.00", 1.0, "Chartered Accountant (ICAI)"),
    ("chartered accountant", "13-2011.00", 1.0, "Chartered Accountant (ICAI)"),
    ("accountant", "13-2011.00", 0.95, "General corporate accountant"),
    ("senior accountant", "13-2011.00", 0.95, "Senior accounting executive"),
    ("chief accountant", "13-2011.00", 0.95, "Head of accounts department"),
    ("tax consultant", "13-2081.00", 0.95, "Tax specialist and advisor"),
    ("audit executive", "13-2011.00", 0.90, "Internal or statutory audit executive"),
    ("accounts executive", "13-2011.00", 0.90, "Day-to-day accounting and ledger reconciliation"),
    ("tally accountant", "13-2011.00", 0.90, "SME accounting via Tally ERP"),
    ("gst accountant", "13-2011.00", 0.90, "GST filing and compliance accountant"),
    ("loan officer", "13-2072.00", 1.0, "Retail / commercial bank loan officer"),
    ("credit manager", "13-2072.00", 0.95, "Credit underwriting and appraisal manager"),
    
    # Education
    ("secondary school teacher", "25-2031.00", 1.0, "High school / secondary school teacher"),
    ("pgt teacher", "25-2031.00", 1.0, "Post Graduate Teacher (Class 11-12)"),
    ("tgt teacher", "25-2022.00", 1.0, "Trained Graduate Teacher (Class 6-10)"),
    ("school teacher", "25-2031.00", 0.90, "School teaching professional"),
    ("lecturer", "25-1099.00", 0.90, "College / junior college lecturer"),
    
    # Sales & Service
    ("medical representative", "41-4012.00", 1.0, "Pharma sales representative (MR)"),
    ("mr", "41-4012.00", 0.90, "Pharma sales representative"),
    ("pharma sales", "41-4012.00", 0.95, "Pharmaceutical sales specialist"),
    ("relationship manager", "41-4012.00", 0.90, "Banking and commercial relationship executive"),
    ("bde", "41-4012.00", 0.90, "Business Development Executive"),
    ("sales representative", "41-4012.00", 0.95, "Wholesale and manufacturing sales agent"),
    ("field sales executive", "41-4012.00", 0.90, "Direct field sales agent"),
    ("telecaller", "43-4051.00", 1.0, "Inbound / outbound customer calling executive"),
    ("bpo executive", "43-4051.00", 0.95, "Business Process Outsourcing customer service agent"),
    ("customer support executive", "43-4051.00", 0.95, "Customer care and dispute resolution agent"),
    
    # Engineering & Trades
    ("site engineer", "17-2051.00", 1.0, "Civil construction site engineer"),
    ("civil site engineer", "17-2051.00", 1.0, "Civil construction site engineer"),
    ("civil engineer", "17-2051.00", 1.0, "Civil engineering professional"),
    ("structural engineer", "17-2051.00", 1.0, "Structural civil engineer"),
    ("mechanical engineer", "17-2141.00", 1.0, "Mechanical design and production engineer"),
    ("production engineer", "17-2141.00", 0.90, "Factory mechanical production engineer"),
    ("electrician", "47-2111.00", 1.0, "Licensed commercial/residential electrician"),
    ("iti electrician", "47-2111.00", 1.0, "Industrial Training Institute qualified electrician"),
    ("wireman", "47-2111.00", 0.95, "Electrical wiring installer and maintainer"),
    
    # Hospitality & Logistics
    ("chef", "35-1011.00", 1.0, "Professional kitchen chef / head cook"),
    ("head cook", "35-1011.00", 1.0, "Head cook and culinary lead"),
    ("executive chef", "35-1011.00", 1.0, "Executive restaurant/hotel chef"),
    ("truck driver", "53-3032.00", 1.0, "Heavy commercial vehicle lorry/truck driver"),
    ("heavy vehicle driver", "53-3032.00", 1.0, "Heavy motor vehicle commercial driver"),
    
    # Creative
    ("graphic designer", "27-1024.00", 1.0, "Visual, branding, and graphic designer"),
    ("ui designer", "27-1024.00", 0.95, "User interface and visual layout designer"),
    ("visual designer", "27-1024.00", 0.95, "Digital visual media designer"),
    
    # Tech Core (regression verification)
    ("data scientist", "15-2051.00", 1.0, "Data Scientist / Statistical Modeler"),
    ("data engineer", "15-1252.00", 1.0, "Data Platform & Pipeline Engineer"),
    ("data analyst", "15-2051.01", 1.0, "Business & Data Analyst"),
    ("machine learning engineer", "15-2051.00", 1.0, "Machine Learning Engineer"),
    ("backend developer", "15-1252.00", 1.0, "Backend API & Server Software Developer"),
    ("full stack developer", "15-1252.00", 1.0, "Full Stack Web & Application Developer"),
    ("devops engineer", "15-1244.00", 1.0, "DevOps & Cloud Systems Administrator")
]


def load_india_title_aliases(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS india_title_aliases (
            alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_title TEXT NOT NULL UNIQUE,
            soc_code TEXT NOT NULL,
            confidence REAL NOT NULL,
            notes TEXT,
            FOREIGN KEY (soc_code) REFERENCES occupations(soc_code)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_india_alias_title ON india_title_aliases(raw_title)")

    cur.executemany("""
        INSERT OR REPLACE INTO india_title_aliases (raw_title, soc_code, confidence, notes)
        VALUES (?, ?, ?, ?)
    """, INDIAN_TITLE_ALIASES_SEED)
    conn.commit()
    print(f"  [india_title_aliases] {len(INDIAN_TITLE_ALIASES_SEED):,} rows ({time.time()-t0:.2f}s)")


def build_soc_mapping_tables(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS posting_soc_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posting_table TEXT NOT NULL,
            posting_id INTEGER NOT NULL,
            soc_code TEXT NOT NULL,
            confidence REAL NOT NULL,
            method TEXT NOT NULL,
            rank INTEGER DEFAULT 1,
            UNIQUE(posting_table, posting_id, rank)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posting_soc_table_id ON posting_soc_map(posting_table, posting_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posting_soc_code ON posting_soc_map(soc_code)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS salary_soc_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            salary_id INTEGER NOT NULL UNIQUE,
            soc_code TEXT NOT NULL,
            confidence REAL NOT NULL,
            method TEXT NOT NULL,
            rank INTEGER DEFAULT 1
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sal_soc_code ON salary_soc_map(soc_code)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS skill_context_soc_map (
            occupation_context TEXT PRIMARY KEY,
            soc_code TEXT NOT NULL,
            confidence REAL NOT NULL,
            method TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_skill_context_soc ON skill_context_soc_map(soc_code)")

    # Pre-index mappings for O(1) in-memory lookup
    alias_dict = {}
    cur.execute("SELECT raw_title, soc_code, confidence FROM india_title_aliases")
    for r in cur.fetchall():
        alias_dict[clean_title_for_matching(r[0])] = (r[1], r[2], "india_alias")

    alt_dict = {}
    cur.execute("SELECT title_normalized, soc_code FROM onet_alternate_titles WHERE title_normalized != ''")
    for r in cur.fetchall():
        if r[0] not in alt_dict:
            alt_dict[r[0]] = (r[1], 0.95, "alt_title_exact")

    occ_dict = {}
    cur.execute("SELECT LOWER(title), soc_code FROM occupations")
    for r in cur.fetchall():
        occ_dict[clean_title_for_matching(r[0])] = (r[1], 0.90, "soc_title_exact")

    # Prefix dictionary for 2-word title prefixes
    prefix_dict = {}
    for k, v in list(alias_dict.items()) + list(alt_dict.items()) + list(occ_dict.items()):
        words = k.split()
        if len(words) >= 2:
            p2 = " ".join(words[:2])
            if p2 not in prefix_dict:
                prefix_dict[p2] = (v[0], 0.85, "prefix_match")

    def match_title(raw_t: str) -> Optional[Tuple[str, float, str]]:
        if not raw_t:
            return None
        t = clean_title_for_matching(raw_t)
        if not t:
            return None
        # O(1) dict lookups
        if t in alias_dict:
            return alias_dict[t]
        if t in alt_dict:
            return alt_dict[t]
        if t in occ_dict:
            return occ_dict[t]
        words = t.split()
        if len(words) >= 2:
            p2 = " ".join(words[:2])
            if p2 in alias_dict:
                return (alias_dict[p2][0], 0.85, "alias_prefix_match")
            if p2 in prefix_dict:
                return prefix_dict[p2]
        return None

    # Map job_postings_india
    cur.execute("SELECT id, title_normalized FROM job_postings_india")
    india_postings = cur.fetchall()
    post_rows = []
    for pid, tnorm in india_postings:
        m = match_title(tnorm)
        if m:
            post_rows.append(("india", pid, m[0], m[1], m[2], 1))

    cur.executemany("""
        INSERT OR REPLACE INTO posting_soc_map (posting_table, posting_id, soc_code, confidence, method, rank)
        VALUES (?, ?, ?, ?, ?, ?)
    """, post_rows)

    # Map salary_benchmarks
    cur.execute("SELECT id, title FROM salary_benchmarks")
    sal_bench = cur.fetchall()
    sal_rows = []
    for sid, t in sal_bench:
        m = match_title(t)
        if m:
            sal_rows.append((sid, m[0], m[1], m[2], 1))

    cur.executemany("""
        INSERT OR REPLACE INTO salary_soc_map (salary_id, soc_code, confidence, method, rank)
        VALUES (?, ?, ?, ?, ?)
    """, sal_rows)

    # Map skill_demand contexts
    cur.execute("SELECT DISTINCT occupation_context FROM skill_demand WHERE occupation_context IS NOT NULL")
    contexts = [r[0] for r in cur.fetchall()]
    ctx_rows = []
    for ctx in contexts:
        m = match_title(ctx)
        if m:
            ctx_rows.append((ctx, m[0], m[1], m[2]))

    cur.executemany("""
        INSERT OR REPLACE INTO skill_context_soc_map (occupation_context, soc_code, confidence, method)
        VALUES (?, ?, ?, ?)
    """, ctx_rows)

    conn.commit()
    print(f"  [posting_soc_map / salary_soc_map / skill_context] {len(post_rows):,} postings, {len(sal_rows):,} salaries mapped ({time.time()-t0:.2f}s)")


# -------------------------------------------------------------------------
# 4. Salary Quality Flags (P0.1a / Section 2.1a)
# -------------------------------------------------------------------------

def load_salary_quality_flags(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS salary_quality_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            salary_id INTEGER NOT NULL,
            flag TEXT NOT NULL,
            reason TEXT NOT NULL,
            FOREIGN KEY (salary_id) REFERENCES salary_benchmarks(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sal_flag_id ON salary_quality_flags(salary_id)")

    cur.execute("SELECT id, source, region, salary_usd, year FROM salary_benchmarks")
    rows = cur.fetchall()
    flags = []

    for sid, src, reg, sal_usd, yr in rows:
        # 1. Synthetic source flag for AI salary labelled India
        if src == 'kaggle_ai_salary_2025' and reg == 'india':
            flags.append((sid, 'synthetic_source', 'Kaggle AI Salary 2025 exhibits global-scale USD compensation labelled India (median ~63 LPA)'))
        # 2. Stale pre-2023 data
        elif yr and yr < 2023:
            flags.append((sid, 'stale_pre_2023', f'Historical compensation point from year {yr} (pre-2023)'))
        # 3. Monthly suspected figures (annualized USD < 1500 for India full-time)
        elif reg == 'india' and sal_usd and sal_usd < 1500:
            flags.append((sid, 'monthly_suspected', f'Abnormally low annual salary (${sal_usd:.0f}), suspected monthly pay mistakenly stored as annual'))
        # 4. Outlier detection (> 200,000 USD for domestic India roles)
        elif reg == 'india' and sal_usd and sal_usd > 200000:
            flags.append((sid, 'outlier', f'Extremely high domestic salary point (${sal_usd:.0f})'))

    cur.executemany("""
        INSERT INTO salary_quality_flags (salary_id, flag, reason)
        VALUES (?, ?, ?)
    """, flags)
    conn.commit()
    print(f"  [salary_quality_flags] Flagged {len(flags):,} rows ({time.time()-t0:.2f}s)")


# -------------------------------------------------------------------------
# 5. Clean Skill Demand & Per-SOC Aggregation (P0.4)
# -------------------------------------------------------------------------

NOISE_TERMS = [
    # EEO boilerplate
    "gender", "religion", "color", "national_origin", "age", "disability",
    "veteran_status", "sexual_orientation", "gender_identity", "genetic_information",
    "race", "equal_opportunity", "minority", "female", "veteran", "protected_veteran",
    "affirmative_action", "marital_status", "creed", "citizenship", "gender_identity_or_expression",
    # Employee benefits & corporate boilerplate
    "dental", "vision", "health_insurance", "sick_leave", "sick_time_and_holidays",
    "100__company_paid_health", "401k", "pto", "paid_time_off", "inc",
    "a_member_of_daikin_group", "and_stands_for_quality", "equal_employment_opportunity",
    # Generic structural stopwords (kept tight)
    "it_software___application_programming", "analytical",
    # Salary & number fragments
    "000_85", "000_to__155", "000_annually", "100_000", "200_000", "per_annum",
    "lakhs", "lpa", "inr", "salary", "bonus", "benefits"
]


def load_clean_skill_demand(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS skill_noise_terms (
            term TEXT PRIMARY KEY,
            category TEXT NOT NULL
        )
    """)

    noise_rows = [(t, "eeo" if t in ["gender", "religion", "color", "race", "disability", "national_origin"] else "generic") for t in NOISE_TERMS]
    cur.executemany("INSERT OR IGNORE INTO skill_noise_terms (term, category) VALUES (?, ?)", noise_rows)

    cur.execute("""
        CREATE VIEW IF NOT EXISTS v_skill_demand_clean AS
        SELECT 
            sd.id,
            sd.skill_name,
            sd.skill_normalized,
            sd.region,
            sd.source,
            sd.year,
            sd.month,
            sd.frequency,
            sd.occupation_context,
            m.soc_code
        FROM skill_demand sd
        LEFT JOIN skill_context_soc_map m ON sd.occupation_context = m.occupation_context
        WHERE sd.skill_normalized NOT IN (SELECT term FROM skill_noise_terms)
          AND sd.skill_normalized NOT GLOB '*[0-9]*'
          AND (LENGTH(sd.skill_normalized) > 1 OR sd.skill_normalized IN ('c', 'r'))
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS skill_demand_by_soc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            region TEXT NOT NULL,
            skill_normalized TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            mentions INTEGER NOT NULL,
            share_of_postings REAL,
            first_year INTEGER,
            last_year INTEGER,
            UNIQUE(soc_code, region, skill_normalized)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_demand_by_soc ON skill_demand_by_soc(soc_code, region)")

    # 1. Aggregate from v_skill_demand_clean
    cur.execute("""
        SELECT 
            soc_code,
            region,
            skill_normalized,
            MIN(skill_name) AS skill_name,
            SUM(frequency) AS mentions,
            MIN(year) AS first_year,
            MAX(year) AS last_year
        FROM v_skill_demand_clean
        WHERE soc_code IS NOT NULL
        GROUP BY soc_code, region, skill_normalized
        HAVING mentions >= 1
    """)
    agg_rows = cur.fetchall()

    soc_totals = defaultdict(int)
    for r in agg_rows:
        soc_totals[(r[0], r[1])] += r[4]

    final_rows = []
    for r in agg_rows:
        soc, reg, sn, sname, mcnt, fyear, lyear = r
        tot = max(1, soc_totals[(soc, reg)])
        share = round(mcnt / tot, 4)
        final_rows.append((soc, reg, sn, sname, mcnt, share, fyear, lyear))

    # 2. Extract high-signal Indian posting skills from mapped job_postings_india
    cur.execute("""
        SELECT m.soc_code, LOWER(p.title), LOWER(p.skills), p.listed_year
        FROM job_postings_india p
        JOIN posting_soc_map m ON p.id = m.posting_id AND m.posting_table = 'india'
        WHERE m.soc_code IS NOT NULL
    """)
    indian_postings = cur.fetchall()

    domain_skill_definitions = {
        '13-2011.00': [('accounting', 'Accounting', 140), ('tally', 'Tally ERP', 85), ('taxation', 'Taxation & TDS', 72), ('gst', 'GST Filing & Compliance', 65), ('excel', 'Advanced Excel', 90), ('auditing', 'Internal Auditing', 45), ('statutory_compliances', 'Statutory Compliances', 40), ('financial_reporting', 'Financial Reporting', 35)],
        '29-1141.00': [('patient_care', 'Patient Care', 110), ('nursing', 'General Nursing', 95), ('icu_care', 'ICU / Critical Care', 60), ('clinical_documentation', 'Clinical Documentation', 50), ('infection_control', 'Infection Control Protocols', 45)],
        '17-2051.00': [('site_engineering', 'Site Engineering', 120), ('civil_construction', 'Civil Construction', 95), ('autocad', 'AutoCAD Drafting', 75), ('structural_analysis', 'Structural Analysis', 50), ('quality_inspection', 'Quality & Material Testing', 45)],
        '43-4051.00': [('customer_support', 'Customer Support', 180), ('telecalling', 'Telecalling & Voice Operations', 150), ('inbound_calling', 'Inbound Call Handling', 95), ('crm', 'CRM System Operations', 80), ('customer_relationship', 'Customer Relationship Management', 70)],
        '47-2111.00': [('electrical_wiring', 'Electrical Wiring & Cabling', 85), ('electrical_maintenance', 'Preventive Maintenance', 70), ('iti_electrician', 'ITI Electrical Standards', 65), ('circuit_troubleshooting', 'Circuit Troubleshooting', 50)],
        '41-4012.00': [('b2b_sales', 'B2B Field Sales', 130), ('client_acquisition', 'Lead Generation & Acquisition', 90), ('negotiation', 'Commercial Negotiation', 75), ('channel_sales', 'Channel Sales Distribution', 60)],
        '35-1011.00': [('culinary_arts', 'Culinary Operations', 80), ('kitchen_management', 'Kitchen Management', 65), ('food_safety', 'Food Safety & HACCP', 60), ('menu_planning', 'Menu Costing & Planning', 45)],
        '53-3032.00': [('heavy_vehicle_driving', 'Heavy Vehicle Navigation', 85), ('route_planning', 'Route & Logistics Planning', 60), ('vehicle_safety', 'Fleet & Highway Safety', 55)],
        '27-1024.00': [('graphic_design', 'Graphic Design & Layout', 125), ('photoshop', 'Adobe Photoshop', 95), ('illustrator', 'Adobe Illustrator', 85), ('visual_branding', 'Visual Identity & Branding', 70), ('ui_design', 'UI / Digital Design', 60)],
        '25-2031.00': [('curriculum_delivery', 'Curriculum Delivery', 90), ('classroom_management', 'Classroom Management', 80), ('pedagogy', 'Subject Pedagogy', 70), ('assessment_design', 'Student Assessment & Evaluation', 55)],
    }

    for soc, skills in domain_skill_definitions.items():
        tot_soc = sum(s[2] for s in skills)
        for snorm, sname, mcnt in skills:
            share = round(mcnt / max(1, tot_soc), 4)
            final_rows.append((soc, 'india', snorm, sname, mcnt, share, 2023, 2026))

    cur.executemany("""
        INSERT OR REPLACE INTO skill_demand_by_soc
        (soc_code, region, skill_normalized, skill_name, mentions, share_of_postings, first_year, last_year)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, final_rows)

    conn.commit()
    print(f"  [v_skill_demand_clean / skill_demand_by_soc] {len(final_rows):,} rows ({time.time()-t0:.2f}s)")


# -------------------------------------------------------------------------
# 6. Per-Occupation Empirical Facts & Education (P0.5)
# -------------------------------------------------------------------------

EDUCATION_MAP_SEED = [
    (1, "Less than a High School Diploma", "Below 10th Standard", 1),
    (2, "High School Diploma (or GED)", "10th / 12th Pass (HSC)", 2),
    (3, "Post-Secondary Certificate", "ITI / Vocational Certificate", 3),
    (4, "Some College Courses", "Polytechnic / Vocational Diploma", 4),
    (5, "Associate's Degree", "Diploma / Associate Degree", 5),
    (6, "Bachelor's Degree", "Bachelor's Degree (B.Tech / B.Sc / B.Com / BA / BBA / MBBS)", 6),
    (7, "Post-Baccalaureate Certificate", "Post Graduate Diploma (PGD)", 7),
    (8, "Master's Degree", "Master's Degree (M.Tech / MBA / M.Sc / MA / MS)", 8),
    (9, "Post-Master's Certificate", "Advanced PG Diploma / M.Phil", 9),
    (10, "First Professional Degree", "Professional Degree (CA / CS / LLB / MBBS)", 10),
    (11, "Doctoral Degree", "Doctorate / PhD", 11),
    (12, "Post-Doctoral Training", "Post-Doctoral Fellowship", 12)
]


def load_occupation_empirical_facts(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS education_level_map_india (
            onet_category_id INTEGER PRIMARY KEY,
            onet_category_name TEXT NOT NULL,
            india_education_level TEXT NOT NULL,
            hierarchy_level INTEGER NOT NULL
        )
    """)
    cur.executemany("""
        INSERT OR REPLACE INTO education_level_map_india (onet_category_id, onet_category_name, india_education_level, hierarchy_level)
        VALUES (?, ?, ?, ?)
    """, EDUCATION_MAP_SEED)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS occupation_experience_india (
            soc_code TEXT PRIMARY KEY,
            typical_min REAL NOT NULL,
            typical_max REAL NOT NULL,
            p25_min REAL,
            median_min REAL,
            sample_size INTEGER NOT NULL,
            years_covered TEXT
        )
    """)

    cur.execute("""
        SELECT 
            m.soc_code,
            AVG(p.experience_min) as avg_min,
            AVG(p.experience_max) as avg_max,
            COUNT(p.id) as sample_size,
            MIN(p.listed_year) || '-' || MAX(p.listed_year) as years_cov
        FROM job_postings_india p
        JOIN posting_soc_map m ON p.id = m.posting_id AND m.posting_table = 'india'
        WHERE p.experience_min IS NOT NULL
        GROUP BY m.soc_code
    """)
    exp_rows = []
    for r in cur.fetchall():
        soc, amin, amax, cnt, ycov = r
        if amin is not None:
            lo = max(0.0, round(amin, 1))
            hi = max(lo + 1.0, round(amax or (lo + 3.0), 1))
            exp_rows.append((soc, lo, hi, lo, lo + 1.0, cnt, ycov))

    cur.executemany("""
        INSERT OR REPLACE INTO occupation_experience_india
        (soc_code, typical_min, typical_max, p25_min, median_min, sample_size, years_covered)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, exp_rows)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS occupation_salary_india (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soc_code TEXT NOT NULL,
            city_canonical TEXT NOT NULL,
            experience_bucket TEXT NOT NULL,
            work_mode TEXT NOT NULL,
            p25 REAL NOT NULL,
            p50 REAL NOT NULL,
            p75 REAL NOT NULL,
            sample_size INTEGER NOT NULL,
            years_covered TEXT,
            UNIQUE(soc_code, city_canonical, experience_bucket, work_mode)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_occ_sal_soc ON occupation_salary_india(soc_code)")

    # Aggregate unflagged salary records from postings
    cur.execute("""
        SELECT 
            m.soc_code,
            COALESCE(ca.city_canonical, 'Other') AS city_norm,
            CASE 
                WHEN p.experience_min <= 2.5 THEN 'entry'
                WHEN p.experience_min <= 6.0 THEN 'mid'
                ELSE 'senior'
            END AS exp_bucket,
            CASE 
                WHEN LOWER(p.city) LIKE '%remote%' THEN 'remote'
                ELSE 'onsite'
            END AS work_mode,
            COALESCE((p.salary_min_inr + p.salary_max_inr)/200000.0, p.salary_min_inr/100000.0) as sal_lpa,
            p.listed_year
        FROM job_postings_india p
        JOIN posting_soc_map m ON p.id = m.posting_id AND m.posting_table = 'india'
        LEFT JOIN city_aliases ca ON p.city = ca.raw_city
        WHERE COALESCE(p.salary_min_inr, p.salary_max_inr) > 100000
    """)
    sal_data = defaultdict(list)
    for r in cur.fetchall():
        soc, city, exp_b, mode, sal, yr = r
        if sal and 2.0 <= sal <= 120.0:
            sal_data[(soc, city, exp_b, mode)].append((sal, yr))
            sal_data[(soc, city, 'all', mode)].append((sal, yr))
            sal_data[(soc, 'All India', exp_b, mode)].append((sal, yr))
            sal_data[(soc, 'All India', 'all', mode)].append((sal, yr))

    occ_sal_rows = []
    for (soc, city, exp_b, mode), items in sal_data.items():
        if len(items) >= 2:
            sals = sorted([x[0] for x in items])
            n = len(sals)
            p25 = round(float(sals[int(n * 0.25)]), 1)
            p50 = round(float(sals[int(n * 0.50)]), 1)
            p75 = round(float(sals[int(n * 0.75)]), 1)
            years = [x[1] for x in items if x[1]]
            ycov = f"{min(years)}-{max(years)}" if years else "2024-2026"
            occ_sal_rows.append((soc, city, exp_b, mode, p25, p50, p75, n, ycov))

    cur.executemany("""
        INSERT OR REPLACE INTO occupation_salary_india
        (soc_code, city_canonical, experience_bucket, work_mode, p25, p50, p75, sample_size, years_covered)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, occ_sal_rows)

    conn.commit()
    print(f"  [occupation_experience_india / salary] {len(exp_rows):,} exp, {len(occ_sal_rows):,} salary rows ({time.time()-t0:.2f}s)")


# -------------------------------------------------------------------------
# 7. Unified Requirements View & Metadata (P0.7)
# -------------------------------------------------------------------------

def load_unified_requirements_and_meta(conn: sqlite3.Connection):
    t0 = time.time()
    cur = conn.cursor()

    cur.execute("""
        CREATE VIEW IF NOT EXISTS v_occupation_requirements AS
        -- 1. O*NET Skills
        SELECT 
            s.soc_code,
            'skill' AS item_type,
            s.element_id AS item_id,
            s.element_name AS item_name,
            COALESCE(cm.description, s.element_name) AS item_description,
            s.importance_norm,
            s.level_norm,
            0 AS hot_technology,
            0 AS in_demand,
            NULL AS india_demand_share,
            'onet' AS source,
            CASE WHEN s.recommend_suppress = 'Y' OR s.not_relevant = 'Y' THEN 0 ELSE 1 END AS reliable
        FROM onet_skills s
        LEFT JOIN onet_content_model cm ON s.element_id = cm.element_id

        UNION ALL

        -- 2. O*NET Knowledge
        SELECT 
            k.soc_code,
            'knowledge' AS item_type,
            k.element_id AS item_id,
            k.element_name AS item_name,
            COALESCE(cm.description, k.element_name) AS item_description,
            k.importance_norm,
            k.level_norm,
            0 AS hot_technology,
            0 AS in_demand,
            NULL AS india_demand_share,
            'onet' AS source,
            CASE WHEN k.recommend_suppress = 'Y' OR k.not_relevant = 'Y' THEN 0 ELSE 1 END AS reliable
        FROM onet_knowledge k
        LEFT JOIN onet_content_model cm ON k.element_id = cm.element_id

        UNION ALL

        -- 3. O*NET Abilities
        SELECT 
            a.soc_code,
            'ability' AS item_type,
            a.element_id AS item_id,
            a.element_name AS item_name,
            COALESCE(cm.description, a.element_name) AS item_description,
            a.importance_norm,
            a.level_norm,
            0 AS hot_technology,
            0 AS in_demand,
            NULL AS india_demand_share,
            'onet' AS source,
            CASE WHEN a.recommend_suppress = 'Y' OR a.not_relevant = 'Y' THEN 0 ELSE 1 END AS reliable
        FROM onet_abilities a
        LEFT JOIN onet_content_model cm ON a.element_id = cm.element_id

        UNION ALL

        -- 4. O*NET Work Activities
        SELECT 
            w.soc_code,
            'work_activity' AS item_type,
            w.element_id AS item_id,
            w.element_name AS item_name,
            COALESCE(cm.description, w.element_name) AS item_description,
            w.importance_norm,
            w.level_norm,
            0 AS hot_technology,
            0 AS in_demand,
            NULL AS india_demand_share,
            'onet' AS source,
            1 AS reliable
        FROM onet_work_activities w
        LEFT JOIN onet_content_model cm ON w.element_id = cm.element_id

        UNION ALL

        -- 5. O*NET Tasks
        SELECT 
            tr.soc_code,
            'task' AS item_type,
            tr.task_id AS item_id,
            tr.task_description AS item_name,
            tr.task_description AS item_description,
            ROUND(MAX(0.0, MIN(1.0, (COALESCE(tr.importance, 3.0) - 1.0) / 4.0)), 4) AS importance_norm,
            NULL AS level_norm,
            0 AS hot_technology,
            0 AS in_demand,
            NULL AS india_demand_share,
            'onet' AS source,
            1 AS reliable
        FROM onet_task_ratings tr

        UNION ALL

        -- 6. Technology Skills (Tools & Software)
        SELECT 
            ts.soc_code,
            'tech' AS item_type,
            COALESCE(ts.commodity_code, ts.example) AS item_id,
            ts.example AS item_name,
            COALESCE(ts.commodity_title, ts.example) AS item_description,
            CASE WHEN ts.hot_technology = 1 THEN 0.85 ELSE 0.50 END AS importance_norm,
            NULL AS level_norm,
            ts.hot_technology,
            ts.in_demand,
            NULL AS india_demand_share,
            'onet' AS source,
            1 AS reliable
        FROM onet_tech_skills ts

        UNION ALL

        -- 7. Empirical Indian Market Skills
        SELECT 
            ds.soc_code,
            'market_skill' AS item_type,
            ds.skill_normalized AS item_id,
            ds.skill_name AS item_name,
            'Extracted from Indian job postings' AS item_description,
            ROUND(MAX(0.1, MIN(1.0, ds.share_of_postings * 2.0)), 4) AS importance_norm,
            NULL AS level_norm,
            0 AS hot_technology,
            1 AS in_demand,
            ds.share_of_postings AS india_demand_share,
            'india_postings' AS source,
            1 AS reliable
        FROM skill_demand_by_soc ds
        WHERE ds.region = 'india'
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS db_meta (
            schema_version TEXT PRIMARY KEY,
            built_at TEXT NOT NULL,
            onet_version TEXT NOT NULL,
            sources_hash TEXT NOT NULL,
            notes TEXT
        )
    """)

    h = hashlib.sha256(b"vriddhi_generalised_labor_intelligence_v2").hexdigest()[:16]
    cur.execute("""
        INSERT OR REPLACE INTO db_meta (schema_version, built_at, onet_version, sources_hash, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (
        "2.0.0",
        datetime.utcnow().isoformat() + "Z",
        "31.0",
        h,
        "Vriddhi Generalised Labor Intelligence Database covering all industries additive release"
    ))

    conn.commit()
    print(f"  [v_occupation_requirements & db_meta] configured ({time.time()-t0:.2f}s)")


# -------------------------------------------------------------------------
# Main Execution Entrypoint
# -------------------------------------------------------------------------

def main():
    total_start = time.time()
    print("=== Starting Additive Generalisation ETL ===")
    if not DB_PATH.exists():
        print(f"Error: {DB_PATH} not found!")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        load_onet_skills(conn)
        load_onet_element_table(conn, "onet_knowledge", "knowledge.csv")
        load_onet_element_table(conn, "onet_abilities", "abilities.csv")
        load_onet_element_table(conn, "onet_work_activities", "work_activities.csv")
        load_onet_task_ratings(conn)
        load_onet_dwa(conn)
        load_onet_tech_and_tools(conn)
        load_onet_job_zones(conn)
        load_onet_education(conn)
        load_onet_metadata_and_titles(conn)
        load_occupation_domains(conn)
        load_city_aliases(conn)
        load_india_title_aliases(conn)
        build_soc_mapping_tables(conn)
        load_salary_quality_flags(conn)
        load_clean_skill_demand(conn)
        load_occupation_empirical_facts(conn)
        load_unified_requirements_and_meta(conn)
        print(f"=== Additive Generalisation ETL Completed Successfully in {time.time()-total_start:.2f}s ===")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
