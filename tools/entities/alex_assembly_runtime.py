"""Alex TransformationDescription assembly state from the generated manifest.

Component/resource fields and TOD references are exact manifest facts. Source
transformation callbacks drive component activation; visual morph interpolation
and direct inspector switch() remain explicit simulator approximations.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

class AlexAssembly:
 def __init__(self,manifest_path):
  self.manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
  actor=self.manifest['actor'];self.base_fig_block=actor['base_fig_block'];self.assembly_root=actor['assembly_root']
  self.descriptions={x['fields']['description_name']:x for x in actor['transformations']}
  self.active_name='Alex';self.history=[];self.morph_pending=None;self.morph_phase='idle';self.desired_name=None
  self.morph_fade=None;self._fade_state=None
 @property
 def active(self):return self.descriptions[self.active_name]
 @property
 def fields(self):return dict(self.active['fields'])
 @property
 def exact_references(self):return list(self.active['exact_tod_object_references'])
 @property
 def fig_blocks(self):
  return sorted({x['block'] for x in self.active['fig_gate_occurrences']})
 def assembled_components(self)->dict[str,Any]:
  f=self.fields
  return {'assembly_root':self.assembly_root,'transformation':self.active_name,
   'stream_package':f['stream_package'],'drawable':f['drawable'],'body_template':f['body_template'],
   'body_grab_slot':f['body_grab_slot'],'physics_object':f['physics_object'],
   'physics_object_factory':f['physics_object_factory'],
   'supporting_limb_list_object':f['supporting_limb_list_object'],'power':f['power'],
   'faction':f['faction'],'override_left_arm_template':f['override_left_arm_template'],
   'left_arm_needed':f['left_arm_needed'],'damage_multiplier':f['damage_multiplier'],
   'health_damage_scale':f['health_damage_scale'],'hud_power':f['hud_power'],
   'tod_references':self.exact_references,'fig_blocks':self.fig_blocks,
   'morph_phase':self.morph_phase,'morph_pending':self.morph_pending,'morph_fade':self.morph_fade,'desired_transformation':self.desired_name,
   'evidence':'decoded TransformationDescription/TOD references'}
 def switch(self,name):
  if name not in self.descriptions:raise KeyError(name)
  before=self.active_name;self.active_name=name
  event={'from':before,'to':name,'components':self.assembled_components(),
   'evidence':'simulator instantaneous switch; source component fields exact, morph timing unresolved'}
  self.history.append(event);return event
 def advance(self,dt):
  if not self._fade_state:return
  s=self._fade_state;s['elapsed']+=max(0.0,float(dt));duration=s['duration']
  self.morph_fade=s['to'] if duration<=0 else s['from']+(s['to']-s['from'])*(s['elapsed']/duration)
 def apply_transform_track(self,track,phase='start'):
  kind=track.get('kind')
  if kind=='transformFade':
   if phase in ('start','fire'):
    begin=float(track.get('fade_from',0));end=float(track.get('fade_to',0));duration=float(track.get('time_end',0))-float(track.get('time_begin',0))
    self.morph_fade=begin;self._fade_state={'from':begin,'to':end,'duration':duration,'elapsed':0.0}
   elif phase in ('end','teardown'):self.morph_fade=float(track.get('fade_to',0));self._fade_state=None
   else:return None
   event={'type':'transformFade','phase':phase,'value':self.morph_fade,'track_offset':track.get('offset'),
    'evidence':'decoded linear TransformFadeAction callbacks; simulator callback ordering approximate'}
   self.history.append(event);return event
  if phase not in ('start','fire'):return None
  if kind=='transformDesired':
   target=track.get('transformation');event={'type':'transformDesired','target':target,'transformation_hash':track.get('transformation_hash'),
    'track_offset':track.get('offset'),'time_begin':track.get('time_begin'),
    'evidence':'decoded desired-transformation name range is sent to actor; downstream choice semantics unresolved'}
   if target in self.descriptions:self.desired_name=target
   else:event['blocked']='target TransformationDescription is not an Alex manifest entry'
   self.history.append(event);return event
  if kind=='transformStart':
   target=track.get('transformation')
   event={'type':'transformStart','target':target,'track_offset':track.get('offset'),
    'time_begin':track.get('time_begin'),'evidence':'decoded transformStart track callback'}
   if target in self.descriptions:
    self.morph_pending=target;self.morph_phase='started';event['assembly_switch']=self.switch(target)
    event['assembly_switch']['evidence']='source transformStart drives exact component fields; visual morph interpolation approximate'
   else:event['blocked']='target TransformationDescription is not an Alex manifest entry'
   self.history.append(event);return event
  if kind=='transformComplete':
   self.morph_phase='complete';event={'type':'transformComplete','target':self.morph_pending,
    'track_offset':track.get('offset'),'time_begin':track.get('time_begin'),'evidence':'decoded transformComplete track callback'}
   self.morph_pending=None;self.history.append(event);return event
  return None
