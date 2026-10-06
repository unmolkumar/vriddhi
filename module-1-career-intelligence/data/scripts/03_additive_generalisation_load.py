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
from datetime import datetime, timezone
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

    # 1. Technology Skills (Software)
    path_tech = ONET_DIR / "software_skills.csv"
    tech_rows = []
    if path_tech.exists():
        with open(path_tech, 'r', encoding='utf-8-sig', errors='replace') as f:
            for r in csv.DictReader(f):
                soc = r.get('O*NET-SOC Code', '').strip()
                ex = (r.get('Workplace Example') or r.get('Example') or '').strip()
                cid = r.get('Element ID', '').strip()
                ctitle = r.get('Element Name', '').strip()
                hot = 1 if str(r.get('Hot Technology', '')).strip().upper() == 'Y' else 0
                indem = 1 if str(r.get('In Demand', '')).strip().upper() == 'Y' else 0
                if soc and ex:
                    tech_rows.append((soc, ex, cid, ctitle, hot, indem))

    # 2. Official O*NET Tools Used (41,663 lines covering all occupations)
    tool_rows = []
    tools_path = ONET_DIR / "Tools Used.txt"
    if not tools_path.exists():
        tools_path = ONET_DIR / "tools_used.csv"

    if tools_path.exists():
        delim = '\t' if tools_path.suffix == '.txt' else ','
        with open(tools_path, 'r', encoding='utf-8', errors='replace') as f:
            for r in csv.DictReader(f, delimiter=delim):
                soc = r.get('O*NET-SOC Code', '').strip()
                ex = (r.get('Example') or r.get('Workplace Example') or '').strip()
                ccode = r.get('Commodity Code', '').strip()
                ctitle = r.get('Commodity Title', '').strip()
                if soc and ex:
                    tool_rows.append((soc, ex, ccode, ctitle))

    # For parent occupations (.00) that don't have direct tools in O*NET but have child specializations (.01, .02, etc.)
    # (e.g. 15-2051.00 Data Scientists inheriting official tools from 15-2051.01 / 15-2051.02)
    direct_tool_socs = set(r[0] for r in tool_rows)
    cur.execute("SELECT soc_code FROM occupations")
    all_known_socs = set(r[0] for r in cur.fetchall())

    parent_additions = []
    seen_parent_examples = set()
    for soc, ex, ccode, ctitle in tool_rows:
        if not soc.endswith('.00'):
            parent_soc = soc[:7] + '.00'
            if parent_soc in all_known_socs and parent_soc not in direct_tool_socs:
                if (parent_soc, ex) not in seen_parent_examples:
                    seen_parent_examples.add((parent_soc, ex))
                    parent_additions.append((parent_soc, ex, ccode, ctitle))

    if parent_additions:
        tool_rows.extend(parent_additions)
        print(f"    Roll-up mapped {len(parent_additions)} official tools to {len(seen_parent_examples)} parent occupations")

    cur.execute("DELETE FROM onet_tech_skills")
    cur.executemany("""
        INSERT INTO onet_tech_skills (soc_code, example, commodity_code, commodity_title, hot_technology, in_demand)
        VALUES (?, ?, ?, ?, ?, ?)
    """, tech_rows)

    cur.execute("DELETE FROM onet_tools")
    cur.executemany("""
        INSERT INTO onet_tools (soc_code, example, commodity_code, commodity_title)
        VALUES (?, ?, ?, ?)
    """, tool_rows)

    conn.commit()
    print(f"  [onet_tech_skills / onet_tools] {len(tech_rows):,} tech, {len(tool_rows):,} official O*NET tools ({time.time()-t0:.2f}s)")


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
                prefix_dict[p2] = (v[0], v[1], "prefix_match", k)

    def match_title(raw_t: str) -> Optional[Tuple[str, float, str]]:
        if not raw_t:
            return None
        t = clean_title_for_matching(raw_t)
        if not t:
            return None
        # O(1) dict lookups for exact matches
        if t in alias_dict:
            return alias_dict[t]
        if t in alt_dict:
            return alt_dict[t]
        if t in occ_dict:
            return occ_dict[t]
        words = t.split()
        if len(words) >= 2:
            p2 = " ".join(words[:2])
            target_info = None
            if p2 in alias_dict:
                soc, bconf, _ = alias_dict[p2]
                target_info = (soc, bconf, "alias_prefix_match", p2)
            elif p2 in prefix_dict:
                target_info = prefix_dict[p2]

            if target_info:
                soc_code, base_conf, meth, target_full = target_info

                # Cross-domain safety guard: software titles must NOT map to civil engineering or trades
                if soc_code == "17-2051.00" and any(sw in t for sw in ["software", "developer", "java", "python", "programming", "frontend", "backend", "full stack"]):
                    return None
                if soc_code == "47-2111.00" and any(sw in t for sw in ["software", "developer", "frontend", "backend"]):
                    return None

                # Compute token overlap / Jaccard similarity score
                w_post = set(words)
                w_target = set(target_full.split())
                inter = w_post & w_target
                union = w_post | w_target
                jaccard = len(inter) / max(len(union), 1)
                overlap_ratio = len(inter) / max(len(w_post), 1)

                # Dynamic score between 0.55 and 0.88 based on token overlap
                score = round(0.55 + 0.35 * (0.6 * jaccard + 0.4 * overlap_ratio), 3)
                score = max(0.50, min(0.90, score))
                return (soc_code, score, meth)
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
    # Employee benefits & corporate compensation boilerplate
    "dental", "vision", "health_insurance", "health insurance", "sick_leave", "sick leave",
    "sick_time_and_holidays", "100__company_paid_health", "401k", "pto", "paid_time_off", "paid time off", "inc",
    "a_member_of_daikin_group", "and_stands_for_quality", "equal_employment_opportunity",
    "pf", "provident_fund", "provident fund", "gratuity", "insurance", "life_insurance", "life insurance",
    "medical", "esi", "bonus", "incentives", "allowance", "per_diem", "per diem", "pension",
    "leave_encashment", "leave encashment", "esop", "stock_options", "stock options", "relocation",
    "cab_facility", "free_food", "subsidized_meals", "benefits", "salary",
    # Seniority words & generic level descriptors
    "senior", "junior", "entry_level", "entry level", "entry", "fresher", "freshers", "trainee",
    "lead", "basic", "intern", "internship", "principal", "executive", "associate", "specialist",
    "manager", "officer", "supervisor", "site_engineer", "site engineer", "engineer_trainee",
    "engineer trainee", "focus", "level", "experienced", "intermediate", "expert", "director", "head",
    # Indian cities, states and locations
    "ahmedabad", "gujarat", "bengaluru", "bangalore", "karnataka", "mumbai", "bombay", "maharashtra",
    "pune", "delhi", "new_delhi", "new delhi", "noida", "gurgaon", "gurugram", "haryana",
    "hyderabad", "secunderabad", "telangana", "andhra_pradesh", "andhra pradesh", "chennai", "madras",
    "tamil_nadu", "tamil nadu", "kolkata", "calcutta", "west_bengal", "west bengal", "jaipur", "rajasthan",
    "surat", "kochi", "cochin", "kerala", "indore", "madhya_pradesh", "madhya pradesh", "chandigarh",
    "punjab", "lucknow", "kanpur", "uttar_pradesh", "uttar pradesh", "nagpur", "bhopal", "patna",
    "bihar", "vadodara", "baroda", "ghaziabad", "ludhiana", "agra", "nashik", "faridabad",
    "meerut", "rajkot", "varanasi", "srinagar", "aurangabad", "dhanbad", "amritsar", "navi_mumbai",
    "navi mumbai", "allahabad", "prayagraj", "howrah", "ranchi", "gwalior", "jabalpur", "coimbatore",
    "vijayawada", "jodhpur", "madurai", "raipur", "kota", "guwahati", "solapur", "hubballi",
    "bareilly", "moradabad", "mysore", "mysuru", "tiruchirappalli", "tiruppur", "salem", "aligarh",
    "thiruvananthapuram", "trivandrum", "bhubaneswar", "odisha", "dehradun", "uttarakhand", "shimla",
    "himachal_pradesh", "jammu", "kashmir", "goa", "panaji", "india",
    # Generic structural stopwords & functional fragments
    "it_software___application_programming", "analytical", "communication",
    # Salary & number fragments
    "000_85", "000_to__155", "000_annually", "100_000", "200_000", "per_annum",
    "lakhs", "lpa", "inr"
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

    # Base noise rows
    noise_dict = {}
    for t in NOISE_TERMS:
        cat = "eeo" if t in ["gender", "religion", "color", "race", "disability", "national_origin"] else "generic"
        noise_dict[t.lower().strip()] = cat
        noise_dict[t.lower().strip().replace(" ", "_")] = cat

    # 1. Add all city aliases from city_aliases
    cur.execute("SELECT DISTINCT LOWER(TRIM(raw_city)) FROM city_aliases UNION SELECT DISTINCT LOWER(TRIM(city_canonical)) FROM city_aliases")
    for r in cur.fetchall():
        if r[0]:
            noise_dict[r[0]] = "location"
            noise_dict[r[0].replace(" ", "_")] = "location"

    # 2. Add all PromptCloud functional area / industry category strings
    cur.execute("SELECT DISTINCT LOWER(TRIM(skills)) FROM job_postings_india WHERE source = 'naukri_promptcloud'")
    for r in cur.fetchall():
        val = r[0]
        if val:
            noise_dict[val] = "industry"
            noise_dict[val.replace(" ", "_")] = "industry"
            noise_dict[val.replace("-", "_")] = "industry"

    promptcloud_industries = [
        "hotels", "it_hardware", "it hardware", "ites", "financial_services", "financial services",
        "medical", "hr", "analytics", "backend", "chip_design", "chip design", "application_development",
        "application development", "production", "accounts", "teaching", "marketing", "supply_chain",
        "supply chain", "legal", "site_engineering", "site engineering", "journalism", "strategy",
        "defence_forces", "defence forces", "top_management", "top management", "travel", "export",
        "packaging", "shipping", "banking", "telecom", "fmcg", "retail", "real_estate", "real estate"
    ]
    for ind in promptcloud_industries:
        noise_dict[ind] = "industry"
        noise_dict[ind.replace(" ", "_")] = "industry"

    # 3. Add all occupation titles and aliases so titles never appear as skills of themselves
    cur.execute("SELECT DISTINCT LOWER(TRIM(title)) FROM occupations")
    for r in cur.fetchall():
        if r[0] and len(r[0]) > 3:
            noise_dict[r[0]] = "occupation_title"
            noise_dict[r[0].replace(" ", "_")] = "occupation_title"

    cur.execute("SELECT DISTINCT LOWER(TRIM(raw_title)) FROM india_title_aliases")
    for r in cur.fetchall():
        if r[0] and len(r[0]) > 3:
            noise_dict[r[0]] = "occupation_title"
            noise_dict[r[0].replace(" ", "_")] = "occupation_title"

    noise_rows = [(k, v) for k, v in noise_dict.items() if k]
    cur.executemany("INSERT OR REPLACE INTO skill_noise_terms (term, category) VALUES (?, ?)", noise_rows)

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
        WHERE LOWER(sd.skill_normalized) NOT IN (SELECT LOWER(term) FROM skill_noise_terms)
          AND LOWER(sd.skill_name) NOT IN (SELECT LOWER(term) FROM skill_noise_terms)
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
            posting_count INTEGER NOT NULL,
            soc_posting_total INTEGER,
            share_of_postings REAL,
            mentions INTEGER NOT NULL,
            first_year INTEGER,
            last_year INTEGER,
            source TEXT DEFAULT 'india_postings',
            UNIQUE(soc_code, region, skill_normalized)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_demand_by_soc ON skill_demand_by_soc(soc_code, region)")
    for col in ["posting_count INTEGER DEFAULT 0", "soc_posting_total INTEGER", "share_of_postings REAL", "source TEXT DEFAULT 'india_postings'"]:
        try:
            cur.execute(f"ALTER TABLE skill_demand_by_soc ADD COLUMN {col}")
        except Exception:
            pass

    # Total postings mapped per SOC
    soc_posting_totals = defaultdict(int)
    cur.execute("SELECT soc_code, COUNT(DISTINCT posting_id) FROM posting_soc_map WHERE posting_table = 'india' GROUP BY soc_code")
    for r in cur.fetchall():
        if r[0]:
            soc_posting_totals[(r[0], 'india')] = r[1]
    cur.execute("SELECT soc_code, COUNT(DISTINCT posting_id) FROM posting_soc_map WHERE posting_table = 'global' GROUP BY soc_code")
    for r in cur.fetchall():
        if r[0]:
            soc_posting_totals[(r[0], 'global')] = r[1]

    DISPLAY_NAME_OVERRIDES = {
        "python": "Python",
        "ml": "Machine Learning",
        "machine_learning": "Machine Learning",
        "sql": "SQL",
        "deep_learning": "Deep Learning",
        "tensorflow": "TensorFlow",
        "pytorch": "PyTorch",
        "r": "R",
        "tableau": "Tableau",
        "nlp": "Natural Language Processing (NLP)",
        "computer_vision": "Computer Vision",
        "data_science": "Data Science",
        "scala": "Scala",
        "linux": "Linux",
        "kubernetes": "Kubernetes",
        "data_visualization": "Data Visualization",
        "gcp": "Google Cloud Platform (GCP)",
        "git": "Git",
        "hadoop": "Hadoop",
        "java": "Java",
        "aws": "AWS",
        "spark": "Apache Spark",
        "pyspark": "PySpark",
        "azure": "Microsoft Azure",
        "data_analysis": "Data Analysis",
        "mlops": "MLOps",
        "docker": "Docker",
        "scikit_learn": "Scikit-Learn",
        "pandas": "Pandas",
        "ai": "Artificial Intelligence (AI)",
        "neural_networks": "Neural Networks",
        "predictive_modeling": "Predictive Modeling",
        "data_mining": "Data Mining",
        "statistics": "Statistics",
        "big_data": "Big Data",
        "power_bi": "Power BI",
        "c": "C",
        "cpp": "C++",
        "nosql": "NoSQL",
        "mongodb": "MongoDB",
        "postgresql": "PostgreSQL",
        "mysql": "MySQL",
        "rest_api": "REST APIs",
        "ci_cd": "CI/CD",
        "cloud_computing": "Cloud Computing",
        "kafka": "Apache Kafka",
        "etl": "ETL Pipelines",
        "sas": "SAS",
        "excel": "Advanced Excel",
        "autocad": "AutoCAD",
        "tally": "Tally ERP",
        "gst": "GST Compliance",
        "crm": "CRM Systems"
    }

    # Aggregate from v_skill_demand_clean with minimum support (mentions >= 3)
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
        HAVING mentions >= 3
    """)
    agg_rows = cur.fetchall()

    soc_groups = defaultdict(list)
    for r in agg_rows:
        soc, reg, sn, raw_sname, mcnt, fyear, lyear = r
        posting_count = mcnt
        soc_tot = soc_posting_totals.get((soc, reg), 0)
        if soc_tot <= 0:
            soc_tot = max(1, posting_count)
        else:
            soc_tot = max(posting_count, soc_tot)
        share = round(posting_count / soc_tot, 4)
        src_tag = "india_postings" if reg == "india" else "global_postings"

        display_name = DISPLAY_NAME_OVERRIDES.get(sn.lower(), sn.replace("_", " ").title())

        # Keep if posting_count >= 3
        soc_groups[(soc, reg)].append((
            soc, reg, sn, display_name, posting_count, soc_tot, share, posting_count, fyear, lyear, src_tag
        ))

    final_rows = []
    for (soc, reg), items in soc_groups.items():
        # Sort by posting_count DESC and cap at top 50 per SOC
        items.sort(key=lambda x: x[4], reverse=True)
        final_rows.extend(items[:50])

    # 2. Hand-curated domain competencies: labeled source='curated', share_of_postings=NULL
    domain_skill_definitions = {
        '13-2011.00': [('accounting', 'Accounting'), ('tally', 'Tally ERP'), ('taxation', 'Taxation & TDS'), ('gst', 'GST Filing & Compliance'), ('excel', 'Advanced Excel'), ('auditing', 'Internal Auditing'), ('statutory_compliances', 'Statutory Compliances'), ('financial_reporting', 'Financial Reporting')],
        '29-1141.00': [('patient_care', 'Patient Care'), ('nursing', 'General Nursing'), ('icu_care', 'ICU / Critical Care'), ('clinical_documentation', 'Clinical Documentation'), ('infection_control', 'Infection Control Protocols')],
        '17-2051.00': [('site_engineering', 'Site Engineering'), ('civil_construction', 'Civil Construction'), ('autocad', 'AutoCAD Drafting'), ('structural_analysis', 'Structural Analysis'), ('quality_inspection', 'Quality & Material Testing')],
        '43-4051.00': [('customer_support', 'Customer Support'), ('telecalling', 'Telecalling & Voice Operations'), ('inbound_calling', 'Inbound Call Handling'), ('crm', 'CRM System Operations'), ('customer_relationship', 'Customer Relationship Management')],
        '47-2111.00': [('electrical_wiring', 'Electrical Wiring & Cabling'), ('electrical_maintenance', 'Preventive Maintenance'), ('iti_electrician', 'ITI Electrical Standards'), ('circuit_troubleshooting', 'Circuit Troubleshooting')],
        '41-4012.00': [('b2b_sales', 'B2B Field Sales'), ('client_acquisition', 'Lead Generation & Acquisition'), ('negotiation', 'Commercial Negotiation'), ('channel_sales', 'Channel Sales Distribution')],
        '35-1011.00': [('culinary_arts', 'Culinary Operations'), ('kitchen_management', 'Kitchen Management'), ('food_safety', 'Food Safety & HACCP'), ('menu_planning', 'Menu Costing & Planning')],
        '53-3032.00': [('heavy_vehicle_driving', 'Heavy Vehicle Navigation'), ('route_planning', 'Route & Logistics Planning'), ('vehicle_safety', 'Fleet & Highway Safety')],
        '27-1024.00': [('graphic_design', 'Graphic Design & Layout'), ('photoshop', 'Adobe Photoshop'), ('illustrator', 'Adobe Illustrator'), ('visual_branding', 'Visual Identity & Branding'), ('ui_design', 'UI / Digital Design')],
        '25-2031.00': [('curriculum_delivery', 'Curriculum Delivery'), ('classroom_management', 'Classroom Management'), ('pedagogy', 'Subject Pedagogy'), ('assessment_design', 'Student Assessment & Evaluation')],
    }

    curated_rows = []
    for soc, skills in domain_skill_definitions.items():
        for snorm, sname in skills:
            # Curated rows: posting_count=0, soc_posting_total=None, share_of_postings=None, mentions=0, source='curated'
            curated_rows.append((soc, 'curated', snorm, sname, 0, None, None, 0, 2023, 2026, 'curated'))

    all_skill_rows = final_rows + curated_rows

    cur.execute("DELETE FROM skill_demand_by_soc")
    cur.executemany("""
        INSERT OR REPLACE INTO skill_demand_by_soc
        (soc_code, region, skill_normalized, skill_name, posting_count, soc_posting_total, share_of_postings, mentions, first_year, last_year, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, all_skill_rows)

    conn.commit()
    print(f"  [v_skill_demand_clean / skill_demand_by_soc] {len(final_rows):,} posting skills + {len(curated_rows):,} curated skills ({time.time()-t0:.2f}s)")


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

    cur.execute("DROP VIEW IF EXISTS v_occupation_requirements")
    cur.execute("""
        CREATE VIEW v_occupation_requirements AS
        SELECT 
            soc_code,
            item_type,
            MIN(item_id) AS item_id,
            item_name,
            MAX(item_description) AS item_description,
            MAX(importance_norm) AS importance_norm,
            MAX(level_norm) AS level_norm,
            MAX(hot_technology) AS hot_technology,
            MAX(in_demand) AS in_demand,
            MAX(india_demand_share) AS india_demand_share,
            MIN(source) AS source,
            MAX(reliable) AS reliable,
            MAX(posting_count) AS posting_count,
            MAX(soc_posting_total) AS soc_posting_total
        FROM (
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
                CASE WHEN s.recommend_suppress = 'Y' OR s.not_relevant = 'Y' THEN 0 ELSE 1 END AS reliable,
                0 AS posting_count,
                NULL AS soc_posting_total
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
                CASE WHEN k.recommend_suppress = 'Y' OR k.not_relevant = 'Y' THEN 0 ELSE 1 END AS reliable,
                0 AS posting_count,
                NULL AS soc_posting_total
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
                CASE WHEN a.recommend_suppress = 'Y' OR a.not_relevant = 'Y' THEN 0 ELSE 1 END AS reliable,
                0 AS posting_count,
                NULL AS soc_posting_total
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
                1 AS reliable,
                0 AS posting_count,
                NULL AS soc_posting_total
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
                1 AS reliable,
                0 AS posting_count,
                NULL AS soc_posting_total
            FROM onet_task_ratings tr

            UNION ALL

            -- 6. Detailed Work Activities (DWA)
            SELECT 
                d.soc_code,
                'dwa' AS item_type,
                d.dwa_id AS item_id,
                d.dwa_title AS item_name,
                d.dwa_title AS item_description,
                0.70 AS importance_norm,
                NULL AS level_norm,
                0 AS hot_technology,
                0 AS in_demand,
                NULL AS india_demand_share,
                'onet' AS source,
                1 AS reliable,
                0 AS posting_count,
                NULL AS soc_posting_total
            FROM onet_dwa d

            UNION ALL

            -- 7. Technology Skills (Software & Digital Tools)
            SELECT 
                ts.soc_code,
                'tech' AS item_type,
                'tech_' || LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(ts.example, ' ', '_'), '/', '_'), '-', '_'), '(', ''), ')', '')) AS item_id,
                ts.example AS item_name,
                COALESCE(ts.commodity_title, ts.example) AS item_description,
                CASE WHEN ts.hot_technology = 1 THEN 0.85 ELSE 0.50 END AS importance_norm,
                NULL AS level_norm,
                ts.hot_technology,
                ts.in_demand,
                NULL AS india_demand_share,
                'onet' AS source,
                1 AS reliable,
                0 AS posting_count,
                NULL AS soc_posting_total
            FROM onet_tech_skills ts

            UNION ALL

            -- 8. Tools & Equipment (Physical Equipment, Instruments, Machinery)
            SELECT 
                t.soc_code,
                'tool' AS item_type,
                'tool_' || LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(t.example, ' ', '_'), '/', '_'), '-', '_'), '(', ''), ')', '')) AS item_id,
                t.example AS item_name,
                COALESCE(t.commodity_title, t.example) AS item_description,
                0.65 AS importance_norm,
                NULL AS level_norm,
                0 AS hot_technology,
                0 AS in_demand,
                NULL AS india_demand_share,
                'onet' AS source,
                1 AS reliable,
                0 AS posting_count,
                NULL AS soc_posting_total
            FROM onet_tools t

            UNION ALL

            -- 9. Empirical Indian Market Skills & Curated Domain Competencies
            SELECT 
                ds.soc_code,
                'market_skill' AS item_type,
                ds.skill_normalized AS item_id,
                ds.skill_name AS item_name,
                CASE WHEN ds.source = 'curated' THEN 'Curated domain competency' ELSE 'Extracted from Indian job postings' END AS item_description,
                CASE 
                    WHEN ds.source = 'curated' THEN 0.50 
                    ELSE ROUND(MAX(0.20, MIN(1.0, CAST(ds.posting_count AS REAL) / MAX(1.0, (SELECT MAX(sub.posting_count) FROM skill_demand_by_soc sub WHERE sub.soc_code = ds.soc_code AND sub.region = ds.region)))), 4)
                END AS importance_norm,
                NULL AS level_norm,
                0 AS hot_technology,
                1 AS in_demand,
                CASE WHEN ds.source = 'curated' THEN NULL ELSE ds.share_of_postings END AS india_demand_share,
                ds.source AS source,
                1 AS reliable,
                ds.posting_count AS posting_count,
                ds.soc_posting_total AS soc_posting_total
            FROM skill_demand_by_soc ds
            WHERE ds.region IN ('india', 'curated')
        )
        GROUP BY soc_code, item_type, item_name
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS db_meta (
            schema_version TEXT PRIMARY KEY,
            built_at TEXT NOT NULL,
            onet_version TEXT NOT NULL,
            sources_hash TEXT NOT NULL,
            notes TEXT,
            table_counts TEXT,
            export_hash TEXT
        )
    """)
    for col in ["table_counts", "export_hash"]:
        try:
            cur.execute(f"ALTER TABLE db_meta ADD COLUMN {col} TEXT")
        except Exception:
            pass

    # Compute row counts for all key tables
    table_names = [
        "occupations", "onet_skills", "onet_knowledge", "onet_abilities",
        "onet_work_activities", "onet_tasks", "onet_task_ratings", "onet_dwa",
        "onet_tools", "onet_tech_skills", "onet_job_zones", "india_title_aliases",
        "posting_soc_map", "salary_soc_map", "skill_demand_by_soc"
    ]
    counts = {}
    for t in table_names:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            counts[t] = cur.fetchone()[0]
        except Exception:
            pass
    try:
        cur.execute("SELECT COUNT(*) FROM v_occupation_requirements")
        counts["v_occupation_requirements"] = cur.fetchone()[0]
    except Exception:
        pass

    counts_json = json.dumps(counts, indent=2)

    h = hashlib.sha256(b"vriddhi_generalised_labor_intelligence_v2_2").hexdigest()[:16]
    cur.execute("""
        INSERT OR REPLACE INTO db_meta (schema_version, built_at, onet_version, sources_hash, notes, table_counts, export_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "2.2.0",
        datetime.now(timezone.utc).isoformat(),
        "31.0",
        h,
        "Vriddhi Generalised Labor Intelligence Database v2.2.0 (Official O*NET Tools, Top-50 Market Skills with posting_count, Normalized Names, Neutral Curated Weights)",
        counts_json,
        None
    ))

    conn.commit()
    print(f"  [v_occupation_requirements & db_meta] configured v2.2.0 ({time.time()-t0:.2f}s)")


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
