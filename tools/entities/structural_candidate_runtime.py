"""Condition-safe structural candidate traversal for demonstrated FIG owners.

Mirrors the recovered node/bank/store/reference virtual +0x28 behavior. It does
not model activation lifetime, track playback, or weight-producing condition
kinds. For the covered Hammer scopes all traversed conditions are boolean and
therefore use the engine's immediate (weight-zero) path.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Optional
from arbitration_runtime import owner_condition_truth
from condition_runtime import Blackboard, Truth
from sequencer_selection_runtime import ProbeResult, select_child

@dataclass(frozen=True)
class StructuralSelection:
    selected_offset: Optional[int]
    status: str
    trace: tuple[dict[str, Any], ...]
    selected_resolver: Optional[Callable[[int], dict[str, Any]]] = None
    external_branch: str = ''
    external_root: Optional[dict[str, Any]] = None

Resolver = Callable[[int], dict[str, Any]]


def select_scope(scope: dict[str, Any], bb: Blackboard, resolve: Resolver,
                 random_unit: float = 0.0,
                 excluded_offset: Optional[int] = None,
                 allow_weighted_store: bool = False,
                 resolve_branch=None) -> StructuralSelection:
    """Select within a scope.

    excluded_offset models the active-candidate context supplied to engine
    probes. The receiver is proven to reject its current candidate; where in
    the probe stack current-child exclusion occurs is still inferred. Callers
    must therefore surface this option as inferred, not byte-exact.
    """
    trace: list[dict[str, Any]] = []

    def random_weight(owner):
        weights=[]
        def visit(t):
            if t.get('kind')=='random' and isinstance(t.get('weight'),(int,float)):weights.append(float(t['weight']))
            for c in t.get('children',[]):visit(c)
        for group in owner.get('condition_groups',[]):
            for token in group:visit(token)
        return max(weights,default=0.0)

    def probe_owner(owner: dict[str, Any], weighted_pass: bool, current_resolve=resolve,
                    external_branch: str = '', external_root=None, explicit_entry: bool = False):
        kind=owner['kind']; off=owner['offset']
        if kind=='store' and not allow_weighted_store and not explicit_entry:
            trace.append({'offset':off,'kind':kind,'result':'deferred-until-active-child-or-reference-scope'})
            return ProbeResult(None,0.0)
        truth, condition_trace=owner_condition_truth(owner,bb)
        trace.append({'offset':off,'kind':kind,'truth':truth.name,
                      'weighted_pass':weighted_pass,'conditions':condition_trace})
        if off == excluded_offset:
            trace[-1]['result']='excluded-active-candidate-inference'
            return ProbeResult(None,0.0)
        if truth is not Truth.TRUE:
            return ProbeResult(None,0.0)
        if kind=='node':
            weight=random_weight(owner)
            if weight:trace[-1]['random_weight']=weight
            return ProbeResult((off,current_resolve,external_branch,external_root),weight)
        if kind in ('bank','store'):
            entries=[]
            for child in owner.get('child_owners',[]):
                entries.append((child['offset'],('owner',child)))
            for ref in owner.get('other_direct_children',[]):
                if ref.get('kind')=='reference':entries.append((ref['offset'],('reference',ref)))
            entries.sort(key=lambda x:x[0])
            probes=[]
            for _,entry in entries:
                tag,obj=entry
                if tag=='owner':
                    probes.append(lambda branch,priority,weighted,o=obj,cr=current_resolve,eb=external_branch,er=external_root:probe_owner(o,weighted,cr,eb,er))
                else:
                    probes.append(lambda branch,priority,weighted,r=obj,cr=current_resolve,eb=external_branch,er=external_root:probe_reference(r,weighted,cr,eb,er))
            nested=select_child(probes,None,-1,random_unit)
            return ProbeResult(nested.selected,0.0)
        return ProbeResult(None,0.0)

    active_external_branches:set[str]=set()

    def probe_reference(ref: dict[str, Any], weighted_pass: bool, current_resolve=resolve,
                        external_branch: str = '', external_root=None):
        targets=ref.get('target_owner_offsets',[])
        truth,condition_trace=owner_condition_truth(ref,bb)
        aliases=ref.get('target_reference_aliases',[])
        trace.append({'offset':ref['offset'],'kind':'reference','targets':targets,
                      'reference_aliases':aliases,'weighted_pass':weighted_pass,'truth':truth.name,
                      'conditions':condition_trace})
        if truth is not Truth.TRUE:return ProbeResult(None,0.0)
        if len(targets)==1:
            return probe_owner(current_resolve(targets[0]),weighted_pass,current_resolve,external_branch,external_root,True)
        branch=ref.get('branch')
        if not branch and len(aliases)==1:branch=aliases[0].get('branch')
        if not branch or resolve_branch is None:return ProbeResult(None,0.0)
        if branch in active_external_branches:
            trace[-1]['alias_resolution']='cycle-blocked'
            return ProbeResult(None,0.0)
        resolved=resolve_branch(branch)
        if resolved is None:return ProbeResult(None,0.0)
        root,branch_resolve=resolved
        trace[-1]['resolved_external_branch']=branch
        active_external_branches.add(branch)
        try:return probe_owner(root,weighted_pass,branch_resolve,branch,root,True)
        finally:active_external_branches.remove(branch)

    result=probe_owner(scope,False,explicit_entry=True)
    unknown=any(x.get('truth')=='UNKNOWN' for x in trace)
    if result.candidate is not None:
        status=('selected-with-active-exclusion-inference' if excluded_offset is not None
                else 'selected-demonstrated-zero-weight-path')
        off,selected_resolve,external_branch,external_root=result.candidate
    else:
        off=None;selected_resolve=None;external_branch='';external_root=None
        status='blocked-by-unknown' if unknown else 'none'
    return StructuralSelection(off,status,tuple(trace),selected_resolve,external_branch,external_root)


def select_child_scope(scope_owner: dict[str, Any], bb: Blackboard, resolve: Resolver,
                       random_unit: float = 0.0,
                       excluded_offset: Optional[int] = None,
                       after_child_offset: Optional[int] = None,
                       resolve_branch=None) -> StructuralSelection:
    """Probe a node's nested owner/reference scope rather than the node itself.

    Active runtime evidence proves recursive nested-owner dispatch; using the
    activation node as the persistent transition scope is an inference required
    by the Hammer windup→hold→release→recovery topology and is labeled in the
    returned status.
    """
    children=scope_owner.get('child_owners',[])
    others=scope_owner.get('other_direct_children',[])
    if after_child_offset is not None:
        # Inferred cursor semantics: subsequent transition probes continue after
        # the currently active direct child, not again from source entry zero.
        children=[x for x in children if x['offset']>after_child_offset]
        others=[x for x in others if x['offset']>after_child_offset]
    synthetic={'kind':'bank','offset':scope_owner['offset'],'condition_groups':[[]],
               'child_owners':children,'other_direct_children':others}
    got=select_scope(synthetic,bb,resolve,random_unit,excluded_offset,
                     allow_weighted_store=after_child_offset is not None,
                     resolve_branch=resolve_branch)
    if got.selected_offset is not None:
        return StructuralSelection(got.selected_offset,
          'selected-with-active-child-scope-inference',got.trace,
          got.selected_resolver,got.external_branch,got.external_root)
    return got
