"""Prototype entity catalog and census engine.

Categorizes and indexes all Pure3D entities in art.rcf into four standard categories:
  1. powers: Alex Mercer base model, weapon morphs (Claws, Blade, Hammerfist, Whipfist,
     Musclemass), defensive powers (Shield, Armor), devastators (Spines, Parasite),
     and military/civilian disguises.
  2. vehicles: Military vehicles (Tanks, APCs, Blackhawk, Gunship, F45 Jet) and
     civilian city vehicles (Taxis, Police cars, Ambulances, Buses, Trucks, Sedans).
  3. characters: Story NPCs (Dana, Karen, Greene, Specialist Cross, Dr. Ragland),
     bosses/infected (Brawler, Hunter, Hydra, Supreme Hunter), SuperSoldiers,
     Blackwatch troopers, Marines, and pedestrians.
  4. props: Interactive & destructible Manhattan environment entities (Water towers,
     HVAC ventilation units, transformers, antennas, barriers, bus shelters, hydrants).
"""
from __future__ import annotations

import os
import re
import struct
from collections import defaultdict
from typing import Any

from probe_static_geometry import _p3d_string
from render_static_uv_candidates import _records

try:
    import rcf_extract
except ImportError:
    try:
        import tools.rcf_unpack.rcf_extract as rcf_extract
    except ImportError:
        rcf_extract = None

# Pure3D Chunk Types
GEOMETRY = 0x00010000
POLYSKIN = 0x00010001
SKELETON_V1 = 0x00002200
SKELETON_V2 = 0x00023000
SKELETON_JOINT = 0x00002201
SKELETON_JOINT_V2 = 0x00023001
ANIMATION = 0x00121000
COMPOSITE_DRAWABLE = 0x00123000
TEXTURE = 0x00019000

CATEGORY_POWERS = "powers"
CATEGORY_VEHICLES = "vehicles"
CATEGORY_CHARACTERS = "characters"
CATEGORY_PROPS = "props"

CATEGORIES = (CATEGORY_POWERS, CATEGORY_VEHICLES, CATEGORY_CHARACTERS, CATEGORY_PROPS)

CATEGORY_NAMES_ZH = {
    CATEGORY_POWERS: "主角形态与能力",
    CATEGORY_VEHICLES: "载具系统",
    CATEGORY_CHARACTERS: "角色与生物",
    CATEGORY_PROPS: "环境与可破坏道具",
}

# Friendly entity display titles
FRIENDLY_NAMES = {
    # Powers
    "alex": "Alex Mercer (默认兜帽本体)",
    "alex_claws": "利爪形态 (Claws)",
    "alex_blades": "利刃/刀锋形态 (Blade)",
    "alex_hammerfist": "充气重拳 (Hammerfist)",
    "alex_whipfist": "鞭拳形态 (Whipfist)",
    "alex_musclemass": "肌肉强化 (Musclemass)",
    "alex_shield": "生化护盾 (Shield)",
    "alex_armour": "重装甲模式 (Armor)",
    "alex_spines": "墓碑地刺 (Groundspike)",
    "alex_parasite": "万千触须 (Tendril Barrage)",
    "soldier_disguise": "常规陆军伪装 (Soldier)",
    "commander_disguise": "军方指挥官伪装 (Commander)",
    "pilot_disguise": "飞行员伪装 (Pilot)",
    "bwtrooper_disguise": "黑色守望突击队员 (Blackwatch Trooper)",
    "bwofficer_disguise": "黑色守望军官 (Blackwatch Officer)",
    "bwscientist2008_disguise": "便衣科学家伪装 (Scientist)",
    "alexshotbody_disguise": "破损负伤躯体 (Damaged Body)",
    # Vehicles
    "tank_ram_marine": "M1A2 艾布拉姆斯坦克",
    "tank_ram_thermobolic": "热压重型坦克 (Thermobaric)",
    "apc_m2_marine": "M2 步兵装甲运兵车 (APC)",
    "heli_bh_marine": "UH-60 黑鹰直升机",
    "heli_gunship_marine_core": "AH-64 阿帕奇武装直升机",
    "f45Thunder001Military": "F-45 雷霆战斗机",
    "bloodtoxDriller001": "毒气钻机车 (Bloodtox Driller)",
    "generic_taxi": "纽约黄色出租车 (Taxi)",
    "Mini_van_Taxi": "商务出租车 (Minivan Taxi)",
    "police_car": "纽约警车 (Police Car)",
    "ambulance": "紧急救护车 (Ambulance)",
    "busOS001": "单层城市巴士",
    "redTourBus001": "双层红色观光巴士",
    "limoOS001": "加长礼宾豪华轿车",
    "humveeAvenger001": "复仇者防空悍马",
    "titaniumGT001": "钛金跑车 GT",
    "tankerTruck001": "大型重载油罐车",
    # Characters
    "Soldier": "黑色守望与陆军军备全家桶 (Soldier/Blackwatch/Weapons)",
    "soldier": "黑色守望与陆军军备全家桶 (Soldier/Blackwatch/Weapons)",
    "DanaMercer": "达娜·墨瑟 (Dana Mercer)",
    "karen_parker": "凯伦·帕克 (Karen Parker)",
    "ElizabethGreene": "伊丽莎白·格林 (Elizabeth Greene)",
    "mother": "格林母体巨兽形态 (Mother)",
    "specialist": "队长 Cross (Specialist)",
    "ragland_suit": "拉格兰医生 (Dr. Ragland)",
    "SuperSoldier": "黑色守望超级士兵 (Super Soldier)",
    "Brawler": "格斗者变异体 (Brawler)",
    "LeaderHunter": "猎手领袖 (Leader Hunter)",
    "StripedLeaderHunter": "斑纹猎手领袖",
    "Hydra": "九头蛇巨兽触手 (Hydra)",
    "supreme_hunter": "终极至尊猎手 (Supreme Hunter)",
}


def classify_entry(name: str) -> str | None:
    """Classify an RCF entry path into one of the 4 sub-categories."""
    clean = name.lower()
    if clean.endswith(("_tod.p3d.rz", "_fig.p3d.rz", "_lod.p3d.rz", "_camera.p3d.rz", "_nis.p3d.rz")):
        return None
    if "\\powers\\" in clean or clean == "\\art\\alex\\alex.p3d.rz":
        return CATEGORY_POWERS
    if "\\vehicles\\" in clean or ("\\missions\\" in clean and any(k in clean for k in ("tank", "apc", "heli", "f45", "driller"))):
        return CATEGORY_VEHICLES
    if ("\\missions\\" in clean and not any(k in clean for k in ("tank", "apc", "heli", "f45", "driller", "props", "tod", "fig", "effects", "characters"))) or "\\pedestrians\\" in clean:
        return CATEGORY_CHARACTERS
    if clean == "\\art\\locations\\manhattan\\props.p3d.rz":
        return CATEGORY_PROPS
    return None


def inspect_p3d_package(data: bytes) -> dict[str, Any]:
    """Extract structural inventory from a decompressed Pure3D payload."""
    records, children = _records(data)
    
    geometries: list[str] = []
    skeletons: list[str] = []
    total_joints = 0
    comp_drawables: list[str] = []
    animations: list[str] = []
    textures: list[str] = []
    
    for idx, record in enumerate(records):
        tid = record["type_id"]
        if tid in (POLYSKIN, GEOMETRY):
            try:
                name, _ = _p3d_string(record["payload"])
                if name and name not in geometries:
                    geometries.append(name)
            except (ValueError, IndexError):
                pass
        elif tid in (SKELETON_V1, SKELETON_V2):
            try:
                name, _ = _p3d_string(record["payload"])
                if name and name not in skeletons:
                    skeletons.append(name)
                j_count = sum(1 for _, c in children.get(idx, []) if c["type_id"] in (SKELETON_JOINT, SKELETON_JOINT_V2))
                total_joints = max(total_joints, j_count)
            except (ValueError, IndexError):
                pass
        elif tid == COMPOSITE_DRAWABLE:
            try:
                name, _ = _p3d_string(record["payload"])
                if name and name not in comp_drawables:
                    comp_drawables.append(name)
            except (ValueError, IndexError):
                pass
        elif tid == ANIMATION:
            try:
                p = record["payload"]
                name = ""
                if len(p) >= 5 and p[:4] == b"\x00\x00\x00\x00":
                    name, _ = _p3d_string(p, 4)
                if not name:
                    name, _ = _p3d_string(p, 0)
                if name and name not in animations:
                    animations.append(name)
            except (ValueError, IndexError):
                pass
        elif tid == TEXTURE:
            try:
                name, _ = _p3d_string(record["payload"])
                if name and name not in textures:
                    textures.append(name)
            except (ValueError, IndexError):
                pass

    return {
        "geometries": geometries,
        "skeletons": skeletons,
        "total_joints": total_joints,
        "composite_drawables": comp_drawables,
        "animations": animations,
        "textures": textures,
        "total_bytes": len(data),
    }


def parse_props_library(data: bytes) -> list[dict[str, Any]]:
    """Group the 370+ shapes in props.p3d into individual prop items with destruction states."""
    records, _ = _records(data)
    geometries: list[str] = []
    textures: list[str] = []
    for r in records:
        if r["type_id"] in (POLYSKIN, GEOMETRY):
            try:
                name, _ = _p3d_string(r["payload"])
                if name: geometries.append(name)
            except (ValueError, IndexError):
                pass
        elif r["type_id"] == TEXTURE:
            try:
                name, _ = _p3d_string(r["payload"])
                if name and name not in textures:
                    textures.append(name)
            except (ValueError, IndexError):
                pass

    groups = defaultdict(list)
    for g in geometries:
        base = re.sub(
            r"(InitialShape|DamagedShape|DestroyedShape|Spawn\d+InitialShape|Spawn\d+DamagedShape|BaseOnlyShape|BrokenBaseDamagedShape|BrokenBaseInitialShape|DeadInitialShape|Shape\d*)$",
            "", g)
        if base:
            groups[base].append(g)

    items = []
    for base, shapes in sorted(groups.items()):
        items.append({
            "id": base,
            "name": base,
            "category": CATEGORY_PROPS,
            "entry_path": "\\art\\locations\\manhattan\\props.p3d.rz",
            "shapes": shapes,
            "shape_count": len(shapes),
            "geometry_count": len(shapes),
            "total_joints": 0,
            "skeleton_count": 0,
            "animation_count": 0,
            "textures": textures[:4],
        })
    return items


_ENTITY_CATALOG_CACHE: dict[str, dict[str, Any]] = {}

def build_entity_catalog(art_rcf_path: str) -> dict[str, Any]:
    """Scan and index all entities from art.rcf into 4 standard categories."""
    if art_rcf_path in _ENTITY_CATALOG_CACHE:
        return _ENTITY_CATALOG_CACHE[art_rcf_path]
        
    if rcf_extract is None:
        raise RuntimeError("rcf_extract module required")
        
    archive = rcf_extract.CementFile.load(art_rcf_path)
    
    catalog: dict[str, list[dict[str, Any]]] = {
        CATEGORY_POWERS: [],
        CATEGORY_VEHICLES: [],
        CATEGORY_CHARACTERS: [],
        CATEGORY_PROPS: [],
    }
    
    with open(art_rcf_path, "rb") as f:
        for entry in archive.entries:
            meta = archive.get_metadata(entry.name_hash)
            if not meta or not meta.name:
                continue
            cat = classify_entry(meta.name)
            if not cat:
                continue
                
            f.seek(entry.offset)
            raw = f.read(entry.size)
            data = rcf_extract.decompress_rz_payload(raw) if raw.startswith(b"RZ") else raw
            
            if cat == CATEGORY_PROPS:
                prop_items = parse_props_library(data)
                catalog[CATEGORY_PROPS].extend(prop_items)
            else:
                info = inspect_p3d_package(data)
                if not info["geometries"] and not info["composite_drawables"]:
                    continue
                parts = [p for p in meta.name.split("\\") if p]
                entity_id = parts[-1].replace(".p3d.rz", "")
                
                catalog[cat].append({
                    "id": entity_id,
                    "name": FRIENDLY_NAMES.get(entity_id, entity_id),
                    "category": cat,
                    "entry_path": meta.name,
                    "size": entry.size,
                    "decompressed_size": info["total_bytes"],
                    "geometry_count": len(info["geometries"]),
                    "geometries": info["geometries"][:8],
                    "skeleton_count": len(info["skeletons"]),
                    "total_joints": info["total_joints"],
                    "skeletons": info["skeletons"][:4],
                    "animation_count": len(info["animations"]),
                    "texture_count": len(info["textures"]),
                    "textures": info["textures"][:8],
                })
                
    result = {
        "categories": catalog,
        "category_names_zh": CATEGORY_NAMES_ZH,
        "counts": {cat: len(items) for cat, items in catalog.items()},
        "total_entities": sum(len(items) for items in catalog.values()),
    }
    _ENTITY_CATALOG_CACHE[art_rcf_path] = result
    return result
