"""
Career Knowledge Graph Engine.
Constructs multi-relational graph structures connecting occupations, granular tasks,
essential skills, software technologies, and industry domains.
"""
from typing import Dict, List, Any, Optional
from ..models.schemas import KnowledgeGraph, GraphNode, GraphEdge


class KnowledgeGraphEngine:
    def build_graph(
        self,
        occupation: str,
        soc_code: Optional[str],
        domain: str,
        tasks: List[Dict[str, Any]],
        skills_dict: Dict[str, Any],
        top_market_skills: List[str]
    ) -> KnowledgeGraph:
        """
        Construct a knowledge graph for the given career.
        """
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        node_ids = set()

        # 1. Root Occupation Node
        occ_id = f"occ_{soc_code or 'root'}"
        nodes.append(GraphNode(
            id=occ_id,
            label=occupation,
            type="occupation",
            weight=1.0,
            metadata={"soc_code": soc_code or "N/A"}
        ))
        node_ids.add(occ_id)

        # 2. Domain Node
        dom_name = domain or "Technology & Applied Sciences"
        dom_id = f"dom_{dom_name.lower().replace(' ', '_')}"
        if dom_id not in node_ids:
            nodes.append(GraphNode(
                id=dom_id,
                label=dom_name,
                type="domain",
                weight=0.85
            ))
            node_ids.add(dom_id)

        edges.append(GraphEdge(
            source=occ_id,
            target=dom_id,
            relationship="BELONGS_TO_DOMAIN",
            weight=1.0
        ))

        # 3. Task Nodes (Sample of representative tasks with AI exposure metadata)
        for idx, task in enumerate(tasks[:5]):
            t_id = f"task_{idx+1}"
            desc = task.get("task_description", "")
            short_label = desc[:45] + "..." if len(desc) > 45 else desc
            ttype = task.get("transformation_type", "Moderate Transformation")
            score = task.get("ai_impact_score", 0.40)

            nodes.append(GraphNode(
                id=t_id,
                label=short_label,
                type="task",
                weight=score,
                metadata={
                    "full_statement": desc,
                    "transformation_type": ttype,
                    "ai_impact_score": score
                }
            ))
            node_ids.add(t_id)

            edges.append(GraphEdge(
                source=occ_id,
                target=t_id,
                relationship="EXECUTES_TASK",
                weight=score
            ))

        # 4. Essential Skill Nodes
        raw_skills = skills_dict.get("skills", [])
        for s in raw_skills[:6]:
            s_name = s.get("skill_name", "")
            s_id = f"skill_{s_name.lower().replace(' ', '_')}"
            imp = s.get("importance", 50.0) / 100.0 if s.get("importance") else 0.70

            if s_id not in node_ids:
                nodes.append(GraphNode(
                    id=s_id,
                    label=s_name,
                    type="skill",
                    weight=round(imp, 2)
                ))
                node_ids.add(s_id)

            edges.append(GraphEdge(
                source=occ_id,
                target=s_id,
                relationship="REQUIRES_COMPETENCY",
                weight=round(imp, 2)
            ))

        # 5. Technology Tools Nodes
        raw_tech = skills_dict.get("technologies", [])
        tech_to_show = raw_tech[:6] if raw_tech else [{"technology_name": sk, "hot_technology": 1} for sk in top_market_skills[:5]]
        for t in tech_to_show:
            t_name = t.get("technology_name", "")
            if not t_name:
                continue
            tech_id = f"tech_{t_name.lower().replace(' ', '_')}"
            hot = t.get("hot_technology", 0)

            if tech_id not in node_ids:
                nodes.append(GraphNode(
                    id=tech_id,
                    label=t_name,
                    type="technology",
                    weight=1.0 if hot else 0.65,
                    metadata={"is_hot_tech": bool(hot)}
                ))
                node_ids.add(tech_id)

            edges.append(GraphEdge(
                source=occ_id,
                target=tech_id,
                relationship="UTILIZES_TOOL",
                weight=0.90 if hot else 0.65
            ))

        return KnowledgeGraph(nodes=nodes, edges=edges)
