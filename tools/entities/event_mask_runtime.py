"""Exact fight event-mask identities recovered from the DLL static initializer.

The 22-entry table is constructed at 0x10D4C2E0 and installed by 0x10092AE0
through 0x10A822B0. Bit n corresponds to entry n.
"""
EVENT_NAMES=('Input','SupportingSurface','Health','Attachment','Playback','LOD','Target','Autonomous','Task','Goal','Variable','Patsy','Threat','DriverState','InfectedLevel','Pedestrian','Charge','Reaction','MotionState','AIEvent','Movement','HunterCorner')
EVENT_BITS={name:1<<i for i,name in enumerate(EVENT_NAMES)}

def decode_event_mask(mask:int)->tuple[str,...]:
 return tuple(name for i,name in enumerate(EVENT_NAMES) if mask&(1<<i))

def unknown_event_bits(mask:int)->int:
 return mask&~((1<<len(EVENT_NAMES))-1)
