"""
Script to export unified requirements (v_occupation_requirements) for 15 cross-industry test occupations.
Output: data/m1_occupation_requirements_export.json
Provides mocks and fixtures for Module 2 & 3 development and testing.
"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

TEST_SOCS = [
    ("29-1141.00", "Healthcare", "Registered Nurses"),
    ("29-1051.00", "Healthcare", "Pharmacists"),
    ("31-9092.00", "Healthcare", "Medical Assistants"),
    ("13-2011.00", "Finance", "Accountants and Auditors"),
    ("13-2072.00", "Finance", "Loan Officers"),
    ("25-2031.00", "Education", "Secondary School Teachers"),
    ("41-4012.00", "Sales / Service", "Sales Representatives, Wholesale and Manufacturing, Except Technical and Scientific Products"),
    ("43-4051.00", "Sales / Service", "Customer Service Representatives"),
    ("17-2141.00", "Engineering / Trades", "Mechanical Engineers"),
    ("17-2051.00", "Engineering / Trades", "Civil Engineers"),
    ("47-2111.00", "Engineering / Trades", "Electricians"),
    ("35-1011.00", "Hospitality / Logistics", "Chefs and Head Cooks"),
    ("53-3032.00", "Hospitality / Logistics", "Heavy and Tractor-Trailer Truck Drivers"),
    ("27-1024.00", "Creative", "Graphic Designers"),
    ("15-2051.00", "Tech", "Data Scientists")
]

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "career_intel.db"
OUTPUT_PATH = BASE_DIR / "data" / "m1_occupation_requirements_export.json"


def run_export():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    export_data = {
        "metadata": {
            "version": "2.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "description": "Static mock export of unified requirements (v_occupation_requirements) for 15 cross-industry test occupations, supporting Module 2 & 3 semantic engine development and testing.",
            "target_occupations_count": len(TEST_SOCS)
        },
        "occupations": {}
    }

    for soc, industry_group, fallback_name in TEST_SOCS:
        c.execute("SELECT title, description FROM occupations WHERE soc_code = ?", (soc,))
        occ = c.fetchone()
        title = occ["title"] if occ else fallback_name
        desc = occ["description"] if occ else ""

        c.execute("SELECT major_group_title, career_cluster, india_industry FROM occupation_domains WHERE soc_code = ?", (soc,))
        dom = c.fetchone()

        c.execute("SELECT job_zone, name, education_text FROM onet_job_zones WHERE soc_code = ?", (soc,))
        jz = c.fetchone()

        c.execute("""
            SELECT soc_code, item_type, item_id, item_name, item_description,
                   importance_norm, level_norm, hot_technology, in_demand,
                   india_demand_share, source, reliable
            FROM v_occupation_requirements
            WHERE soc_code = ?
            ORDER BY CASE WHEN importance_norm IS NOT NULL THEN importance_norm ELSE 0 END DESC
        """, (soc,))
        reqs = [dict(r) for r in c.fetchall()]

        breakdown = {
            "skills": sum(1 for r in reqs if r["item_type"] == "skill"),
            "knowledge": sum(1 for r in reqs if r["item_type"] == "knowledge"),
            "abilities": sum(1 for r in reqs if r["item_type"] == "ability"),
            "work_activities": sum(1 for r in reqs if r["item_type"] == "work_activity"),
            "tasks": sum(1 for r in reqs if r["item_type"] == "task"),
            "dwa": sum(1 for r in reqs if r["item_type"] == "dwa"),
            "tech": sum(1 for r in reqs if r["item_type"] == "tech"),
            "tools": sum(1 for r in reqs if r["item_type"] == "tool"),
            "market_skills": sum(1 for r in reqs if r["item_type"] == "market_skill"),
        }

        export_data["occupations"][soc] = {
            "soc_code": soc,
            "title": title,
            "industry_group": industry_group,
            "description": desc,
            "domain": dict(dom) if dom else {},
            "job_zone": dict(jz) if jz else {},
            "total_requirements": len(reqs),
            "requirements_breakdown": breakdown,
            "requirements": reqs
        }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2)

    print(f"Export successfully written to {OUTPUT_PATH}")
    for soc, data in export_data["occupations"].items():
        title_trunc = data["title"][:32]
        tot = data["total_requirements"]
        sk = data["requirements_breakdown"]["skills"]
        kn = data["requirements_breakdown"]["knowledge"]
        ab = data["requirements_breakdown"]["abilities"]
        print(f"[{soc}] {title_trunc:32} -> {tot} reqs (skills:{sk}, know:{kn}, abils:{ab})")


if __name__ == "__main__":
    run_export()
