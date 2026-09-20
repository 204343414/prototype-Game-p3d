"""Safe candidate arbitration shell for the player simulator.

It evaluates owner-local condition wrappers but refuses to invent a winner when
multiple sibling branches are true. Sequence/opportunity/playback arbitration
will plug into this boundary after its engine semantics are proven.
"""
from __future__ import annotations
from typing import Any
from condition_runtime import Blackboard,Truth,evaluate_tokens

def owner_condition_truth(owner:dict[str,Any],bb:Blackboard):
 groups=owner.get('condition_groups',[])
 if not groups:return Truth.TRUE,[]
 trace=[];result=Truth.TRUE
 for g in groups:
  r=evaluate_tokens(g,bb);trace.append(r)
  if r['truth'] is Truth.FALSE:return Truth.FALSE,trace
  if r['truth'] is Truth.UNKNOWN:result=Truth.UNKNOWN
 return result,trace

def sibling_candidates(parent:dict[str,Any],bb:Blackboard)->dict[str,Any]:
 rows=[]
 for child in parent.get('child_owners',[]):
  truth,trace=owner_condition_truth(child,bb)
  rows.append({'offset':child['offset'],'kind':child['kind'],'name':child['name'],'truth':truth.name,'trace':trace})
 true=[x for x in rows if x['truth']=='TRUE'];unknown=[x for x in rows if x['truth']=='UNKNOWN']
 if len(true)==1 and not unknown:status='unique'
 elif len(true)>1:status='ambiguous-needs-sequence-arbitration'
 elif unknown:status='blocked-by-unknown'
 else:status='none'
 # Only a uniquely true set with no unknown competitors is safe to select.
 selected=true[0]['offset'] if status=='unique' else None
 return {'status':status,'selected_offset':selected,'candidates':rows,
         'claim_boundary':'condition eligibility only; no sibling priority, sequence, opportunity, or playback arbitration inferred'}
