"""
Data Acquisition Script: Downloads all raw datasets from real, reputable sources.

Sources:
  1. O*NET 31.0 Database (U.S. DOL) — occupations, tasks, skills taxonomy
  2. Kaggle: LinkedIn Job Postings 2023-24 (arshkon/linkedin-job-postings)
  3. Kaggle: 1.3M LinkedIn Jobs & Skills 2024 (asaniczka/1-3m-linkedin-jobs-skills-2024)
  4. Kaggle: Indian Job Market 2024-25 (uom190346a/indian-job-market-dataset-2024-25)
  5. Kaggle: Global AI/ML Salary Trends 2025 (asaniczka/global-ai-ml-data-science-salary-2025)

Usage:
  python data/scripts/01_download_raw.py
"""
import os
import sys
import zipfile
import urllib.request
import shutil
from pathlib import Path
import subprocess

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent  # data/
RAW_DIR = BASE_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def download_file(url: str, dest: Path, description: str) -> bool:
    """Download a file via urllib with progress."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [SKIP] {description} -- already downloaded at {dest.name}")
        return True
    print(f"  [DOWNLOADING] {description} ...")
    print(f"    URL: {url}")
    try:
        urllib.request.urlretrieve(url, str(dest))
        size_mb = dest.stat().st_size / (1024 * 1024)
        print(f"  [DONE] {dest.name} ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to download {description}: {e}")
        return False


def extract_zip(zip_path: Path, extract_to: Path, description: str) -> bool:
    """Extract a zip file into a target directory."""
    if not zip_path.exists():
        print(f"  [SKIP] {description} zip not found: {zip_path}")
        return False
    extract_to.mkdir(parents=True, exist_ok=True)
    print(f"  [EXTRACTING] {description} -> {extract_to.name}/")
    try:
        with zipfile.ZipFile(str(zip_path), 'r') as zf:
            zf.extractall(str(extract_to))
        print(f"  [DONE] Extracted {description}")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to extract {description}: {e}")
        return False


def kaggle_download(slug: str, dest_dir: Path, description: str) -> bool:
    """Download a Kaggle dataset via CLI."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Check if already downloaded (any csv/zip file exists)
    existing = list(dest_dir.glob("*.csv")) + list(dest_dir.glob("*.zip"))
    if existing:
        print(f"  [SKIP] {description} — already downloaded ({len(existing)} files in {dest_dir.name}/)")
        return True

    print(f"  [DOWNLOADING] {description} via Kaggle API ...")
    print(f"    Slug: {slug}")
    try:
        result = subprocess.run(
            ["kaggle", "datasets", "download", "-d", slug, "-p", str(dest_dir), "--unzip"],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode == 0:
            files = list(dest_dir.rglob("*"))
            print(f"  [DONE] {description} — {len(files)} files downloaded")
            return True
        else:
            # Try without --unzip (some datasets don't support it cleanly)
            result2 = subprocess.run(
                ["kaggle", "datasets", "download", "-d", slug, "-p", str(dest_dir)],
                capture_output=True, text=True, timeout=600
            )
            if result2.returncode == 0:
                # Extract any zip files
                for zf in dest_dir.glob("*.zip"):
                    extract_zip(zf, dest_dir, description)
                print(f"  [DONE] {description}")
                return True
            print(f"  [ERROR] Kaggle download failed: {result2.stderr}")
            return False
    except FileNotFoundError:
        print(f"  [ERROR] 'kaggle' CLI not found. Install with: pip install kaggle")
        print(f"           Also ensure ~/.kaggle/kaggle.json exists with your API token.")
        return False
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] Download timed out for {description}")
        return False


def main():
    print("=" * 70)
    print("  MODULE 1: RAW DATA ACQUISITION")
    print("  Career Intelligence & Forecasting Engine")
    print("=" * 70)

    results = {}

    # ─────────────────────────────────────────────────────────────────────
    # 1. O*NET 31.0 Database (CSV)
    # ─────────────────────────────────────────────────────────────────────
    print("\n[1/5] O*NET 31.0 Database (U.S. Department of Labor)")
    onet_zip = RAW_DIR / "onet_31_0_csv.zip"
    onet_dir = RAW_DIR / "onet"
    ok = download_file(
        url="https://www.onetcenter.org/dl_files/database/db_31_0_csv.zip",
        dest=onet_zip,
        description="O*NET 31.0 CSV Database"
    )
    if ok:
        extract_zip(onet_zip, onet_dir, "O*NET 31.0")
    results["O*NET 31.0"] = ok

    # ─────────────────────────────────────────────────────────────────────
    # 2. Kaggle: LinkedIn Job Postings 2023-2024
    # ─────────────────────────────────────────────────────────────────────
    print("\n[2/5] Kaggle: LinkedIn Job Postings 2023-2024")
    results["LinkedIn 2023-24"] = kaggle_download(
        slug="arshkon/linkedin-job-postings",
        dest_dir=RAW_DIR / "linkedin_2023_24",
        description="LinkedIn Job Postings 2023-2024 (124k+ postings)"
    )

    # ─────────────────────────────────────────────────────────────────────
    # 3. Kaggle: 1.3M LinkedIn Jobs & Skills 2024
    # ─────────────────────────────────────────────────────────────────────
    print("\n[3/5] Kaggle: 1.3M LinkedIn Jobs & Skills 2024")
    results["LinkedIn 1.3M 2024"] = kaggle_download(
        slug="asaniczka/1-3m-linkedin-jobs-skills-2024",
        dest_dir=RAW_DIR / "linkedin_1_3m_2024",
        description="1.3M LinkedIn Jobs & Skills 2024"
    )

    # ─────────────────────────────────────────────────────────────────────
    # 4. Kaggle: Indian Job Market 2024-25
    # ─────────────────────────────────────────────────────────────────────
    print("\n[4/5] Kaggle: Indian Job Market 2024-25 (Naukri.com)")
    results["India Jobs 2024-25"] = kaggle_download(
        slug="uom190346a/indian-job-market-dataset-2024-25",
        dest_dir=RAW_DIR / "india_jobs_2024_25",
        description="Indian Job Market 2024-25 (97k+ postings, Naukri.com)"
    )

    # ─────────────────────────────────────────────────────────────────────
    # 5. Kaggle: Global AI/ML Salary Trends 2025
    # ─────────────────────────────────────────────────────────────────────
    print("\n[5/5] Kaggle: Global AI/ML/DS Salary Trends 2025")
    results["AI Salary 2025"] = kaggle_download(
        slug="asaniczka/global-ai-ml-data-science-salary-2025",
        dest_dir=RAW_DIR / "ai_salary_2025",
        description="Global AI/ML/DS Salary 2020-2025 (15k+ records, 50+ countries)"
    )

    # ---------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  ACQUISITION SUMMARY")
    print("=" * 70)
    for source, ok in results.items():
        status = "[SUCCESS]" if ok else "[FAILED]"
        print(f"  {status}  {source}")

    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"\n  [WARN] {len(failed)} source(s) failed. Check error messages above.")
        print("  For Kaggle errors: ensure kaggle.json is at C:\\Users\\<you>\\.kaggle\\kaggle.json")
    else:
        print(f"\n  [SUCCESS] All {len(results)} sources downloaded successfully!")

    print(f"\n  Raw data location: {RAW_DIR}")
    # Show directory sizes
    for child in sorted(RAW_DIR.iterdir()):
        if child.is_dir():
            total_size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
            count = sum(1 for f in child.rglob("*") if f.is_file())
            print(f"    {child.name}/  — {count} files, {total_size / (1024*1024):.1f} MB")
    print()


if __name__ == "__main__":
    main()
