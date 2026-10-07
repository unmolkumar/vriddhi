"""
AI & Automation Exposure Engine.
Evaluates granular task-level transformation according to O*NET task decomposition:
  Occupation -> Tasks -> AI Exposure per Task -> Potential Task Transformation -> Potential Occupation Impact.
"""
import re
from typing import Dict, List, Tuple, Any

# High-exposure task indicators (routine data, repetitive codification, transcription, boilerplate)
# High-exposure task indicators (routine data, repetitive codification, transcription, boilerplate)
HIGH_AUTOMATION_KEYWORDS = [
    "record", "transcribe", "compile", "calculate", "enter data", "format",
    "schedule", "proofread", "file", "sort", "routine", "template", "standardized",
    "inspect document", "reconcile", "collate", "tabulate", "dispatch", "type",
    "keyboard", "billing", "invoicing", "copy", "logs", "telemarket", "dial",
    "cold call", "switchboard", "ticket", "form", "routine verification", "data entry"
]

# High-augmentation task indicators (analytical, coding, drafting, modeling, diagnostic)
AUGMENTATION_KEYWORDS = [
    "analyze", "design", "develop", "model", "program", "code", "evaluate",
    "synthesize", "forecast", "optimize", "troubleshoot", "review", "test",
    "diagnose", "investigate", "architect", "simulate", "benchmark", "configure",
    "machine learning", "algorithm", "statistical", "strategy", "roadmap"
]

# Human-centric / physical / high-stakes indicators (hands-on craft, clinical care, empathy, trades)
HUMAN_CENTRIC_KEYWORDS = [
    "negotiate", "persuade", "counsel", "empathy", "mentor", "lead", "manage conflict",
    "physical", "install", "repair", "operate machinery", "surgery", "therapy",
    "collaborate with client", "interview", "care", "inspect physically", "supervise people",
    "wire", "wiring", "conduit", "ladder", "scaffold", "assemble", "fixture", "cable",
    "circuit", "machinery", "pipe", "equipment", "construction", "weld", "carpentry",
    "motor", "tool", "hand tools", "power tools", "patient", "bedside", "vital signs",
    "medication", "clinical", "triage", "nurse", "nursing", "wound", "rehabilitation",
    "dressing", "physical exam", "vehicle", "drive", "plumb", "cook"
]


class AIExposureEngine:
    def evaluate_task(self, task_description: str) -> Dict[str, Any]:
        """
        Evaluate a single task statement for AI exposure, transformation type, and rationale.
        """
        desc = task_description.lower()

        # Keyword frequency matching
        auto_score = sum(1 for kw in HIGH_AUTOMATION_KEYWORDS if kw in desc)
        aug_score = sum(1 for kw in AUGMENTATION_KEYWORDS if kw in desc)
        human_score = sum(1 for kw in HUMAN_CENTRIC_KEYWORDS if kw in desc)

        if human_score > 0 and human_score >= auto_score and human_score >= aug_score:
            impact_score = max(0.08, min(0.25 - (human_score * 0.04), 0.35))
            transform_type = "Human-Centric / High Discretion"
            rationale = "Task requires direct human physical presence, manual dexterity, interpersonal empathy, or clinical patient care."
        elif auto_score > aug_score:
            impact_score = min(0.75 + (auto_score * 0.06), 0.96)
            transform_type = "Direct Automation"
            rationale = "Task involves routine, rule-based, or codified information handling suitable for direct automated execution."
        elif aug_score >= auto_score and aug_score > 0:
            impact_score = min(0.40 + (aug_score * 0.06), 0.75)
            transform_type = "AI Augmentation"
            rationale = "Task is significantly accelerated by generative AI/ML copilots, expanding analytical capacity and throughput."
        elif human_score > 0:
            impact_score = max(0.10, 0.28 - (human_score * 0.04))
            transform_type = "Human-Centric / High Discretion"
            rationale = "Task requires human empathy, interpersonal negotiation, physical presence, or nuanced leadership discretion."
        else:
            # Neutral / mixed baseline
            impact_score = 0.30
            transform_type = "Moderate Transformation"
            rationale = "Task involves standard domain activities with incremental AI tool adoption."

        return {
            "task_description": task_description,
            "ai_impact_score": round(impact_score, 2),
            "transformation_type": transform_type,
            "rationale": rationale
        }

    def analyze_occupation_tasks(
        self,
        tasks: List[Dict[str, Any]],
        occupation_title: str = "",
        domain: str = ""
    ) -> Dict[str, Any]:
        """
        Analyze a set of granular O*NET tasks for an occupation with title/domain context calibration.
        Distinguishes task transformation from simplistic elimination.
        """
        title_lower = (occupation_title or "").lower()

        # Physical / Clinical Craft indicators in title
        is_physical_trade = any(term in title_lower for term in [
            "electrician", "carpenter", "plumber", "machinist", "mechanic", "welder",
            "construction", "mason", "painter", "roofer", "driver", "chef", "cook",
            "technician", "hvac", "assembler", "maintenance"
        ])
        is_clinical_healthcare = any(term in title_lower for term in [
            "nurse", "nursing", "physician", "surgeon", "dentist", "dental", "therapist",
            "paramedic", "veterinarian", "radiologic", "phlebotomist"
        ])
        is_routine_administrative = any(term in title_lower for term in [
            "data entry", "telemarketer", "telecaller", "typist", "switchboard", "proofreader",
            "file clerk", "mail clerk", "word processor", "teller", "billing clerk", "keyer"
        ])

        if not tasks:
            # Default baseline calibrated by role archetype
            if is_physical_trade or is_clinical_healthcare:
                base_exp = 0.16
                category = "Low transformation exposure"
            elif is_routine_administrative:
                base_exp = 0.84
                category = "High transformation exposure"
            else:
                base_exp = 0.40
                category = "Moderate transformation exposure"

            return {
                "ai_exposure_score": base_exp,
                "exposure_category": category,
                "task_count": 0,
                "sample_evaluations": [],
                "summary": "Baseline occupational exposure estimate based on comparable domain benchmarks."
            }

        evaluations = []
        total_score = 0.0
        transform_counts = {
            "Direct Automation": 0,
            "AI Augmentation": 0,
            "Human-Centric / High Discretion": 0,
            "Moderate Transformation": 0
        }

        for t in tasks:
            desc = t.get("task_description", "")
            eval_res = self.evaluate_task(desc)
            evaluations.append(eval_res)
            total_score += eval_res["ai_impact_score"]
            ttype = eval_res["transformation_type"]
            transform_counts[ttype] = transform_counts.get(ttype, 0) + 1

        avg_exposure = total_score / len(tasks)

        # Context calibration: anchor physical/clinical craft or routine administrative
        if is_physical_trade:
            avg_exposure = min(avg_exposure * 0.55, 0.22)
        elif is_clinical_healthcare:
            avg_exposure = min(avg_exposure * 0.58, 0.25)
        elif is_routine_administrative:
            avg_exposure = max(avg_exposure * 1.35, 0.82)

        avg_exposure = max(0.08, min(round(avg_exposure, 2), 0.95))

        # Map to required categorical exposure tiers
        if avg_exposure < 0.35:
            category = "Low transformation exposure"
        elif avg_exposure <= 0.65:
            category = "Moderate transformation exposure"
        else:
            category = "High transformation exposure"

        return {
            "ai_exposure_score": avg_exposure,
            "exposure_category": category,
            "task_count": len(tasks),
            "breakdown": transform_counts,
            "sample_evaluations": evaluations[:5],  # top 5 representative tasks
            "summary": (
                f"{len(tasks)} tasks analyzed. {transform_counts.get('AI Augmentation', 0)} tasks augmented, "
                f"{transform_counts.get('Direct Automation', 0)} tasks automated, "
                f"{transform_counts.get('Human-Centric / High Discretion', 0)} tasks human-centric."
            )
        }
