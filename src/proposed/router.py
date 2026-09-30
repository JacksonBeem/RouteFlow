"""Proposed-arm router: node instruction -> property -> tier -> model (offline, pure).

Difficulty is read from WHAT a node computes (its property: Wiener index, implicit hydrogens,
reaction product, ...), not from its verb: LongCoT instructions are templated, so the verbs
("select the molecule with the ...") are boilerplate and the property noun carries the load.
The 41 properties below cover every dev + reserve node (3,990, zero unmatched, 2026-09-28).

Rules are ordered and the first match wins; a specific phrase must precede any generic phrase
it contains ("graph center" nodes also say "eccentricity"; "secondary structure" nodes also say
"dihedral angles"). Two modifiers then adjust the tier:
  * multi_candidate ("select all", "keep track of all", "for each valid", "valid candidates"):
    the output is a set and errors fan out, so the tier moves up one step;
  * tie_break ("break the tie", "if tied", ...): a second property, usually molecular weight,
    is needed, so easy becomes medium.
An instruction that matches no property routes to hard and is marked matched=False.

Tier -> model, v2 (user, 2026-09-28, after proposed-1): easy and medium DeepSeek V4.1 Flash, hard GPT-5.2.
(DeepSeek V3.2 was screened first and dropped: 50-227 s per node, its endpoints ignore reasoning_effort;
data/proposed_router/reserve-screen.)
v1 (commit 3dc73f1, run proposed-1) used easy Claude Sonnet 4.5 / medium Gemini 3.1 Pro; on the same
nodes Sonnet cost 3.4x gpt-5.2 and Gemini 0.88x, so v1 saved nothing. Prices: snapshot
data/proposed_router/openrouter_models_snapshot.json.
"""
import re

VERSION = "proposed-router-v2"
TIERS = ("easy", "medium", "hard")
# Tier -> model presets; the property lexicon and bumps are shared by all of them.
# v3 (user, 2026-09-29): easy DeepSeek V4.1 Flash / medium Gemini 3.1 Pro / hard GPT-5.2.
MODEL_SETS = {
    "v1": {"easy": "anthropic/claude-sonnet-4.5", "medium": "google/gemini-3.1-pro-preview", "hard": "openai/gpt-5.2"},
    "v2": {"easy": "deepseek/deepseek-v4.1-flash", "medium": "deepseek/deepseek-v4.1-flash", "hard": "openai/gpt-5.2"},
    "v3": {"easy": "deepseek/deepseek-v4.1-flash", "medium": "google/gemini-3.1-pro-preview", "hard": "openai/gpt-5.2"},
}
MODELS = MODEL_SETS["v2"]

# (property, tier, pattern) in match order.
PROPERTIES = [
    # reactions
    ("reaction_product", "hard", r"predict (?:the )?(?:major )?product"),
    # protein templates (med4, hard4)
    ("protein_gap", "medium", r"missing residue names"),
    ("protein_secondary", "medium", r"predict the secondary structure"),     # before protein_dihedral
    ("protein_compactness", "hard", r"compactness of the protein"),
    ("protein_dihedral", "hard", r"dihedral angles for the residues"),
    ("protein_residue_identity", "hard", r"identity of the missing residues"),
    ("protein_residue_rank", "hard", r"rank the residues in sequence positions"),
    # final-node computations
    ("mcs_smiles", "hard", r"maximum common subgraph"),
    ("balaban_j", "hard", r"balaban"),
    ("total_hydrogens", "medium", r"number of total hydrogens"),
    ("ring_count_compare", "medium", r"count the number of rings"),
    # graph theory (before eccentricity / topological diameter, whose words they contain)
    ("graph_center", "hard", r"graph center"),
    ("mcs_similarity", "hard", r"mcs similarity|maximum common substructure"),
    ("adjacency_matrix_ring", "hard", r"adjacency matrix"),
    # identify / match
    ("smiles_equivalence", "medium", r"equivalent to the (?:following|reference) smiles"),
    ("empirical_formula", "medium", r"empirical formula"),
    ("atom_bond_constraint", "medium", r"must have exactly .* carbons"),
    ("topological_diameter", "hard", r"topological diameter"),
    ("wiener_index", "hard", r"wiener index"),
    ("eccentricity", "hard", r"eccentricity"),
    ("morgan_fingerprints", "hard", r"morgan"),
    ("sssr", "medium", r"sssr"),
    # rules and formulas
    ("cyclic_ratio", "medium", r"cyclic ratio"),
    ("branching_index", "medium", r"branching index"),
    ("atom_degree", "medium", r"atom degree"),
    ("connectivity", "medium", r"connectivity"),
    ("formal_charge", "medium", r"formal charge"),
    ("degree_unsaturation", "medium", r"degree of unsaturation"),
    ("lipinski", "medium", r"lipinski"),
    ("molecular_weight_range", "medium", r"molecular weight between"),
    ("atom_bond_sum", "medium", r"summed atom and bond count"),
    ("rotatable_bonds", "hard", r"rotatable bonds"),
    # direct counts
    ("implicit_hydrogens", "medium", r"implicit hydrogen"),
    ("heavy_atoms", "medium", r"heavy atoms"),
    ("heteroatoms", "medium", r"heteroatoms"),
    ("double_bonds", "medium", r"double bonds"),
    # rings, substructures, functional groups
    ("ring_count_aromatic", "medium", r"aromatic rings?"),
    ("ring_count_aliphatic", "medium", r"aliphatic rings?"),
    ("substructure_yesno", "easy", r"contains the .* substructure"),
    ("functional_group_select", "easy", r"(?:containing|contains?) (?:a|an) "),
    ("conditional_on_prior", "easy", r"based on your previous"),
]
_COMPILED = [(name, tier, re.compile(rx, re.I)) for name, tier, rx in PROPERTIES]
MULTI_CANDIDATE = re.compile(r"select all|keep track of all|for each valid|valid candidates", re.I)
TIE_BREAK = re.compile(r"break (?:the )?ties?|if tied|if there is a tie|if they have the same|"
                       r"if multiple molecules have", re.I)
assert all(t in TIERS for _, t, _ in PROPERTIES) and len({p for p, _, _ in PROPERTIES}) == len(PROPERTIES)


def _up(tier):
    return TIERS[min(TIERS.index(tier) + 1, len(TIERS) - 1)]


def route(instruction, models=None):
    """One node's routing decision, with everything needed to audit it (models: a MODEL_SETS preset)."""
    prop, base = next(((n, t) for n, t, rx in _COMPILED if rx.search(instruction)), (None, "hard"))
    tier, bumps = base, []
    if MULTI_CANDIDATE.search(instruction) and tier != "hard":
        tier = _up(tier)
        bumps.append("multi_candidate")
    if TIE_BREAK.search(instruction) and tier == "easy":
        tier = "medium"
        bumps.append("tie_break")
    return dict(version=VERSION, property=prop, matched=prop is not None, base_tier=base,
                tier=tier, bumps=bumps, model=(models or MODELS)[tier])
