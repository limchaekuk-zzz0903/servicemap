import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx

from config import OUTPUT_DIR


class SitemapGraph:
    def __init__(self):
        self.graph = nx.DiGraph()

    def add_screen(self, fingerprint, activity, screenshot_path, depth, discovered_at, label=""):
        if fingerprint in self.graph:
            return
        self.graph.add_node(
            fingerprint,
            activity=activity,
            screenshot_path=screenshot_path,
            depth=depth,
            discovered_at=discovered_at,
            label=label or activity,
            blocked_actions=[],
        )

    def add_transition(self, from_fp, to_fp, action_label, blocked=False, block_reason="", signature=""):
        self.graph.add_edge(
            from_fp,
            to_fp,
            action_label=action_label,
            blocked=blocked,
            block_reason=block_reason,
            # 앱 재시작 후 경로 재생(navigate_to) 시 '같은 버튼'을 안정적으로 찾기 위한 키.
            # 좌표 기반 라벨과 달리 재시작에도 흔들리지 않는다(UIElement.signature 참고).
            signature=signature,
        )

    def add_blocked_action(self, from_fp, action_label, block_reason):
        """탭하지 않고 발견만 한 주행 관련 액션을 화면 노드에 기록한다."""
        if from_fp not in self.graph:
            return
        self.graph.nodes[from_fp]["blocked_actions"].append(
            {"action_label": action_label, "block_reason": block_reason}
        )

    def to_json(self, path: str = None, left_app_events=None) -> str:
        path = path or os.path.join(OUTPUT_DIR, "sitemap.json")
        data = {
            "screens": [{"fingerprint": n, **attrs} for n, attrs in self.graph.nodes(data=True)],
            "transitions": [
                {"from": u, "to": v, **attrs} for u, v, attrs in self.graph.edges(data=True)
            ],
            "left_app_events": left_app_events or [],
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def to_graphml(self, path: str = None) -> str:
        path = path or os.path.join(OUTPUT_DIR, "sitemap.graphml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        g = self.graph.copy()
        for _, attrs in g.nodes(data=True):
            for k, v in list(attrs.items()):
                if isinstance(v, (list, dict)):
                    attrs[k] = json.dumps(v, ensure_ascii=False)
        nx.write_graphml(g, path)
        return path

    def render_png(self, path: str = None):
        path = path or os.path.join(OUTPUT_DIR, "sitemap.png")
        if self.graph.number_of_nodes() == 0:
            return None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        plt.figure(figsize=(16, 12))
        pos = nx.spring_layout(self.graph, k=0.6, seed=42)
        labels = {n: str(attrs.get("label", n))[:20] for n, attrs in self.graph.nodes(data=True)}
        nx.draw(
            self.graph,
            pos,
            labels=labels,
            node_color="#8ecae6",
            node_size=1200,
            font_size=7,
            arrows=True,
            edge_color="#888888",
        )
        plt.title("Tmap Sitemap")
        plt.tight_layout()
        plt.savefig(path, dpi=150)
        plt.close()
        return path
