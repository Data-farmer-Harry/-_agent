from __future__ import annotations

import re


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_+\-./]+|[\u4e00-\u9fff]+")

_CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "ensemble:nvt": ("nvt", "恒温系综", "恒温恒体", "nose-hoover nvt", "fix nvt"),
    "ensemble:npt": ("npt", "恒压恒温", "恒压系综", "fix npt"),
    "ensemble:nph": ("nph", "恒焓恒压", "fix nph"),
    "potential:eam": ("eam", "嵌入原子势", "embedded atom method", "pair_style eam", "eam/alloy"),
    "potential:lj": ("lj", "lennard-jones", "lennard jones", "pair_style lj/cut"),
    "potential:meam": ("meam", "modified embedded atom method", "pair_style meam"),
    "potential:tersoff": ("tersoff", "pair_style tersoff", "共价势"),
    "potential:reaxff": ("reaxff", "reax/c", "reactive force field", "反应力场"),
    "potential:buckingham": ("buckingham", "buck/coul", "离子材料势"),
    "potential:mliap": ("mliap", "machine learning potential", "mace", "机器学习势"),
    "analysis:msd": ("msd", "均方位移", "mean squared displacement", "compute msd", "扩散系数"),
    "analysis:rdf": ("rdf", "径向分布函数", "radial distribution function", "compute rdf"),
    "analysis:stress": ("stress/atom", "compute stress", "原子应力"),
    "analysis:centro": ("centro/atom", "centrosymmetry", "中心对称参数"),
    "analysis:cna": ("cna/atom", "common neighbor analysis", "公共近邻分析"),
    "analysis:voronoi": ("voronoi/atom", "voronoi", "体积分析"),
    "analysis:thermo": ("thermo_style", "热力学输出", "thermo 输出", "温度输出", "能量输出"),
    "error:lost_atoms": ("lost atoms", "原子丢失", "丢原子"),
    "error:non_numeric_pressure": ("non-numeric pressure", "压力非数", "压力nan", "压力发散"),
    "error:pair_coeff_missing": ("pair coeff missing", "pair coeff 缺失", "pair_coeff missing"),
    "error:incorrect_pair_coeff": ("incorrect args for pair coefficients", "pair coefficients 参数错误", "pair coeff 参数错误"),
    "error:potential_file": ("cannot open potential file", "potential file not found", "势文件打不开", "势文件找不到"),
    "error:out_of_range_atoms": ("out of range atoms", "原子超出范围", "domain too large"),
    "error:bond_atoms_missing": ("bond atoms missing", "键合原子丢失"),
    "error:illegal_command": ("illegal command", "非法命令", "unknown command"),
    "error:shake_atoms_missing": ("shake atoms missing", "shake 原子丢失"),
    "concept:eutectic": ("共晶", "eutectic", "共晶点"),
    "concept:eutectoid": ("共析", "eutectoid", "共析点"),
    "concept:peritectic": ("包晶", "peritectic", "包晶反应"),
    "concept:liquidus": ("液相线", "liquidus"),
    "concept:solidus": ("固相线", "solidus"),
    "concept:lever_rule": ("lever rule", "杠杆定律"),
    "concept:tie_line": ("tie line", "连线", "平衡连线"),
    "concept:calphad": ("calphad", "pycalphad", "热力学数据库"),
    "concept:phase_rule": ("gibbs phase rule", "phase rule", "吉布斯相律", "相律", "自由度"),
    "concept:miscibility_gap": ("miscibility gap", "混溶间隙", "不混溶区"),
    "concept:interatomic_potential": (
        "interatomic potential",
        "atomic potential",
        "force field",
        "势函数",
        "原子间势",
        "力场",
    ),
    "concept:force_field_validation": (
        "force field validation",
        "potential validation",
        "validating interatomic potentials",
        "training domain",
        "transferability",
        "benchmark against dft",
        "dft result deviation",
        "dft结果偏差",
        "力场验证",
        "势函数验证",
        "训练域",
        "适用域",
        "可迁移性",
        "偏差超过",
    ),
    "concept:biomaterial": (
        "biomaterial",
        "biocompatibility",
        "implant",
        "medical implant",
        "biomedical device",
        "人工关节",
        "生物相容性",
        "生物材料",
        "骨整合",
        "离子释放",
        "表面氧化层",
    ),
    "concept:martensite": ("martensite", "马氏体", "无扩散相变"),
    "concept:austenite": ("austenite", "奥氏体", "gamma iron", "γ铁"),
    "concept:ferrite": ("ferrite", "铁素体", "alpha iron", "α铁"),
    "concept:cementite": ("cementite", "渗碳体", "fe3c"),
    "concept:pearlite": ("pearlite", "珠光体"),
    "concept:bainite": ("bainite", "贝氏体"),
    "concept:hall_petch": ("hall-petch", "hall petch", "细晶强化"),
    "concept:miller_index": ("miller index", "miller indices", "米勒指数", "晶面指数"),
    "concept:materials_science": (
        "processing-structure-properties-performance",
        "加工-结构-性能-服役",
        "材料的加工",
        "加工、内部结构",
    ),
    "concept:vacancy_site": ("vacancy defect", "空位缺陷", "缺少一个本应占据格点的原子"),
    "concept:creep": ("creep", "蠕变", "高温和恒定载荷", "随时间缓慢积累变形"),
    "concept:spinodal": (
        "spinodal decomposition",
        "调幅分解",
        "旋节分解",
        "自由能曲线负曲率",
        "不需要克服形核势垒",
    ),
    "analysis:xrd": ("xrd", "x-ray diffraction", "x ray diffraction", "x射线衍射", "物相鉴定"),
    "analysis:ebsd": ("ebsd", "electron backscatter diffraction", "电子背散射衍射"),
    "analysis:eds": ("eds", "edx", "energy-dispersive x-ray spectroscopy", "元素分析"),
    "analysis:dsc": ("dsc", "differential scanning calorimetry", "差示扫描量热"),
    "concept:formation_energy": ("formation energy", "形成能", "formation enthalpy"),
    "concept:energy_above_hull": ("energy above hull", "hull energy", "凸包能量", "相稳定性"),
    "concept:band_gap": ("band gap", "bandgap", "带隙", "禁带宽度"),
    "concept:elastic_tensor": ("elastic tensor", "elastic constants", "弹性张量", "弹性常数"),
    "concept:phonon": ("phonon", "声子", "动力学稳定性"),
    "concept:oxide": ("oxide", "alumina", "al2o3", "ceramic oxide", "氧化物", "氧化铝", "陶瓷氧化物"),
    "concept:defect": ("defect", "vacancy", "interstitial", "substitutional", "缺陷", "空位", "间隙原子", "替位"),
    "concept:dislocation": ("dislocation", "位错"),
    "concept:grain_boundary": ("grain boundary", "晶界"),
    "concept:stacking_fault": ("stacking fault", "层错"),
    "concept:arrhenius": ("arrhenius", "activation energy", "扩散激活能", "阿伦尼乌斯"),
    "concept:dft": ("dft", "density functional theory", "密度泛函"),
    "concept:high_entropy_alloy": ("high entropy alloy", "hea", "高熵合金"),
    "concept:multi_principal_element_alloy": (
        "multi principal element alloy",
        "multi-principal element alloy",
        "five or more elements",
        "equiatomic alloy",
        "configurational entropy",
        "mixing entropy",
        "solid solution",
        "五种以上元素",
        "等摩尔比",
        "混合熵",
        "构型熵",
        "固溶体",
        "抑制析出",
    ),
    "database:materials_project": ("materials project", "mp api", "materials project api"),
    "database:jarvis": ("jarvis", "jarvis-dft", "nist jarvis"),
    "database:aflow": ("aflow", "aflux", "aflow api"),
    "database:matminer": ("matminer", "matminer datasets"),
}

_MATERIAL_ALIASES: dict[str, str] = {
    "al": "Al",
    "aluminum": "Al",
    "aluminium": "Al",
    "铝": "Al",
    "cu": "Cu",
    "copper": "Cu",
    "铜": "Cu",
    "ni": "Ni",
    "nickel": "Ni",
    "镍": "Ni",
    "fe": "Fe",
    "iron": "Fe",
    "铁": "Fe",
    "mg": "Mg",
    "magnesium": "Mg",
    "镁": "Mg",
    "zn": "Zn",
    "zinc": "Zn",
    "锌": "Zn",
    "pb": "Pb",
    "lead": "Pb",
    "铅": "Pb",
    "sn": "Sn",
    "tin": "Sn",
    "锡": "Sn",
    "ti": "Ti",
    "titanium": "Ti",
    "钛": "Ti",
    "cr": "Cr",
    "chromium": "Cr",
    "铬": "Cr",
    "nb": "Nb",
    "niobium": "Nb",
    "铌": "Nb",
    "si": "Si",
    "silicon": "Si",
    "硅": "Si",
    "c": "C",
    "carbon": "C",
    "碳": "C",
    "o": "O",
    "oxygen": "O",
    "氧": "O",
    "co": "Co",
    "cobalt": "Co",
    "钴": "Co",
    "mn": "Mn",
    "manganese": "Mn",
    "锰": "Mn",
    "mo": "Mo",
    "molybdenum": "Mo",
    "钼": "Mo",
    "w": "W",
    "tungsten": "W",
    "钨": "W",
    "v": "V",
    "vanadium": "V",
    "钒": "V",
    "li": "Li",
    "lithium": "Li",
    "锂": "Li",
    "na": "Na",
    "sodium": "Na",
    "钠": "Na",
    "pt": "Pt",
    "platinum": "Pt",
    "铂": "Pt",
    "pd": "Pd",
    "palladium": "Pd",
    "钯": "Pd",
}

_KNOWN_MATERIAL_SYMBOLS = frozenset(_MATERIAL_ALIASES.values())


def normalize_text(text: str) -> str:
    lowered = (text or "").strip().lower()
    lowered = lowered.replace("–", "-").replace("—", "-")
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def tokenize_text(text: str) -> tuple[str, ...]:
    tokens = [token.lower() for token in TOKEN_PATTERN.findall(text or "")]
    return tuple(dict.fromkeys(token for token in tokens if token))


def normalize_material(material: str | None) -> str | None:
    if not material:
        return None
    normalized = _MATERIAL_ALIASES.get(normalize_text(material))
    if normalized:
        return normalized
    stripped = material.strip()
    if len(stripped) == 2 and stripped[0].isalpha() and stripped[1].isalpha():
        return stripped[0].upper() + stripped[1].lower()
    if len(stripped) == 1 and stripped.isalpha():
        return stripped.upper()
    return stripped or None


def extract_materials(text: str) -> tuple[str, ...]:
    lowered = normalize_text(text)
    materials: list[str] = []
    for alias, symbol in _MATERIAL_ALIASES.items():
        if alias.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered):
                materials.append(symbol)
        elif alias in lowered:
            materials.append(symbol)
    for symbol in _extract_formula_materials(text):
        materials.append(symbol)
    return tuple(dict.fromkeys(materials))


def canonical_terms(text: str) -> tuple[str, ...]:
    lowered = normalize_text(text)
    matched: list[str] = []
    for canonical, aliases in _CANONICAL_ALIASES.items():
        for alias in aliases:
            alias_lower = alias.lower()
            if alias_lower.isascii():
                if re.search(rf"(?<![a-z0-9]){re.escape(alias_lower)}(?![a-z0-9])", lowered):
                    matched.append(canonical)
                    break
            elif alias_lower in lowered:
                matched.append(canonical)
                break
    matched.extend(f"material:{item}" for item in extract_materials(text))
    return tuple(dict.fromkeys(matched))


def _extract_formula_materials(text: str) -> tuple[str, ...]:
    """Extract known element symbols from compact formula/alloy tokens.

    This catches user queries such as ``Al2O3`` and ``AlCoCrFeNi`` that do not
    contain spaces, while avoiding ordinary English words such as "Control" by
    requiring at least two parsed element symbols or a digit in the token.
    """

    materials: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]*", text or ""):
        if not any(character.isupper() for character in token):
            continue
        parsed = [symbol for symbol in re.findall(r"[A-Z][a-z]?", token) if symbol in _KNOWN_MATERIAL_SYMBOLS]
        if len(parsed) >= 2 or (parsed and any(character.isdigit() for character in token)):
            materials.extend(parsed)
    return tuple(dict.fromkeys(materials))


def canonical_expansion_terms(text: str, *, max_aliases_per_term: int = 6) -> tuple[str, ...]:
    """Return compact synonym/translation terms for matched canonical concepts."""

    lowered = normalize_text(text)
    expanded: list[str] = []
    for canonical in canonical_terms(text):
        if canonical.startswith("material:"):
            continue
        label = canonical.split(":", 1)[-1].replace("_", " ")
        expanded.append(label)
        aliases = _CANONICAL_ALIASES.get(canonical, ())
        added_aliases = 0
        for alias in aliases:
            alias_text = " ".join(str(alias).split())
            if not alias_text or normalize_text(alias_text) in lowered:
                continue
            expanded.append(alias_text)
            added_aliases += 1
            if added_aliases >= max_aliases_per_term:
                break
    return tuple(dict.fromkeys(expanded))


def material_expansion_terms(materials: tuple[str, ...] | list[str], *, max_aliases_per_material: int = 2) -> tuple[str, ...]:
    """Return element symbols plus a few common names/translations for a query."""

    expanded: list[str] = []
    for symbol in materials:
        normalized = normalize_material(symbol)
        if not normalized:
            continue
        expanded.append(normalized)
        added_aliases = 0
        for alias, alias_symbol in _MATERIAL_ALIASES.items():
            if alias_symbol != normalized or alias == normalized.lower():
                continue
            expanded.append(alias)
            added_aliases += 1
            if added_aliases >= max_aliases_per_material:
                break
    return tuple(dict.fromkeys(expanded))


def infer_domain_hint(text: str) -> str | None:
    lowered = normalize_text(text)
    if any(token in lowered for token in ("lammps", "fix ", "pair_style", "pair coeff", "msd", "rdf", "ovito")):
        return "lammps"
    if any(token in lowered for token in ("相图", "热力学", "液相线", "固相线", "共晶", "共析", "包晶")):
        return "thermodynamics"
    if any(
        token in lowered
        for token in (
            "材料",
            "晶体结构",
            "fcc",
            "bcc",
            "hcp",
            "formation energy",
            "energy above hull",
            "band gap",
            "elastic",
            "phonon",
            "defect",
            "vacancy",
            "grain boundary",
            "dislocation",
            "materials project",
            "jarvis",
            "aflow",
            "matminer",
        )
    ):
        return "materials"
    return None
