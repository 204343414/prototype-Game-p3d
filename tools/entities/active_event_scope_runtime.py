"""Active-scope event dispatch for fight onEvent tracks.

Evidence boundary:
- 0x10A7F730 walks active tracks and calls virtual +0x10 for event delivery.
- 0x10A7F7B0 maps phase n to bit (11+n) and calls virtual +0x1C.
- onEvent hook/lifetime behavior is delegated to OnEventWindow.

Track construction/subscription and candidate branch resolution are injected.
"""
from __future__ import annotations
from dataclasses import dataclass,field
from typing import Callable,Generic,Iterable,Optional,TypeVar
from on_event_window_runtime import OnEventConfig,OnEventWindow
T=TypeVar('T')
Resolver=Callable[[OnEventConfig,int,object],Optional[T]]

@dataclass(frozen=True)
class Activation(Generic[T]):
 track_index:int
 candidate:T
 cause_bits:int
 priority:int

@dataclass
class ActiveEventScope(Generic[T]):
 tracks:list[OnEventWindow[T]]
 activations:list[Activation[T]]=field(default_factory=list)

 @classmethod
 def from_configs(cls,configs:Iterable[OnEventConfig]):
  return cls([OnEventWindow(c) for c in configs])

 def _deliver(self,index:int,bits:int,context:object,resolver:Resolver[T])->Optional[Activation[T]]:
  w=self.tracks[index]
  if not w.alive or w.candidate is not None or not (w.config.event_mask&bits):return None
  candidate=resolver(w.config,bits,context)
  fired=w.receive(bits,candidate)
  if fired is None:return None
  a=Activation(index,fired,bits,w.config.priority);self.activations.append(a);return a

 def dispatch(self,bits:int,context:object,resolver:Resolver[T])->list[Activation[T]]:
  """Deliver one event bitset in source-track order."""
  out=[]
  for i in range(len(self.tracks)):
   a=self._deliver(i,bits,context,resolver)
   if a is not None:out.append(a)
  return out

 def advance(self,dt:float,cause_bits:int=0)->list[Activation[T]]:
  """Advance elapsed/hook/lifetime once without synthesizing a phase event."""
  if dt<0:raise ValueError('dt must be nonnegative')
  out=[]
  for i,w in enumerate(self.tracks):
   if not w.alive:continue
   fired=w.tick(dt)
   if fired is not None:
    a=Activation(i,fired,cause_bits,w.config.priority);self.activations.append(a);out.append(a)
  return out

 def phase_tick(self,phase:int,dt:float,context:object,resolver:Resolver[T])->list[Activation[T]]:
  """Invoke one exact high-category phase (0..10 => Patsy..HunterCorner)."""
  if not 0<=phase<=10:raise ValueError('phase must be in 0..10')
  bits=1<<(11+phase);out=[]
  for i,w in enumerate(self.tracks):
   if not w.alive:continue
   if w.config.initial_test and not w.initial_tested and (w.config.event_mask&bits):
    a=self._deliver(i,bits,context,resolver)
    if a is not None:out.append(a);continue
   fired=w.tick(dt)
   if fired is not None:
    a=Activation(i,fired,bits,w.config.priority);self.activations.append(a);out.append(a)
  return out

 def teardown(self,cause_bits:int=0)->list[Activation[T]]:
  out=[]
  for i,w in enumerate(self.tracks):
   fired=w.teardown()
   if fired is not None:
    a=Activation(i,fired,cause_bits,w.config.priority);self.activations.append(a);out.append(a)
  return out
