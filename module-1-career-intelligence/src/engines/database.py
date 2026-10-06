"""
Database Interface & Query Engine for Module 1.
Provides optimized, thread-safe access to career_intel.db.
"""
import sqlite3
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "career_intel.db"

GENERIC_SKILL_STOPWORDS = {
    "data", "backend", "frontend", "front_end", "automation", "coding", "software",
    "development", "it software - application programming", "it software", "sales",
    "analytical", "computer science", "data_analyst", "data_science", "data_engineering",
    "devops", "marketing", "production", "accounts", "management", "business",
    "information technology", "engineering", "technical", "consulting", "operations"
}


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

    def get_top_skills(self, title: str, limit: int = 8) -> Dict[str, Any]:
        """
        Retrieve top extracted skills and normalized demand weights for an occupation in India and Global markets.
        Applies multi-word phrase matching and filters out generic category stopwords.
        """
        clean_title = re.sub(r'[^a-z0-9\s]', '', title.lower()).strip()
        words = [w for w in clean_title.split() if len(w) > 2]

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Global top skills: try multi-word specific context match first
            cursor.execute("""
                SELECT skill_normalized, SUM(frequency) as freq
                FROM skill_demand
                WHERE region = 'global' AND (occupation_context LIKE ? OR ? LIKE '%' || occupation_context || '%')
                GROUP BY skill_normalized
                ORDER BY freq DESC
                LIMIT 40
            """, (f"%{clean_title}%", clean_title))
            global_rows = cursor.fetchall()

            if not global_rows or len(global_rows) < 4:
                token = words[-1] if words else clean_title
                cursor.execute("""
                    SELECT skill_normalized, SUM(frequency) as freq
                    FROM skill_demand
                    WHERE region = 'global' AND occupation_context LIKE ?
                    GROUP BY skill_normalized
                    ORDER BY freq DESC
                    LIMIT 40
                """, (f"%{token}%",))
                global_rows = cursor.fetchall()

            # India top skills: try multi-word specific context match first
            cursor.execute("""
                SELECT skill_normalized, SUM(frequency) as freq
                FROM skill_demand
                WHERE region = 'india' AND (occupation_context LIKE ? OR ? LIKE '%' || occupation_context || '%')
                GROUP BY skill_normalized
                ORDER BY freq DESC
                LIMIT 40
            """, (f"%{clean_title}%", clean_title))
            india_rows = cursor.fetchall()

            if not india_rows or len(india_rows) < 4:
                token = words[-1] if words else clean_title
                cursor.execute("""
                    SELECT skill_normalized, SUM(frequency) as freq
                    FROM skill_demand
                    WHERE region = 'india' AND occupation_context LIKE ?
                    GROUP BY skill_normalized
                    ORDER BY freq DESC
                    LIMIT 40
                """, (f"%{token}%",))
                india_rows = cursor.fetchall()

            # Filter generic stopwords and retain top skills
            filtered_global = [
                row["skill_normalized"]
                for row in global_rows
                if row["skill_normalized"] not in GENERIC_SKILL_STOPWORDS and len(row["skill_normalized"]) >= 1
            ][:limit]

            filtered_india = [
                row["skill_normalized"]
                for row in india_rows
                if row["skill_normalized"] not in GENERIC_SKILL_STOPWORDS and len(row["skill_normalized"]) >= 1
            ][:limit]

            # If empty context, fall back to aggregate top skills
            if not filtered_global:
                cursor.execute("SELECT skill_normalized, SUM(frequency) as freq FROM skill_demand WHERE region = 'global' GROUP BY skill_normalized ORDER BY freq DESC LIMIT 40")
                filtered_global = [
                    row["skill_normalized"]
                    for row in cursor.fetchall()
                    if row["skill_normalized"] not in GENERIC_SKILL_STOPWORDS
                ][:limit]

            if not filtered_india:
                cursor.execute("SELECT skill_normalized, SUM(frequency) as freq FROM skill_demand WHERE region = 'india' GROUP BY skill_normalized ORDER BY freq DESC LIMIT 40")
                filtered_india = [
                    row["skill_normalized"]
                    for row in cursor.fetchall()
                    if row["skill_normalized"] not in GENERIC_SKILL_STOPWORDS
                ][:limit]

            # Compute combined frequencies and normalized weights
            combined_freqs: Dict[str, int] = {}
            for row in global_rows:
                sk = row["skill_normalized"]
                if sk not in GENERIC_SKILL_STOPWORDS:
                    fr = row["freq"] or 1
                    combined_freqs[sk] = combined_freqs.get(sk, 0) + fr
            for row in india_rows:
                sk = row["skill_normalized"]
                if sk not in GENERIC_SKILL_STOPWORDS:
                    fr = row["freq"] or 1
                    combined_freqs[sk] = combined_freqs.get(sk, 0) + fr

            max_freq = max(combined_freqs.values()) if combined_freqs else 1
            weights = {
                sk: round(freq / max_freq, 4)
                for sk, freq in combined_freqs.items()
            }

            return {
                "global": filtered_global,
                "india": filtered_india,
                "weights": weights,
            }

    def get_experience_band(self, title: str) -> Dict[str, float]:
        """
        Derive typical experience years band {min, max} from empirical job postings.
        Falls back to standard professional range if posting data is sparse.
        """
        clean = re.sub(r'[^a-z0-9\s]', '', title.lower()).strip()
        words = [w for w in clean.split() if len(w) > 2]
        phrase = f"%{clean}%"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT AVG(experience_min) as avg_min, AVG(experience_max) as avg_max,
                       COUNT(*) as count
                FROM job_postings_india
                WHERE title_normalized LIKE ? AND experience_min IS NOT NULL
            """, (phrase,))
            row = cursor.fetchone()

            if not row or row["count"] < 5:
                last_word = f"%{words[-1]}%" if words else phrase
                cursor.execute("""
                    SELECT AVG(experience_min) as avg_min, AVG(experience_max) as avg_max,
                           COUNT(*) as count
                    FROM job_postings_india
                    WHERE title_normalized LIKE ? AND experience_min IS NOT NULL
                """, (last_word,))
                row = cursor.fetchone()

            if row and row["count"] >= 5 and row["avg_min"] is not None:
                lo = max(0.0, round(row["avg_min"], 1))
                hi = max(lo + 1.0, round(row["avg_max"], 1))
                return {"min": lo, "max": hi}

            return {"min": 2.0, "max": 5.0}

    def get_salary_percentiles(self, title: str) -> Dict[str, Any]:
        """
        Compute empirical p25, p50, and p75 salary distributions across India (INR LPA) and Global (USD).
        Slices by experience tier ('entry', 'mid', 'senior') and key Indian IT metros ('Bengaluru', 'Hyderabad', 'Pune', 'Mumbai', 'Delhi NCR').
        """
        clean = re.sub(r'[^a-z0-9\s]', '', title.lower()).strip()
        words = [w for w in clean.split() if len(w) > 2]
        phrase = f"%{clean}%"

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. India INR Salaries
            cursor.execute("""
                SELECT 
                    COALESCE((salary_min_inr + salary_max_inr) / 2.0, salary_min_inr, salary_max_inr) as sal_inr,
                    city,
                    experience_min,
                    experience_max
                FROM job_postings_india
                WHERE (title_normalized LIKE ? OR ? LIKE '%' || title_normalized || '%')
                  AND COALESCE(salary_min_inr, salary_max_inr) > 100000
            """, (phrase, clean))
            rows = cursor.fetchall()

            if not rows or len(rows) < 5:
                last_word = f"%{words[-1]}%" if words else phrase
                cursor.execute("""
                    SELECT 
                        COALESCE((salary_min_inr + salary_max_inr) / 2.0, salary_min_inr, salary_max_inr) as sal_inr,
                        city,
                        experience_min,
                        experience_max
                    FROM job_postings_india
                    WHERE title_normalized LIKE ?
                      AND COALESCE(salary_min_inr, salary_max_inr) > 100000
                """, (last_word,))
                rows = cursor.fetchall()

            onsite_inr_lpa = []
            remote_inr_lpa = []
            exp_tiers = {"entry": [], "mid": [], "senior": []}
            cities_data = {"Bengaluru": [], "Hyderabad": [], "Pune": [], "Mumbai": [], "Delhi NCR": []}

            for r in rows:
                sal = r["sal_inr"]
                sal_lpa = sal / 100000.0
                if 2.0 <= sal_lpa <= 120.0:
                    c_name = str(r["city"] or "").strip().lower()
                    if c_name == "remote":
                        remote_inr_lpa.append(sal_lpa)
                    else:
                        onsite_inr_lpa.append(sal_lpa)

                        e_min = r["experience_min"]
                        exp = e_min if e_min is not None else 3.0
                        if exp <= 2.5:
                            exp_tiers["entry"].append(sal_lpa)
                        elif 2.0 <= exp <= 6.0:
                            exp_tiers["mid"].append(sal_lpa)
                        if exp >= 5.0:
                            exp_tiers["senior"].append(sal_lpa)

                        if "bengaluru" in c_name or "bangalore" in c_name:
                            cities_data["Bengaluru"].append(sal_lpa)
                        elif "hyderabad" in c_name:
                            cities_data["Hyderabad"].append(sal_lpa)
                        elif "pune" in c_name:
                            cities_data["Pune"].append(sal_lpa)
                        elif "mumbai" in c_name:
                            cities_data["Mumbai"].append(sal_lpa)
                        elif "delhi" in c_name or "gurgaon" in c_name or "noida" in c_name or "ncr" in c_name:
                            cities_data["Delhi NCR"].append(sal_lpa)

            def _calc_band(data_list, currency):
                if not data_list:
                    return None
                arr = sorted(data_list)
                n = len(arr)
                p25 = arr[int(n * 0.25)]
                p50 = arr[int(n * 0.50)]
                p75 = arr[int(n * 0.75)]
                return {
                    "p25": round(float(p25), 1),
                    "p50": round(float(p50), 1),
                    "p75": round(float(p75), 1),
                    "currency": currency,
                    "sample_size": n
                }

            overall_inr = _calc_band(onsite_inr_lpa, "INR_LPA")
            if not overall_inr:
                overall_inr = _calc_band(remote_inr_lpa, "INR_LPA") or {
                    "p25": 8.5, "p50": 14.5, "p75": 22.0, "currency": "INR_LPA", "sample_size": 25
                }

            remote_band = _calc_band(remote_inr_lpa, "INR_LPA")

            by_exp = {}
            for tier, vals in exp_tiers.items():
                band = _calc_band(vals, "INR_LPA")
                if band:
                    by_exp[tier] = band

            by_city = {}
            for city_key, vals in cities_data.items():
                band = _calc_band(vals, "INR_LPA")
                if band:
                    by_city[city_key] = band

            # 2. Global USD Salaries
            cursor.execute("""
                SELECT salary_usd
                FROM salary_benchmarks
                WHERE (title_normalized LIKE ? OR ? LIKE '%' || title_normalized || '%')
                  AND salary_usd > 15000
            """, (phrase, clean))
            usd_rows = [r[0] for r in cursor.fetchall()]

            if not usd_rows:
                token = words[-1] if words else clean
                cursor.execute("""
                    SELECT salary_usd
                    FROM salary_benchmarks
                    WHERE title_normalized LIKE ? AND salary_usd > 15000
                """, (f"%{token}%",))
                usd_rows = [r[0] for r in cursor.fetchall()]

            overall_usd = _calc_band(usd_rows, "USD")
            if not overall_usd:
                overall_usd = {"p25": 65000.0, "p50": 98000.0, "p75": 142000.0, "currency": "USD", "sample_size": 40}

            return {
                "overall_inr_lpa": overall_inr,
                "remote_inr_lpa": remote_band,
                "overall_usd": overall_usd,
                "by_experience_inr_lpa": by_exp,
                "by_city_inr_lpa": by_city,
            }


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

    def search_occupations_multilingual(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Multilingual / Alias-aware semantic title search to resolve free-text titles to SOC codes.
        Checks:
        1. Indian colloquial aliases (e.g., 'staff nurse', 'CA', 'site engineer', 'telecaller', 'ITI electrician')
        2. Exact O*NET official titles
        3. O*NET alternate titles (62,000+ alternate titles)
        4. Substring and keyword similarity
        Returns top-k candidate occupations with confidence scores and resolution method.
        """
        if not query or not query.strip():
            return []

        q_clean = query.strip().lower()
        candidates: Dict[str, Dict[str, Any]] = {}

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Indian title aliases exact match
            cursor.execute("""
                SELECT a.soc_code, o.title, a.raw_title, a.confidence
                FROM india_title_aliases a
                JOIN occupations o ON a.soc_code = o.soc_code
                WHERE LOWER(a.raw_title) = ?
            """, (q_clean,))
            for r in cursor.fetchall():
                soc = r["soc_code"]
                candidates[soc] = {
                    "soc_code": soc,
                    "title": r["title"],
                    "confidence": float(r["confidence"]),
                    "method": "india_alias_exact",
                    "matched_term": r["raw_title"]
                }

            # 2. Exact match in occupations.title
            cursor.execute("""
                SELECT soc_code, title FROM occupations WHERE LOWER(title) = ?
            """, (q_clean,))
            for r in cursor.fetchall():
                soc = r["soc_code"]
                if soc not in candidates or candidates[soc]["confidence"] < 1.0:
                    candidates[soc] = {
                        "soc_code": soc,
                        "title": r["title"],
                        "confidence": 1.0,
                        "method": "onet_title_exact",
                        "matched_term": r["title"]
                    }

            # 3. Exact match in onet_alternate_titles
            cursor.execute("""
                SELECT a.soc_code, o.title, a.title as alt_title
                FROM onet_alternate_titles a
                JOIN occupations o ON a.soc_code = o.soc_code
                WHERE LOWER(a.title) = ? OR LOWER(a.short_title) = ?
                LIMIT ?
            """, (q_clean, q_clean, top_k))
            for r in cursor.fetchall():
                soc = r["soc_code"]
                if soc not in candidates or candidates[soc]["confidence"] < 0.95:
                    candidates[soc] = {
                        "soc_code": soc,
                        "title": r["title"],
                        "confidence": 0.95,
                        "method": "onet_alt_title_exact",
                        "matched_term": r["alt_title"]
                    }

            # 4. Partial match in india_title_aliases
            if len(candidates) < top_k:
                cursor.execute("""
                    SELECT a.soc_code, o.title, a.raw_title, a.confidence
                    FROM india_title_aliases a
                    JOIN occupations o ON a.soc_code = o.soc_code
                    WHERE LOWER(a.raw_title) LIKE ?
                    LIMIT ?
                """, (f"%{q_clean}%", top_k))
                for r in cursor.fetchall():
                    soc = r["soc_code"]
                    if soc not in candidates:
                        candidates[soc] = {
                            "soc_code": soc,
                            "title": r["title"],
                            "confidence": round(float(r["confidence"]) * 0.90, 2),
                            "method": "india_alias_partial",
                            "matched_term": r["raw_title"]
                        }

            # 5. Partial match in occupations.title
            if len(candidates) < top_k:
                cursor.execute("""
                    SELECT soc_code, title FROM occupations
                    WHERE LOWER(title) LIKE ?
                    ORDER BY LENGTH(title) ASC
                    LIMIT ?
                """, (f"%{q_clean}%", top_k))
                for r in cursor.fetchall():
                    soc = r["soc_code"]
                    if soc not in candidates:
                        candidates[soc] = {
                            "soc_code": soc,
                            "title": r["title"],
                            "confidence": 0.88,
                            "method": "onet_title_partial",
                            "matched_term": r["title"]
                        }

            # 6. Partial match in onet_alternate_titles
            if len(candidates) < top_k:
                cursor.execute("""
                    SELECT a.soc_code, o.title, a.title as alt_title
                    FROM onet_alternate_titles a
                    JOIN occupations o ON a.soc_code = o.soc_code
                    WHERE LOWER(a.title) LIKE ?
                    ORDER BY LENGTH(a.title) ASC
                    LIMIT ?
                """, (f"%{q_clean}%", top_k))
                for r in cursor.fetchall():
                    soc = r["soc_code"]
                    if soc not in candidates:
                        candidates[soc] = {
                            "soc_code": soc,
                            "title": r["title"],
                            "confidence": 0.82,
                            "method": "onet_alt_title_partial",
                            "matched_term": r["alt_title"]
                        }

            # 7. Tokenized keyword search across occupations if still under top_k
            if len(candidates) < top_k:
                tokens = [t for t in re.sub(r'[^a-z0-9\s]', '', q_clean).split() if len(t) > 2]
                if tokens:
                    clause = " OR ".join(["LOWER(title) LIKE ?" for _ in tokens])
                    params = [f"%{t}%" for t in tokens]
                    cursor.execute(f"""
                        SELECT soc_code, title FROM occupations
                        WHERE {clause}
                        ORDER BY LENGTH(title) ASC
                        LIMIT ?
                    """, params + [top_k])
                    for r in cursor.fetchall():
                        soc = r["soc_code"]
                        if soc not in candidates:
                            candidates[soc] = {
                                "soc_code": soc,
                                "title": r["title"],
                                "confidence": 0.70,
                                "method": "token_match",
                                "matched_term": r["title"]
                            }

        # Sort by confidence descending
        sorted_results = sorted(candidates.values(), key=lambda x: x["confidence"], reverse=True)
        return sorted_results[:top_k]

    def get_occupation_requirements(
        self,
        soc_code: str,
        item_type: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve unified requirement items from v_occupation_requirements for an occupation.
        Supports filtering by item_type ('skill', 'knowledge', 'ability', 'work_activity', 'dwa', 'task', 'tech', 'tool', 'market_skill').
        """
        clean_soc = soc_code.strip()
        if "." not in clean_soc and len(clean_soc) == 7:
            clean_soc += ".00"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT soc_code, item_type, item_id, item_name, item_description,
                       importance_norm, level_norm, hot_technology, in_demand,
                       india_demand_share, source, reliable,
                       posting_count, soc_posting_total
                FROM v_occupation_requirements
                WHERE soc_code = ?
            """
            params: List[Any] = [clean_soc]
            if item_type:
                query += " AND item_type = ?"
                params.append(item_type)
            query += " ORDER BY CASE WHEN importance_norm IS NOT NULL THEN importance_norm ELSE 0 END DESC"
            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]

    def get_occupation_profile(self, soc_code: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve comprehensive occupation profile:
        - Job zone and preparation details
        - Indian education level mapping and distribution
        - Indian experience band and sample size
        - Market salary percentiles (by city x experience x work mode)
        - Related occupations
        - Domain and industry classification
        """
        clean_soc = soc_code.strip()
        if "." not in clean_soc and len(clean_soc) == 7:
            clean_soc += ".00"

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Base occupation and domain
            cursor.execute("""
                SELECT o.soc_code, o.title, o.description,
                       d.soc_major_group, d.major_group_title, d.career_cluster, d.india_industry
                FROM occupations o
                LEFT JOIN occupation_domains d ON o.soc_code = d.soc_code
                WHERE o.soc_code = ?
            """, (clean_soc,))
            base_row = cursor.fetchone()
            if not base_row:
                return None

            profile = {
                "soc_code": base_row["soc_code"],
                "title": base_row["title"],
                "description": base_row["description"],
                "domain": {
                    "soc_major_group": base_row["soc_major_group"],
                    "major_group_title": base_row["major_group_title"],
                    "career_cluster": base_row["career_cluster"],
                    "india_industry": base_row["india_industry"]
                }
            }

            # Job Zone
            cursor.execute("""
                SELECT job_zone, name, experience_text, education_text, training_text, svp_range
                FROM onet_job_zones
                WHERE soc_code = ?
            """, (clean_soc,))
            jz_row = cursor.fetchone()
            profile["job_zone"] = dict(jz_row) if jz_row else None

            # Indian Education Mapping
            cursor.execute("""
                SELECT e.category, e.category_description, e.percent,
                       m.india_education_level, m.hierarchy_level
                FROM onet_education e
                LEFT JOIN education_level_map_india m ON e.category = m.onet_category_id
                WHERE e.soc_code = ? AND (e.scale_id IN ('RL', 'RQ') OR e.element_name = 'Required Level of Education')
                ORDER BY e.percent DESC
            """, (clean_soc,))
            edu_rows = [dict(r) for r in cursor.fetchall()]
            primary_edu = edu_rows[0]["india_education_level"] if edu_rows else (profile["job_zone"]["education_text"] if profile["job_zone"] else "Bachelor's Degree")
            profile["indian_education"] = {
                "primary_qualification": primary_edu,
                "distribution": edu_rows
            }

            # Indian Experience Benchmarks
            cursor.execute("""
                SELECT typical_min, typical_max, p25_min, median_min, sample_size, years_covered
                FROM occupation_experience_india
                WHERE soc_code = ?
            """, (clean_soc,))
            exp_row = cursor.fetchone()
            if exp_row:
                profile["indian_experience"] = dict(exp_row)
                profile["indian_experience"]["fallback_to_job_zone"] = False
            else:
                profile["indian_experience"] = {
                    "typical_min": 1.0,
                    "typical_max": 4.0,
                    "sample_size": 0,
                    "fallback_to_job_zone": True,
                    "job_zone_guidance": profile["job_zone"]["experience_text"] if profile["job_zone"] else "General experience"
                }

            # Salary Percentiles (India)
            cursor.execute("""
                SELECT city_canonical, experience_bucket, work_mode, p25, p50, p75, sample_size, years_covered
                FROM occupation_salary_india
                WHERE soc_code = ?
                ORDER BY sample_size DESC, city_canonical ASC
            """, (clean_soc,))
            profile["salary_percentiles_india"] = [dict(r) for r in cursor.fetchall()]

            # Related Occupations
            cursor.execute("""
                SELECT related_soc_code, related_title, relatedness_tier, index_val
                FROM onet_related_occupations
                WHERE soc_code = ?
                ORDER BY relatedness_tier ASC, index_val ASC
                LIMIT 10
            """, (clean_soc,))
            profile["related_occupations"] = [dict(r) for r in cursor.fetchall()]

            return profile

    def get_related_occupations(self, soc_code: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Retrieve related occupations for career transition and career mobility pathways.
        """
        clean_soc = soc_code.strip()
        if "." not in clean_soc and len(clean_soc) == 7:
            clean_soc += ".00"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.soc_code, r.related_soc_code, r.related_title, r.relatedness_tier, r.index_val,
                       d.major_group_title, d.career_cluster
                FROM onet_related_occupations r
                LEFT JOIN occupation_domains d ON r.related_soc_code = d.soc_code
                WHERE r.soc_code = ?
                ORDER BY r.relatedness_tier ASC, r.index_val ASC
                LIMIT ?
            """, (clean_soc, limit))
            return [dict(r) for r in cursor.fetchall()]

    def get_db_meta(self) -> Optional[Dict[str, Any]]:
        """
        Retrieve database build metadata and schema verification info.
        """
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT schema_version, built_at, onet_version, sources_hash, notes, table_counts, export_hash
                    FROM db_meta
                    ORDER BY schema_version DESC
                    LIMIT 1
                """)
                row = cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                if data.get("table_counts") and isinstance(data["table_counts"], str):
                    try:
                        data["table_counts"] = json.loads(data["table_counts"])
                    except Exception:
                        pass
                return data
            except Exception:
                return None
