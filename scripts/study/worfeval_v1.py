"""Reviewed study scorer derived from pinned WorFEval; NOT the official metric.

Thresholded one-to-one semantic alignment, mapped graph edges, and explicit
node/edge metrics. The mapped connected-component score is diagnostic only.
Ordered sequences use thresholded LCS, not arbitrary graph topological orders.
Ambiguous maximum-weight graph alignments have no structural score.
"""
from collections import Counter
import math
import networkx as nx

VERSION = "study-worfeval-correction-v1"
THRESHOLD = 0.6
TIE_TOLERANCE = 1e-9
SENTINELS = {"START", "END"}


def prf(correct, predicted, reference):
    if not 0 <= correct <= min(predicted, reference):
        raise ValueError("invalid_metric_counts")
    precision = correct / predicted if predicted else float(reference == 0)
    recall = correct / reference if reference else float(predicted == 0)
    return {"correct": correct, "predicted": predicted, "reference": reference,
            "precision": precision, "recall": recall,
            "f1": 2 * correct / (predicted + reference) if predicted + reference else 1.0}


def _matrix(predicted, reference, similarity):
    if not predicted or not reference:
        return [[] for _ in predicted]
    matrix = similarity(predicted, reference)
    if len(matrix) != len(predicted) or any(len(row) != len(reference) for row in matrix):
        raise ValueError("similarity_shape_mismatch")
    result = []
    for row in matrix:
        if any(not math.isfinite(float(x)) or not -1.000001 <= float(x) <= 1.000001 for x in row):
            raise ValueError("invalid_cosine_similarity")
        result.append([max(0.0, min(1.0, float(x))) for x in row])
    return result


def _alignment(predicted, reference, similarity):
    """Maximum total cosine, like upstream; test uniqueness rather than ID ties."""
    matrix = _matrix(predicted, reference, similarity)
    graph = nx.Graph()
    for i, row in enumerate(matrix):
        for j, weight in enumerate(row):
            if weight > THRESHOLD:
                graph.add_edge(("p", i), ("r", j), weight=weight)

    def solve(g):
        edges = nx.max_weight_matching(g, maxcardinality=False, weight="weight")
        pairs = {(a[1], b[1]) if a[0] == "p" else (b[1], a[1]) for a, b in edges}
        return pairs, math.fsum(matrix[i][j] for i, j in sorted(pairs))

    pairs, weight = solve(graph)
    for i, j in sorted(pairs):
        alternative = graph.copy()
        alternative.remove_edge(("p", i), ("r", j))
        _, other_weight = solve(alternative)
        if math.isclose(weight, other_weight, rel_tol=0, abs_tol=TIE_TOLERANCE):
            return None, "ambiguous_maximum_weight_alignment"
    return dict(pairs), "unique_semantic_alignment"


def graph_data(graph):
    """Accept upstream indexed graphs or imported explicit string-ID graphs."""
    values = graph.get("nodes")
    if isinstance(values, list):
        nodes = dict(enumerate(values))
    elif isinstance(values, dict):
        nodes = dict(values)
        for sentinel in graph.get("sentinels", []):
            if sentinel not in SENTINELS or sentinel in nodes:
                raise ValueError("invalid_sentinel_declaration")
            nodes[sentinel] = sentinel
    else:
        raise ValueError("nodes_must_be_list_or_mapping")
    if any(not isinstance(v, str) or not v.strip() for v in nodes.values()):
        raise ValueError("invalid_node_description")
    if any(count > 1 for value, count in Counter(nodes.values()).items() if value in SENTINELS):
        raise ValueError("duplicate_sentinel")
    edges = []
    for edge in graph.get("edges", []):
        if not isinstance(edge, (list, tuple)) or len(edge) != 2:
            raise ValueError("malformed_edge")
        a, b = edge
        if isinstance(a, bool) or isinstance(b, bool) or a not in nodes or b not in nodes:
            raise ValueError("unresolved_edge_endpoint")
        edges.append((a, b))
    if len(set(edges)) != len(edges):
        raise ValueError("duplicate_edge")
    g = nx.DiGraph()
    g.add_nodes_from((node, {"label": label}) for node, label in nodes.items())
    g.add_edges_from(edges)
    sentinels = {label: node for node, label in nodes.items() if label in SENTINELS}
    issues = []
    if nodes and sentinels.keys() != SENTINELS:
        issues.append("missing_start_or_end")
    if not nx.is_directed_acyclic_graph(g):
        issues.append("cycle")
    if "START" in sentinels and g.in_degree(sentinels["START"]):
        issues.append("incoming_start_edge")
    if "END" in sentinels and g.out_degree(sentinels["END"]):
        issues.append("outgoing_end_edge")
    if sentinels.keys() == SENTINELS:
        reachable = nx.descendants(g, sentinels["START"]) | {sentinels["START"]}
        to_end = nx.ancestors(g, sentinels["END"]) | {sentinels["END"]}
        if set(nodes) - (reachable & to_end):
            issues.append("node_outside_start_end_path")
    return nodes, set(edges), g, sentinels, issues


def score_graph(predicted, reference, similarity):
    pn, pe, pg, ps, pi = graph_data(predicted)
    rn, re, rg, rs, ri = graph_data(reference)
    if ri:
        raise ValueError("invalid_reference_graph:" + ",".join(ri))
    base = {"metric_version": VERSION, "predicted_structure_issues": pi,
            "prediction_structurally_valid": not pi, "threshold_strictly_greater_than": THRESHOLD}
    # Exact labeled graph isomorphism handles duplicate action text and graph
    # automorphisms without arbitrary index-dependent matching. No fuzzy claim.
    iso = nx.algorithms.isomorphism.DiGraphMatcher(pg, rg, node_match=lambda a, b: a["label"] == b["label"])
    if iso.is_isomorphic():
        mapping, status = dict(iso.mapping), "exact_labeled_graph_isomorphism"
    else:
        pids = sorted((n for n in pn if pn[n] not in SENTINELS), key=lambda n: (pn[n], str(n)))
        rids = sorted((n for n in rn if rn[n] not in SENTINELS), key=lambda n: (rn[n], str(n)))
        aligned, status = _alignment([pn[n] for n in pids], [rn[n] for n in rids], similarity)
        if aligned is None:
            return base | {"alignment_status": status, "action_nodes": None, "edges": None,
                           "mapped_component_diagnostic": None, "mapping": None,
                           "grading_status": "ambiguous_alignment"}
        mapping = {pids[i]: rids[j] for i, j in aligned.items()}
        mapping.update({ps[label]: rs[label] for label in ps.keys() & rs.keys()})
    mapped_edges = {(mapping[a], mapping[b]) for a, b in pe if a in mapping and b in mapping}
    common = mapped_edges & re
    action_matches = sum(pn[p] not in SENTINELS for p in mapping)
    component = nx.Graph()
    component.add_nodes_from(mapping.values())
    component.add_edges_from(common)
    size = max((len(c) for c in nx.connected_components(component)), default=0)
    return base | {"alignment_status": status, "grading_status": "scored",
                   "action_nodes": prf(action_matches, sum(v not in SENTINELS for v in pn.values()), sum(v not in SENTINELS for v in rn.values())),
                   "edges": prf(len(common), len(pe), len(re)),
                   "mapped_component_diagnostic": prf(size, len(pn), len(rn)),
                   "mapping": [[p, r] for p, r in sorted(mapping.items(), key=lambda item: str(item[0]))]}


def score_plan(predicted, reference, similarity, ordered=True):
    """An explicitly ordered sequence, never an arbitrary DAG node enumeration.

    LCS optimizes the number of order-preserving threshold-admissible matches;
    unlike upstream's max-weight-then-LIS, unmatched nodes never start at one.
    Repeated action descriptions retain their occurrence multiplicity.
    """
    if any(not isinstance(x, str) or not x.strip() for x in [*predicted, *reference]):
        raise ValueError("invalid_action_description")
    predicted = [x for x in predicted if x not in SENTINELS]
    reference = [x for x in reference if x not in SENTINELS]
    if ordered:
        matrix = _matrix(predicted, reference, similarity)
        previous = [0] * (len(reference) + 1)
        for row in matrix:
            current = [0]
            for j, cosine in enumerate(row):
                current.append(max(previous[j+1], current[-1], previous[j] + int(cosine > THRESHOLD)))
            previous = current
        correct, status = previous[-1], "ordered_threshold_lcs"
    else:
        mapping, status = _alignment(predicted, reference, similarity)
        if mapping is None:
            return {"metric_version": VERSION, "grading_status": "ambiguous_alignment", "scores": None, "alignment_status": status}
        correct = len(mapping)
    return {"metric_version": VERSION, "grading_status": "scored", "alignment_status": status,
            "scores": prf(correct, len(predicted), len(reference)), "ordered": ordered}


class SentenceSimilarity:
    """CPU evaluator-only embedder supplied by the caller; never loads remotely."""
    def __init__(self, model):
        self.model = model
        self.cache = {}

    def __call__(self, predicted, reference):
        import numpy as np
        needed = sorted(set([*predicted, *reference]) - self.cache.keys())
        if needed:
            values = self.model.encode(needed, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
            self.cache.update(zip(needed, values))
        return [[1.0 if a == b else float(np.dot(self.cache[a], self.cache[b])) for b in reference] for a in predicted]
