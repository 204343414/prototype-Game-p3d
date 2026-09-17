"""Prototype entity catalog and census engine.

Categorizes and indexes all Pure3D entities in art.rcf into five standard categories:
  1. powers: Alex Mercer base model, weapon morphs (Claws, Blade, Hammerfist, Whipfist,
     Musclemass), defensive powers (Shield, Armor), devastators (Groundspike, Tendril Barrage),
     and military/civilian tactical disguises.
  2. vehicles: Heavy military armor (Tanks, APCs, Blackhawk, Gunship, F45 Jet) and
     civilian city vehicles (Taxis, Police cars, Ambulances, Buses, Trucks, Sedans).
  3. characters: Story NPCs (Dana, Karen, Greene, Specialist Cross, Dr. Ragland),
     bosses/infected (Brawler, Hunter, Hydra, Supreme Hunter), SuperSoldiers,
     Blackwatch troopers, and Marines.
  4. pedestrians: Manhattan civilians and pedestrian demographic NPCs (ped_m_*, ped_f_*).
  5. props: Interactive & destructible Manhattan environment entities (Water towers,
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
CATEGORY_PEDESTRIANS = "pedestrians"
CATEGORY_PROPS = "props"

CATEGORIES = (
    CATEGORY_POWERS,
    CATEGORY_VEHICLES,
    CATEGORY_CHARACTERS,
    CATEGORY_PEDESTRIANS,
    CATEGORY_PROPS,
)

CATEGORY_NAMES_ZH = {
    CATEGORY_POWERS: "主角形态与生化武装",
    CATEGORY_VEHICLES: "载具与重装武备系统",
    CATEGORY_CHARACTERS: "剧情角色、变异体与守望军团",
    CATEGORY_PEDESTRIANS: "曼哈顿市民与路人 NPC",
    CATEGORY_PROPS: "曼哈顿环境与可破坏道具",
}

# Friendly entity display titles
FRIENDLY_NAMES = {
    # Powers (主角形态与能力)
    "alex": "Alex Mercer (默认兜帽本体)",
    "alex_claws": "生化利爪形态 (Claws)",
    "alex_blades": "致命利刃形态 (Blade)",
    "alex_hammerfist": "充气重锤巨拳 (Hammerfist)",
    "alex_whipfist": "远距生化鞭拳 (Whipfist)",
    "alex_musclemass": "肌肉强化模式 (Musclemass)",
    "alex_shield": "生化护盾模式 (Shield)",
    "alex_armour": "充能重装甲模式 (Armor)",
    "alex_spines": "墓碑地刺歼灭技 (Groundspike)",
    "alex_parasite": "万千触须终结技 (Tendril Barrage)",
    "alexshotbody_disguise": "负伤残躯 (Damaged Body)",
    "soldier_disguise": "陆军战术伪装 (Soldier Disguise)",
    "commander_disguise": "军方指挥官伪装 (Commander Disguise)",
    "pilot_disguise": "军用飞行员伪装 (Pilot Disguise)",
    "bwtrooper_disguise": "黑色守望突击队员伪装 (Blackwatch Trooper)",
    "bwofficer_disguise": "黑色守望军官伪装 (Blackwatch Officer)",
    "bwscientist2008_disguise": "便衣科研人员伪装 (Scientist Disguise)",
    "bwplainclothes01_disguise": "便衣特工 01 伪装 (Plainclothes Agent 01)",
    "bwplainclothes02_disguise": "便衣特工 02 伪装 (Plainclothes Agent 02)",
    # Vehicles (载具与重装武备)
    "tank_ram_marine": "M1A2 艾布拉姆斯主战坦克",
    "tank_ram_thermobolic": "热压重型攻坚坦克 (Thermobaric)",
    "apc_m2_marine": "M2 步兵装甲运兵车 (APC)",
    "heli_bh_marine": "UH-60 黑鹰武装运输直升机",
    "heli_gunship_marine_core": "AH-64 阿帕奇武装直升机",
    "f45Thunder001Military": "F-45 雷霆超音速战斗机",
    "bloodtoxDriller001": "毒气钻机车 (Bloodtox Driller)",
    "generic_taxi": "纽约经典黄色出租车 (Taxi)",
    "Mini_van_Taxi": "商务出租车 (Minivan Taxi)",
    "police_car": "纽约警车 (Police Car)",
    "ambulance": "紧急救援救护车 (Ambulance)",
    "busOS001": "城市单层公交巴士",
    "redTourBus001": "曼哈顿双层红色观光巴士",
    "limoOS001": "加长礼宾豪华轿车 (Limo)",
    "humveeAvenger001": "复仇者防空悍马 (Humvee)",
    "titaniumGT001": "钛金跑车 GT",
    "tankerTruck001": "大型重载油罐车",
    "garbageTruck001": "城市环卫垃圾车",
    "fireTruck001": "重型消防云梯车",
    "flatbedTruck001": "平板重型卡车",
    "armoredCar001": "重装防弹运钞车",
    # Characters (剧情角色、变异体与守望部队)
    "Soldier": "黑色守望与陆军军备全家桶 (Soldier/Blackwatch/Weapons)",
    "soldier": "黑色守望与陆军军备全家桶 (Soldier/Blackwatch/Weapons)",
    "DanaMercer": "达娜·墨瑟 (Dana Mercer)",
    "karen_parker": "凯伦·帕克 (Karen Parker)",
    "ElizabethGreene": "伊丽莎白·格林 (Elizabeth Greene)",
    "mother": "格林母体巨兽形态 (Mother)",
    "specialist": "黑色守望指挥官 Cross (Specialist)",
    "Specialist": "黑色守望指挥官 Cross (Specialist)",
    "ragland_suit": "拉格兰医生 (Dr. Ragland)",
    "ragland_morgue_ingame": "停尸房内的拉格兰医生",
    "SuperSoldier": "黑色守望超级士兵 (Super Soldier)",
    "SuperSoldierE10M4": "强化型超级士兵 (Super Soldier Elite)",
    "Brawler": "格斗者变异体 (Brawler)",
    "LeaderHunter": "猎手领袖 (Leader Hunter)",
    "StripedLeaderHunter": "斑纹猎手领袖 (Striped Leader Hunter)",
    "Hydra": "九头蛇巨兽触手 (Hydra)",
    "supreme_hunter": "终极至尊猎手 (Supreme Hunter)",
    "supreme_hunter_weak": "虚弱状态至尊猎手",
    "bw_scientist_2008": "黑色守望主任科学家",
    "infected2_all": "二次感染变异人群",
    "infected_businessman_fat": "变异肥胖商人",
}


def classify_entry(name: str) -> str | None:
    """Classify an RCF entry path into one of the 5 standard categories."""
    clean = name.lower().replace("\\", "/")
    if clean.endswith(("_tod.p3d.rz", "_fig.p3d.rz", "_lod.p3d.rz", "_camera.p3d.rz", "_nis.p3d.rz")):
        return None
    if "/powers/" in clean or clean == "/art/alex/alex.p3d.rz":
        return CATEGORY_POWERS
    if "/vehicles/" in clean or ("/missions/" in clean and any(k in clean for k in ("tank", "apc", "heli", "f45", "driller"))):
        return CATEGORY_VEHICLES
    if "/pedestrians/" in clean:
        return CATEGORY_PEDESTRIANS
    if "/missions/" in clean and not any(k in clean for k in ("tank", "apc", "heli", "f45", "driller", "props", "tod", "fig", "effects", "characters")):
        return CATEGORY_CHARACTERS
    if clean == "/art/locations/manhattan/props.p3d.rz":
        return CATEGORY_PROPS
    return None


def format_pedestrian_title(entity_id: str) -> str:
    """Generate friendly localized names for pedestrian variants."""
    clean = entity_id
    gender = "女性市民" if "ped_f_" in clean else ("男性市民" if "ped_m_" in clean else "纽约市民")
    
    tags = []
    if "inf_" in clean:
        tags.append("轻度感染")
    if "su_" in clean or "suit" in clean.lower():
        tags.append("西装")
    elif "bu_" in clean or "business" in clean.lower():
        tags.append("商务")
    elif "ct_" in clean or "coat" in clean.lower():
        tags.append("大衣外套")
    elif "li_" in clean:
        tags.append("休闲夹克")
    elif "uw_" in clean:
        tags.append("冬装棉服")
    elif "fd_" in clean:
        tags.append("卫衣长裤")
    elif "hr_" in clean:
        tags.append("短袖衬衫")
    elif "ue_" in clean:
        tags.append("便服")
        
    tag_str = f" [{', '.join(tags)}]" if tags else ""
    return f"{gender}{tag_str} ({entity_id})"


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
    """Scan and index all entities from art.rcf into 5 standard categories."""
    if art_rcf_path in _ENTITY_CATALOG_CACHE:
        return _ENTITY_CATALOG_CACHE[art_rcf_path]
        
    if rcf_extract is None:
        raise RuntimeError("rcf_extract module required")
        
    archive = rcf_extract.CementFile.load(art_rcf_path)
    
    catalog: dict[str, list[dict[str, Any]]] = {
        CATEGORY_POWERS: [],
        CATEGORY_VEHICLES: [],
        CATEGORY_CHARACTERS: [],
        CATEGORY_PEDESTRIANS: [],
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
                
                name = FRIENDLY_NAMES.get(entity_id)
                if not name:
                    if cat == CATEGORY_PEDESTRIANS:
                        name = format_pedestrian_title(entity_id)
                    else:
                        name = entity_id
                
                catalog[cat].append({
                    "id": entity_id,
                    "name": name,
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
