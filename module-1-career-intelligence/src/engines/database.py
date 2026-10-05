"""
Database Interface & Query Engine for Module 1.
Provides optimized, thread-safe access to career_intel.db.
"""
import sqlite3
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "career_intel.db"


class CareerDatabase:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = str(db_path or DB_PATH)

    def get_connection(self) -> sqlite3.Connection:
        """Create a sqlite3 connection with dict-like row access."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def find_occupation(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Fuzzy match an occupation query to standard O*NET occupations.
        Returns the best matching occupation record or None.
        """
        if not query:
            return None
        q = query.strip().lower()
        cleaned_q = re.sub(r'[^a-z0-9\s]', '', q)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            # 1. Exact match on title
            cursor.execute("SELECT * FROM occupations WHERE LOWER(title) = ? LIMIT 1", (q,))
            row = cursor.fetchone()
            if row:
                return dict(row)

            # 2. Substring match
            cursor.execute("SELECT * FROM occupations WHERE LOWER(title) LIKE ? ORDER BY LENGTH(title) ASC LIMIT 1", (f"%{q}%",))
            row = cursor.fetchone()
            if row:
                return dict(row)

            # 3. Match across title words (tokenized)
            words = [w for w in cleaned_q.split() if len(w) > 2]
            if words:
                clause = " AND ".join(["LOWER(title) LIKE ?" for _ in words])
                params = [f"%{w}%" for w in words]
                cursor.execute(f"SELECT * FROM occupations WHERE {clause} ORDER BY LENGTH(title) ASC LIMIT 1", params)
                row = cursor.fetchone()
                if row:
                    return dict(row)

            # 4. Search in job_postings_global or job_postings_india to find mapped soc_code
            cursor.execute("""
                SELECT soc_code_mapped FROM job_postings_global 
                WHERE LOWER(title) LIKE ? AND soc_code_mapped IS NOT NULL LIMIT 1
            """, (f"%{q}%",))
            mapped = cursor.fetchone()
            if mapped and mapped[0]:
                cursor.execute("SELECT * FROM occupations WHERE soc_code = ?", (mapped[0],))
                row = cursor.fetchone()
                if row:
                    return dict(row)

        return None

    def list_occupations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List standard occupations."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT soc_code, title, domain FROM occupations ORDER BY title ASC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_tasks_for_soc(self, soc_code: str) -> List[Dict[str, Any]]:
        """Retrieve all task statements for an occupation."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT task_id, task_description, task_type, importance, relevance, frequency
                FROM occupation_tasks
                WHERE soc_code = ?
                ORDER BY id ASC
            """, (soc_code,))
            return [dict(row) for row in cursor.fetchall()]

    def get_skills_for_soc(self, soc_code: str) -> List[Dict[str, Any]]:
        """Retrieve standard O*NET skills and technologies for an occupation."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT skill_name, skill_category, importance, level
                FROM occupation_skills
                WHERE soc_code = ?
                ORDER BY importance DESC
            """, (soc_code,))
            skills = [dict(row) for row in cursor.fetchall()]

            cursor.execute("""
                SELECT technology_name, hot_technology, in_demand
                FROM occupation_tech
                WHERE soc_code = ?
                ORDER BY hot_technology DESC, in_demand DESC
            """, (soc_code,))
            tech = [dict(row) for row in cursor.fetchall()]
            return {"skills": skills, "technologies": tech}

    def get_posting_history(self, title: str) -> Dict[str, Any]:
        """
        Query real posting volume, yearly velocity, and location distribution
        for both India and Global markets.
        """
        clean_title = re.sub(r'[^a-z0-9\s]', '', title.lower()).strip()
        words = [w for w in clean_title.split() if len(w) > 2]
        like_pattern = f"%{words[0]}%" if words else f"%{clean_title}%"

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Global postings stats
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_postings,
                    COUNT(DISTINCT company) as unique_companies,
                    AVG(salary_median_usd) as avg_salary,
                    SUM(CASE WHEN listed_year = 2024 THEN 1 ELSE 0 END) as postings_2024,
                    SUM(CASE WHEN listed_year >= 2025 THEN 1 ELSE 0 END) as postings_2025_plus
                FROM job_postings_global
                WHERE LOWER(title) LIKE ?
            """, (like_pattern,))
            global_summary = dict(cursor.fetchone() or {})

            # Global top locations
            cursor.execute("""
                SELECT location, COUNT(*) as cnt
                FROM job_postings_global
                WHERE LOWER(title) LIKE ? AND location IS NOT NULL AND location != ''
                GROUP BY location
                ORDER BY cnt DESC
                LIMIT 5
            """, (like_pattern,))
            global_locations = [f"{row['location']} ({row['cnt']})" for row in cursor.fetchall()]

            # India postings stats
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_postings,
                    COUNT(DISTINCT company) as unique_companies,
                    AVG(salary_min_usd) as avg_min_usd,
                    AVG(salary_max_usd) as avg_max_usd,
                    AVG(salary_min_inr) as avg_min_inr,
                    AVG(salary_max_inr) as avg_max_inr,
                    SUM(CASE WHEN listed_year <= 2017 THEN 1 ELSE 0 END) as historical_postings,
                    SUM(CASE WHEN listed_year >= 2024 THEN 1 ELSE 0 END) as modern_postings
                FROM job_postings_india
                WHERE LOWER(title) LIKE ?
            """, (like_pattern,))
            india_summary = dict(cursor.fetchone() or {})

            # India top cities
            cursor.execute("""
                SELECT city, COUNT(*) as cnt
                FROM job_postings_india
                WHERE LOWER(title) LIKE ? AND city IS NOT NULL AND city != ''
                GROUP BY city
                ORDER BY cnt DESC
                LIMIT 5
            """, (like_pattern,))
            india_cities = [f"{row['city']} ({row['cnt']})" for row in cursor.fetchall()]

            return {
                "global": {
                    "total": global_summary.get("total_postings", 0) or 0,
                    "unique_companies": global_summary.get("unique_companies", 0) or 0,
                    "avg_salary_usd": global_summary.get("avg_salary"),
                    "postings_2024": global_summary.get("postings_2024", 0) or 0,
                    "postings_2025_plus": global_summary.get("postings_2025_plus", 0) or 0,
                    "top_locations": global_locations,
                },
                "india": {
                    "total": india_summary.get("total_postings", 0) or 0,
                    "unique_companies": india_summary.get("unique_companies", 0) or 0,
                    "avg_min_inr": india_summary.get("avg_min_inr"),
                    "avg_max_inr": india_summary.get("avg_max_inr"),
                    "historical_postings": india_summary.get("historical_postings", 0) or 0,
                    "modern_postings": india_summary.get("modern_postings", 0) or 0,
                    "top_cities": india_cities,
                }
            }

    def get_top_skills(self, title: str, limit: int = 8) -> Dict[str, List[str]]:
        """Retrieve top extracted skills for an occupation in India and Global markets."""
        clean_title = re.sub(r'[^a-z0-9\s]', '', title.lower()).strip()
        words = [w for w in clean_title.split() if len(w) > 2]
        like_pattern = f"%{words[0]}%" if words else f"%{clean_title}%"

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Global top skills
            cursor.execute("""
                SELECT skill_normalized, SUM(frequency) as freq
                FROM skill_demand
                WHERE region = 'global' AND (LOWER(occupation_context) LIKE ? OR ? LIKE '%' || LOWER(occupation_context) || '%')
                GROUP BY skill_normalized
                ORDER BY freq DESC
                LIMIT ?
            """, (like_pattern, clean_title, limit))
            global_skills = [row["skill_normalized"] for row in cursor.fetchall()]

            # India top skills
            cursor.execute("""
                SELECT skill_normalized, SUM(frequency) as freq
                FROM skill_demand
                WHERE region = 'india' AND (LOWER(occupation_context) LIKE ? OR ? LIKE '%' || LOWER(occupation_context) || '%')
                GROUP BY skill_normalized
                ORDER BY freq DESC
                LIMIT ?
            """, (like_pattern, clean_title, limit))
            india_skills = [row["skill_normalized"] for row in cursor.fetchall()]

            # If empty context, fall back to aggregate top skills
            if not global_skills:
                cursor.execute("SELECT skill_normalized FROM skill_demand WHERE region = 'global' GROUP BY skill_normalized ORDER BY SUM(frequency) DESC LIMIT ?", (limit,))
                global_skills = [row[0] for row in cursor.fetchall()]
            if not india_skills:
                cursor.execute("SELECT skill_normalized FROM skill_demand WHERE region = 'india' GROUP BY skill_normalized ORDER BY SUM(frequency) DESC LIMIT ?", (limit,))
                india_skills = [row[0] for row in cursor.fetchall()]

            return {"global": global_skills, "india": india_skills}

    def get_yearly_breakdown(self, title: str) -> Dict[str, Dict[int, int]]:
        """
        Query actual historical postings counts grouped by year for both India and Global.
        """
        clean_title = re.sub(r'[^a-z0-9\s]', '', title.lower()).strip()
        words = [w for w in clean_title.split() if len(w) > 2]
        like_pattern = f"%{words[0]}%" if words else f"%{clean_title}%"

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Global yearly counts
            cursor.execute("""
                SELECT listed_year, COUNT(*) as cnt
                FROM job_postings_global
                WHERE LOWER(title) LIKE ? AND listed_year IS NOT NULL
                GROUP BY listed_year
                ORDER BY listed_year ASC
            """, (like_pattern,))
            global_yearly = {row["listed_year"]: row["cnt"] for row in cursor.fetchall()}

            # India yearly counts
            cursor.execute("""
                SELECT listed_year, COUNT(*) as cnt
                FROM job_postings_india
                WHERE LOWER(title) LIKE ? AND listed_year IS NOT NULL
                GROUP BY listed_year
                ORDER BY listed_year ASC
            """, (like_pattern,))
            india_yearly = {row["listed_year"]: row["cnt"] for row in cursor.fetchall()}

            return {"global": global_yearly, "india": india_yearly}

    def search_occupations_by_domain(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Semantic and keyword domain search finding matching occupations and career pathways.
        Matches against occupation title, description, domain, and associated skills.
        """
        clean_q = re.sub(r'[^a-z0-9\s]', '', query.lower()).strip()
        tokens = [t for t in clean_q.split() if len(t) > 2]
        if not tokens:
            return self.list_occupations(limit=limit)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            results = []

            # Match on title and description
            title_likes = " OR ".join(["LOWER(title) LIKE ?" for _ in tokens])
            desc_likes = " OR ".join(["LOWER(description) LIKE ?" for _ in tokens])
            params = [f"%{t}%" for t in tokens] * 2

            query_sql = f"""
                SELECT soc_code, title, description, domain,
                    (CASE WHEN LOWER(title) LIKE ? THEN 3.0 ELSE 1.0 END) as relevance
                FROM occupations
                WHERE ({title_likes}) OR ({desc_likes})
                ORDER BY relevance DESC, LENGTH(title) ASC
                LIMIT ?
            """
            cursor.execute(query_sql, [f"%{tokens[0]}%"] + params + [limit])
            rows = cursor.fetchall()

            for r in rows:
                soc = r["soc_code"]
                # Fetch matching skills
                cursor.execute("""
                    SELECT skill_name FROM occupation_skills 
                    WHERE soc_code = ? ORDER BY importance DESC LIMIT 4
                """, (soc,))
                skills = [s[0] for s in cursor.fetchall()]

                results.append({
                    "soc_code": soc,
                    "title": r["title"],
                    "description": r["description"],
                    "domain": r["domain"] or "Technology / Engineering",
                    "matching_skills": skills,
                    "relevance_score": min(round(float(r["relevance"]) * 0.35 + 0.50, 2), 0.98)
                })

            if not results:
                # Fallback to top occupations
                return self.list_occupations(limit=limit)

            return results
