"""Study correction v2: restore semantic alignment before ordered-plan scoring.

V1 threshold-only LCS incorrectly gives full credit to reversed near-synonymous
steps in the pinned boiling/pouring diagnostic. V1 code/evidence stay archived.
Graph scoring is unchanged; ambiguous non-identical mappings remain unscored.
"""
from collections import Counter
from .worfeval_v1 import SentenceSimilarity, THRESHOLD, _alignment, prf
from .worfeval_v1 import score_graph as _score_graph_v1

VERSION = 'study-worfeval-correction-v2'


def score_graph(predicted, reference, similarity):
    return _score_graph_v1(predicted,reference,similarity) | {'metric_version':VERSION}


def score_plan(predicted, reference, similarity, ordered=True):
    if any(not isinstance(x,str) or not x.strip() for x in [*predicted,*reference]):
        raise ValueError('invalid_action_description')
    predicted=[x for x in predicted if x not in {'START','END'}]
    reference=[x for x in reference if x not in {'START','END'}]
    if not predicted or not reference:
        correct,status=0,'empty_sequence'
    elif Counter(predicted)==Counter(reference):
        # Every occurrence can match exactly at weight one. Optimize order only
        # among those maximum-weight exact matches; fuzzy alternatives cannot
        # turn reversed *different* actions into identical actions.
        if ordered:
            previous=[0]*(len(reference)+1)
            for a in predicted:
                current=[0]
                for j,b in enumerate(reference):
                    current.append(max(current[-1],previous[j+1],previous[j]+int(a==b)))
                previous=current
            correct=previous[-1]
        else:correct=len(predicted)
        status='maximum_weight_exact_occurrence_alignment'
    else:
        mapping,status=_alignment(predicted,reference,similarity)
        if mapping is None:
            return {'metric_version':VERSION,'grading_status':'ambiguous_alignment','scores':None,'alignment_status':status}
        if ordered:
            # Only matched nodes participate. Empty/unmatched nodes contribute
            # zero and cannot seed a spurious length-one subsequence.
            sequence=[mapping[i] for i in range(len(predicted)) if i in mapping]
            lengths=[]
            for i,value in enumerate(sequence):
                lengths.append(1+max((lengths[j] for j in range(i) if sequence[j]<value),default=0))
            correct=max(lengths,default=0)
        else:correct=len(mapping)
    return {'metric_version':VERSION,'grading_status':'scored','alignment_status':status,
            'scores':prf(correct,len(predicted),len(reference)),'ordered':ordered}
