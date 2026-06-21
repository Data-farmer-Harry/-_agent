from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib import error, parse, request


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_ROOT / "configs" / "materials_rag_wikipedia.jsonl"
API_TEMPLATE = "https://api.wikimedia.org/core/v1/wikipedia/en/page/{title}/html"
USER_AGENT = "PhaseDiagramAgent/0.2 materials-rag/1.0 (educational research assistant)"
SKIPPED_SECTIONS = {
    "history",
    "see also",
    "references",
    "bibliography",
    "further reading",
    "external links",
    "notes",
}


@dataclass(frozen=True)
class Topic:
    title: str
    domain: str
    keywords: tuple[str, ...]
    methods: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()


TOPICS: tuple[Topic, ...] = (
    Topic("Materials science", "materials", ("processing structure properties performance", "materials engineering")),
    Topic("Microstructure", "materials", ("microstructure", "grains", "phases"), ("characterization",)),
    Topic("Crystal structure", "crystallography", ("crystal structure", "lattice", "unit cell"), ("crystallography",)),
    Topic("Crystallography", "crystallography", ("crystallography", "symmetry", "diffraction"), ("crystallography",)),
    Topic("Crystallographic defect", "crystallography", ("crystal defect", "point defect", "line defect")),
    Topic("Vacancy defect", "crystallography", ("vacancy", "point defect", "diffusion")),
    Topic("Dislocation", "mechanics", ("dislocation", "plastic deformation", "slip")),
    Topic("Grain boundary", "materials", ("grain boundary", "interface", "diffusion")),
    Topic("Phase diagram", "thermodynamics", ("phase diagram", "phase equilibrium", "composition temperature", "peritectic reaction", "iron carbon phase diagram"), ("CALPHAD",)),
    Topic("Phase transition", "thermodynamics", ("phase transition", "phase transformation", "critical point")),
    Topic("Solid solution", "thermodynamics", ("solid solution", "substitutional", "interstitial")),
    Topic("Intermetallic", "metallurgy", ("intermetallic", "ordered compound", "alloy")),
    Topic("Eutectic system", "thermodynamics", ("eutectic", "liquidus", "solidus")),
    Topic("Alloy", "metallurgy", ("alloy", "metal", "composition")),
    Topic("Thermodynamics", "thermodynamics", ("thermodynamics", "equilibrium", "state function")),
    Topic("Gibbs free energy", "thermodynamics", ("Gibbs free energy", "chemical equilibrium", "phase stability")),
    Topic("Chemical potential", "thermodynamics", ("chemical potential", "partial molar free energy", "equilibrium")),
    Topic("Diffusion", "kinetics", ("diffusion", "mass transport", "concentration gradient"), ("diffusion",)),
    Topic("Fick's laws of diffusion", "kinetics", ("Fick law", "diffusion coefficient", "flux"), ("diffusion",)),
    Topic("Nucleation", "kinetics", ("nucleation", "critical nucleus", "phase transformation")),
    Topic("Spinodal decomposition", "kinetics", ("spinodal decomposition", "phase separation", "free energy")),
    Topic("Elastic modulus", "mechanics", ("elastic modulus", "Young modulus", "stiffness")),
    Topic("Yield (engineering)", "mechanics", ("yield strength", "plastic deformation", "stress strain")),
    Topic("Fracture mechanics", "mechanics", ("fracture mechanics", "stress intensity", "crack")),
    Topic("Creep (deformation)", "mechanics", ("creep", "high temperature deformation", "time dependent")),
    Topic("Fatigue (material)", "mechanics", ("fatigue", "cyclic loading", "crack growth")),
    Topic("Hardness", "mechanics", ("hardness", "indentation", "wear resistance")),
    Topic("Toughness", "mechanics", ("toughness", "fracture energy", "impact")),
    Topic("Work hardening", "metallurgy", ("work hardening", "strain hardening", "dislocation density")),
    Topic("Heat treating", "processing", ("heat treatment", "microstructure", "mechanical properties")),
    Topic("Annealing (materials science)", "processing", ("annealing", "recovery", "recrystallization")),
    Topic("Quenching", "processing", ("quenching", "rapid cooling", "martensite")),
    Topic("Tempering (metallurgy)", "processing", ("tempering", "martensite", "toughness")),
    Topic("Precipitation hardening", "processing", ("precipitation hardening", "age hardening", "precipitate")),
    Topic("Sintering", "processing", ("sintering", "powder", "densification")),
    Topic("Powder metallurgy", "processing", ("powder metallurgy", "compaction", "sintering")),
    Topic("Ceramic", "materials", ("ceramic", "inorganic", "brittle"), materials=("ceramic",)),
    Topic("Polymer", "materials", ("polymer", "macromolecule", "thermoplastic"), materials=("polymer",)),
    Topic("Composite material", "materials", ("composite", "matrix", "reinforcement"), materials=("composite",)),
    Topic("Semiconductor", "electronic_materials", ("semiconductor", "carrier", "band gap"), materials=("semiconductor",)),
    Topic("Biomaterial", "materials", ("biomaterial", "biocompatibility", "implant"), materials=("biomaterial",)),
    Topic("Nanomaterials", "materials", ("nanomaterial", "nanoscale", "surface area"), materials=("nanomaterial",)),
    Topic("Superalloy", "metallurgy", ("superalloy", "high temperature", "creep resistance"), materials=("Ni", "Co")),
    Topic("Steel", "metallurgy", ("steel", "iron carbon", "heat treatment"), materials=("Fe", "C")),
    Topic("Aluminium alloy", "metallurgy", ("aluminium alloy", "age hardening", "lightweight"), materials=("Al",)),
    Topic("Titanium alloys", "metallurgy", ("titanium alloy", "alpha beta phase", "strength to weight"), materials=("Ti",)),
    Topic("Band gap", "electronic_materials", ("band gap", "valence band", "conduction band")),
    Topic("Density functional theory", "computational_materials", ("density functional theory", "DFT", "electronic structure"), ("DFT",)),
    Topic("Molecular dynamics", "computational_materials", ("molecular dynamics", "atomistic simulation", "trajectory"), ("molecular_dynamics",)),
    Topic("Embedded atom model", "computational_materials", ("embedded atom model", "EAM", "metal potential"), ("molecular_dynamics",)),
    Topic("Phonon", "electronic_materials", ("phonon", "lattice vibration", "thermal conductivity")),
    Topic("X-ray crystallography", "characterization", ("X-ray diffraction", "crystal structure", "Bragg"), ("XRD",)),
    Topic("Electron microscope", "characterization", ("electron microscopy", "microstructure", "electron beam"), ("electron_microscopy",)),
    Topic("Scanning electron microscope", "characterization", ("SEM", "surface morphology", "electron microscopy"), ("SEM",)),
    Topic("Transmission electron microscopy", "characterization", ("TEM", "thin specimen", "crystal defects"), ("TEM",)),
    Topic("Phase rule", "thermodynamics", ("Gibbs phase rule", "degrees of freedom", "components phases"), ("phase_diagram",)),
    Topic("Lever rule", "thermodynamics", ("lever rule", "phase fraction", "tie line"), ("phase_diagram",)),
    Topic("Liquidus and solidus", "thermodynamics", ("liquidus", "solidus", "first solid", "last liquid", "phase boundary"), ("phase_diagram",)),
    Topic("Miscibility gap", "thermodynamics", ("miscibility gap", "phase separation", "binodal"), ("phase_diagram",)),
    Topic("CALPHAD", "computational_materials", ("CALPHAD", "thermodynamic assessment", "phase diagram calculation"), ("CALPHAD",)),
    Topic("Enthalpy of mixing", "thermodynamics", ("enthalpy of mixing", "solution thermodynamics", "mixing")),
    Topic("Activity coefficient", "thermodynamics", ("activity coefficient", "nonideal solution", "chemical potential")),
    Topic("Regular solution", "thermodynamics", ("regular solution", "mixing enthalpy", "configurational entropy")),
    Topic("Ideal solution", "thermodynamics", ("ideal solution", "Raoult law", "activity")),
    Topic("Metastability", "thermodynamics", ("metastability", "local free energy minimum", "kinetic barrier")),
    Topic("Crystal polymorphism", "crystallography", ("polymorphism", "crystal structure transformation", "solid phase")),
    Topic("Allotropy", "crystallography", ("allotropy", "element crystal forms", "phase transformation")),
    Topic("Martensite", "metallurgy", ("martensite", "diffusionless transformation", "quenching"), materials=("Fe", "C")),
    Topic("Austenite", "metallurgy", ("austenite", "gamma iron", "face centered cubic"), materials=("Fe", "C")),
    Topic("Allotropes of iron", "metallurgy", ("ferrite", "alpha iron", "delta iron", "body centered cubic"), materials=("Fe", "C")),
    Topic("Cementite", "metallurgy", ("cementite", "iron carbide", "Fe3C"), materials=("Fe", "C")),
    Topic("Pearlite", "metallurgy", ("pearlite", "ferrite cementite lamellae", "eutectoid"), materials=("Fe", "C")),
    Topic("Bainite", "metallurgy", ("bainite", "steel transformation", "ferrite carbide"), materials=("Fe", "C")),
    Topic("Glass transition", "thermodynamics", ("glass transition", "amorphous", "glass transition temperature")),
    Topic("Curie temperature", "thermodynamics", ("Curie temperature", "magnetic phase transition", "ferromagnetism")),
    Topic("Grain growth", "processing", ("grain growth", "grain boundary migration", "annealing")),
    Topic("Recrystallization (metallurgy)", "processing", ("recrystallization", "new strain free grains", "annealing")),
    Topic("Ostwald ripening", "kinetics", ("Ostwald ripening", "coarsening", "particle growth")),
    Topic("Kirkendall effect", "kinetics", ("Kirkendall effect", "unequal diffusion", "marker movement"), ("diffusion",)),
    Topic("Arrhenius equation", "kinetics", ("Arrhenius equation", "activation energy", "temperature dependence")),
    Topic("Grain boundary strengthening", "mechanics", ("Hall Petch", "grain size strengthening", "yield strength")),
    Topic("Bravais lattice", "crystallography", ("Bravais lattice", "lattice types", "translational symmetry"), ("crystallography",)),
    Topic("Miller index", "crystallography", ("Miller indices", "crystal planes", "crystal directions"), ("crystallography",)),
    Topic("Space group", "crystallography", ("space group", "crystal symmetry", "symmetry operations"), ("crystallography",)),
    Topic("Crystal system", "crystallography", ("crystal system", "lattice parameters", "symmetry"), ("crystallography",)),
    Topic("Cubic crystal system", "crystallography", ("face centered cubic", "body centered cubic", "simple cubic"), ("crystallography",)),
    Topic("Hexagonal crystal family", "crystallography", ("hexagonal close packed", "HCP", "hexagonal lattice"), ("crystallography",)),
    Topic("Schottky defect", "crystallography", ("Schottky defect", "paired vacancies", "ionic crystal")),
    Topic("Frenkel defect", "crystallography", ("Frenkel defect", "vacancy interstitial pair", "ionic crystal")),
    Topic("Stacking fault", "crystallography", ("stacking fault", "planar defect", "partial dislocation")),
    Topic("Monte Carlo method", "computational_materials", ("Monte Carlo", "statistical sampling", "materials simulation"), ("Monte_Carlo",)),
    Topic("Interatomic potential", "computational_materials", ("interatomic potential", "force field", "atomistic simulation", "MEAM", "Stillinger Weber potential"), ("molecular_dynamics",)),
    Topic("Lennard-Jones potential", "computational_materials", ("Lennard Jones potential", "van der Waals", "pair potential"), ("molecular_dynamics",)),
    Topic("ReaxFF", "computational_materials", ("ReaxFF", "reactive force field", "bond breaking"), ("molecular_dynamics",)),
    Topic("Density of states", "electronic_materials", ("density of states", "electronic states", "Fermi level"), ("DFT",)),
    Topic("X-ray diffraction", "characterization", ("X-ray diffraction", "XRD", "phase identification"), ("XRD",)),
    Topic("Bragg's law", "characterization", ("Bragg law", "diffraction angle", "lattice spacing"), ("XRD",)),
    Topic("Electron diffraction", "characterization", ("electron diffraction", "reciprocal lattice", "crystal structure"), ("TEM",)),
    Topic("Energy-dispersive X-ray spectroscopy", "characterization", ("energy dispersive spectroscopy", "EDS", "elemental analysis"), ("EDS", "SEM")),
    Topic("Electron backscatter diffraction", "characterization", ("electron backscatter diffraction", "EBSD", "grain orientation"), ("EBSD", "SEM")),
    Topic("Differential scanning calorimetry", "characterization", ("differential scanning calorimetry", "DSC", "thermal transition"), ("DSC",)),
    Topic("Atom probe", "characterization", ("atom probe tomography", "APT", "three dimensional composition"), ("APT",)),
    Topic("Nanoindentation", "characterization", ("nanoindentation", "hardness", "reduced modulus"), ("nanoindentation",)),
    Topic("Metallography", "characterization", ("metallography", "polishing etching", "microstructure"), ("optical_microscopy",)),
    Topic("High-entropy alloy", "metallurgy", ("high entropy alloy", "multi principal element alloy", "configurational entropy"), materials=("alloy",)),
    Topic("Shape-memory alloy", "metallurgy", ("shape memory alloy", "martensitic transformation", "superelasticity"), materials=("Ni", "Ti")),
    Topic("Amorphous metal", "materials", ("amorphous metal", "metallic glass", "noncrystalline alloy"), materials=("alloy",)),
    Topic("Perovskite (structure)", "materials", ("perovskite structure", "ABX3", "oxide"), materials=("oxide",)),
    Topic("Corrosion", "materials", ("corrosion", "electrochemical degradation", "oxidation")),
    Topic("Stainless steel", "metallurgy", ("stainless steel", "chromium", "passivation"), materials=("Fe", "Cr")),
    Topic("Magnesium alloy", "metallurgy", ("magnesium alloy", "lightweight alloy", "HCP"), materials=("Mg",)),
    Topic("List of copper alloys", "metallurgy", ("copper alloy", "brass", "bronze"), materials=("Cu",)),
)

CHINESE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Materials science": ("材料科学", "加工-结构-性能-服役"),
    "Microstructure": ("微观组织", "显微组织"),
    "Crystal structure": ("晶体结构", "晶格", "晶胞"),
    "Crystallography": ("晶体学", "对称性", "衍射"),
    "Crystallographic defect": ("晶体缺陷", "点缺陷", "线缺陷"),
    "Vacancy defect": ("空位缺陷", "空位", "点缺陷"),
    "Dislocation": ("位错", "滑移", "塑性变形"),
    "Grain boundary": ("晶界", "界面", "晶界扩散"),
    "Phase diagram": ("相图", "相平衡", "液相线", "固相线", "包晶反应", "铁碳相图"),
    "Phase transition": ("相变", "相转变"),
    "Solid solution": ("固溶体", "置换固溶体", "间隙固溶体"),
    "Intermetallic": ("金属间化合物", "有序相"),
    "Eutectic system": ("共晶", "共晶反应", "共晶点"),
    "Thermodynamics": ("热力学", "平衡", "状态函数"),
    "Gibbs free energy": ("吉布斯自由能", "相稳定性"),
    "Chemical potential": ("化学势", "偏摩尔自由能"),
    "Diffusion": ("扩散", "质量传输", "浓度梯度"),
    "Fick's laws of diffusion": ("菲克定律", "扩散系数", "扩散通量"),
    "Nucleation": ("形核", "临界晶核", "相变"),
    "Spinodal decomposition": ("调幅分解", "旋节分解", "无障碍分相"),
    "Elastic modulus": ("弹性模量", "杨氏模量", "刚度"),
    "Yield (engineering)": ("屈服", "屈服强度", "应力应变"),
    "Fracture mechanics": ("断裂力学", "应力强度因子", "裂纹"),
    "Creep (deformation)": ("蠕变", "高温蠕变", "时间相关变形"),
    "Fatigue (material)": ("疲劳", "循环载荷", "疲劳裂纹"),
    "Hardness": ("硬度", "压痕"),
    "Toughness": ("韧性", "断裂能", "冲击韧性"),
    "Work hardening": ("加工硬化", "应变硬化", "位错密度"),
    "Heat treating": ("热处理", "组织调控"),
    "Annealing (materials science)": ("退火", "回复", "再结晶"),
    "Quenching": ("淬火", "快速冷却", "马氏体"),
    "Tempering (metallurgy)": ("回火", "回火马氏体"),
    "Precipitation hardening": ("析出强化", "时效强化", "第二相颗粒", "阻碍位错"),
    "Sintering": ("烧结", "致密化"),
    "Powder metallurgy": ("粉末冶金", "压制", "烧结"),
    "Semiconductor": ("半导体", "载流子", "能带"),
    "Band gap": ("带隙", "价带", "导带"),
    "Density functional theory": ("密度泛函理论", "电子结构", "第一性原理"),
    "Molecular dynamics": ("分子动力学", "原子轨迹", "牛顿运动方程"),
    "Embedded atom model": ("嵌入原子模型", "EAM势", "金属势函数"),
    "Phonon": ("声子", "晶格振动", "热导率"),
    "X-ray crystallography": ("X射线晶体学", "X射线衍射"),
    "Electron microscope": ("电子显微镜", "电子束"),
    "Scanning electron microscope": ("扫描电子显微镜", "SEM", "表面形貌"),
    "Transmission electron microscopy": ("透射电子显微镜", "TEM", "薄样品", "位错观察"),
    "Phase rule": ("吉布斯相律", "自由度", "组元数", "相数"),
    "Lever rule": ("杠杆定律", "相分数", "连线"),
    "Liquidus and solidus": ("液相线", "固相线", "开始凝固", "完全凝固"),
    "Miscibility gap": ("混溶间隙", "不混溶区", "分相"),
    "CALPHAD": ("计算相图", "热力学评估", "CALPHAD", "TDB"),
    "Enthalpy of mixing": ("混合焓", "溶体热力学"),
    "Activity coefficient": ("活度系数", "非理想溶体"),
    "Regular solution": ("正则溶体", "混合焓"),
    "Ideal solution": ("理想溶体", "拉乌尔定律"),
    "Metastability": ("亚稳态", "动力学势垒"),
    "Crystal polymorphism": ("多晶型", "同质多晶", "晶体结构转变"),
    "Allotropy": ("同素异形", "同素异性"),
    "Martensite": ("马氏体", "无扩散相变"),
    "Austenite": ("奥氏体", "γ铁", "面心立方"),
    "Allotropes of iron": ("铁素体", "α铁", "δ铁", "体心立方"),
    "Cementite": ("渗碳体", "碳化三铁", "Fe3C"),
    "Pearlite": ("珠光体", "共析", "铁素体渗碳体"),
    "Bainite": ("贝氏体", "钢相变"),
    "Glass transition": ("玻璃化转变", "玻璃转变温度", "Tg"),
    "Curie temperature": ("居里温度", "磁性相变"),
    "Grain growth": ("晶粒长大", "晶界迁移"),
    "Recrystallization (metallurgy)": ("再结晶", "无应变晶粒"),
    "Ostwald ripening": ("奥斯瓦尔德熟化", "粗化", "大颗粒长大"),
    "Kirkendall effect": ("柯肯达尔效应", "不等扩散", "标记面移动"),
    "Arrhenius equation": ("阿伦尼乌斯方程", "激活能", "温度依赖"),
    "Grain boundary strengthening": ("细晶强化", "Hall-Petch", "晶粒尺寸"),
    "Bravais lattice": ("布拉菲格子", "14种格子"),
    "Miller index": ("米勒指数", "晶面指数", "晶向指数"),
    "Space group": ("空间群", "晶体对称"),
    "Crystal system": ("晶系", "点群", "晶格常数"),
    "Cubic crystal system": ("立方晶系", "面心立方", "体心立方", "FCC", "BCC"),
    "Hexagonal crystal family": ("六方晶系", "六方密排", "HCP"),
    "Schottky defect": ("肖特基缺陷", "成对空位"),
    "Frenkel defect": ("弗伦克尔缺陷", "空位间隙对"),
    "Stacking fault": ("层错", "堆垛层错", "部分位错"),
    "Monte Carlo method": ("蒙特卡洛方法", "统计采样", "材料模拟"),
    "Interatomic potential": ("原子间势", "势函数", "力场", "MEAM势", "Stillinger-Weber势"),
    "Lennard-Jones potential": ("伦纳德琼斯势", "LJ势", "对势"),
    "ReaxFF": ("反应力场", "ReaxFF", "断键成键"),
    "Density of states": ("态密度", "DOS", "费米能级"),
    "X-ray diffraction": ("X射线衍射", "XRD", "物相鉴定"),
    "Bragg's law": ("布拉格定律", "衍射角", "晶面间距"),
    "Electron diffraction": ("电子衍射", "倒易点阵", "选区电子衍射"),
    "Energy-dispersive X-ray spectroscopy": ("能量色散X射线谱", "EDS", "EDX", "元素分析"),
    "Electron backscatter diffraction": ("电子背散射衍射", "EBSD", "晶粒取向"),
    "Differential scanning calorimetry": ("差示扫描量热", "DSC", "热转变"),
    "Atom probe": ("原子探针层析", "APT", "三维成分"),
    "Nanoindentation": ("纳米压痕", "硬度", "约化模量"),
    "Metallography": ("金相学", "金相制样", "抛光", "腐蚀"),
    "High-entropy alloy": ("高熵合金", "多主元合金"),
    "Shape-memory alloy": ("形状记忆合金", "超弹性", "马氏体相变"),
    "Amorphous metal": ("非晶态金属", "金属玻璃"),
    "Perovskite (structure)": ("钙铛矿结构", "ABX3", "氧化物"),
    "Corrosion": ("腐蚀", "电化学腐蚀", "氧化"),
    "Stainless steel": ("不锈钢", "铬", "钝化"),
    "Magnesium alloy": ("镁合金", "轻质合金", "HCP"),
    "List of copper alloys": ("铜合金", "黄铜", "青铜"),
}


class WikipediaTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_section = "Introduction"
        self._capture_tag = ""
        self._buffer: list[str] = []
        self._skip_depth = 0
        self.paragraphs: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"style", "script", "table", "figure", "nav", "sup", "math"}:
            self._skip_depth += 1
            return
        if self._skip_depth == 0 and tag in {"p", "h2", "h3"} and not self._capture_tag:
            self._capture_tag = tag
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script", "table", "figure", "nav", "sup", "math"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth or tag != self._capture_tag:
            return
        text = _clean_text(" ".join(self._buffer))
        if tag in {"h2", "h3"} and text:
            self.current_section = text
        elif tag == "p" and len(text) >= 80 and self.current_section.lower() not in SKIPPED_SECTIONS:
            self.paragraphs.append((self.current_section, text))
        self._capture_tag = ""
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and self._capture_tag:
            self._buffer.append(data)


def _clean_text(text: str) -> str:
    text = re.sub(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _page_metadata(html: str) -> dict[str, str]:
    def match(pattern: str) -> str:
        result = re.search(pattern, html, flags=re.IGNORECASE)
        return result.group(1) if result else ""

    return {
        "page_id": match(r'property="mw:pageId"\s+content="([^"]+)"'),
        "revision_id": match(r"Special:Redirect/revision/(\d+)"),
        "revision_sha1": match(r'property="mw:revisionSHA1"\s+content="([^"]+)"'),
        "modified_at": match(r'property="dc:modified"\s+content="([^"]+)"'),
        "canonical_title": match(r"<title>([^<]+)</title>"),
    }


def _chunk_paragraphs(paragraphs: list[tuple[str, str]], *, max_chars: int, max_chunks: int) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    section = "Introduction"
    buffer: list[str] = []
    size = 0
    for paragraph_section, paragraph in paragraphs:
        if buffer and size + len(paragraph) + 1 > max_chars:
            chunks.append((section, " ".join(buffer)))
            if len(chunks) >= max_chunks:
                break
            buffer = []
            size = 0
        if not buffer:
            section = paragraph_section
        remaining = max_chars - size
        if len(paragraph) > remaining and not buffer:
            paragraph = paragraph[:max_chars].rsplit(" ", 1)[0].rstrip() + "..."
        buffer.append(paragraph)
        size += len(paragraph) + 1
    if buffer and len(chunks) < max_chunks:
        chunks.append((section, " ".join(buffer)))
    return chunks


def _fetch_page(topic: Topic, *, timeout: float, retries: int) -> tuple[str, dict[str, str]]:
    url = API_TEMPLATE.format(title=parse.quote(topic.title.replace(" ", "_"), safe="()'"))
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            with request.urlopen(req, timeout=timeout) as response:
                html = response.read().decode("utf-8")
            return html, _page_metadata(html)
        except error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                break
            if attempt < retries:
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                wait_seconds = float(retry_after) if retry_after.isdigit() else 15.0 * (attempt + 1)
                time.sleep(max(1.5, wait_seconds))
        except (error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Wikipedia fetch failed for {topic.title}: {last_error}")


def build_documents(
    *,
    topics: tuple[Topic, ...] = TOPICS,
    max_chars: int = 1800,
    max_chunks_per_page: int = 2,
    timeout: float = 30.0,
    retries: int = 2,
    delay: float = 0.15,
) -> tuple[list[dict[str, object]], list[str]]:
    documents: list[dict[str, object]] = []
    failures: list[str] = []
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for topic in topics:
        try:
            html, metadata = _fetch_page(topic, timeout=timeout, retries=retries)
            parser = WikipediaTextParser()
            parser.feed(html)
            chunks = _chunk_paragraphs(parser.paragraphs, max_chars=max_chars, max_chunks=max_chunks_per_page)
            if not chunks:
                raise RuntimeError("no usable paragraphs")
            canonical_title = metadata.get("canonical_title") or topic.title
            page_slug = _slug(topic.title)
            encoded_title = parse.quote(topic.title.replace(" ", "_"), safe="()'")
            source_url = f"https://en.wikipedia.org/wiki/{encoded_title}"
            for index, (section, content) in enumerate(chunks, start=1):
                documents.append(
                    {
                        "id": f"wikipedia.en.{page_slug}.chunk{index}",
                        "domain": topic.domain,
                        "doc_type": "encyclopedia_chunk",
                        "title": f"Wikipedia: {canonical_title} - {section}",
                        "content": content,
                        "keywords": list(
                            dict.fromkeys([topic.title, section, *topic.keywords, *CHINESE_KEYWORDS.get(topic.title, ())])
                        ),
                        "materials": list(topic.materials),
                        "methods": list(topic.methods),
                        "tools": [],
                        "source": "Wikipedia (English)",
                        "source_url": source_url,
                        "trust_level": "medium",
                        "metadata": {
                            "language": "en",
                            "license": "CC BY-SA 4.0",
                            "attribution": "Wikipedia contributors",
                            "page_id": metadata.get("page_id", ""),
                            "revision_id": metadata.get("revision_id", ""),
                            "revision_sha1": metadata.get("revision_sha1", ""),
                            "page_modified_at": metadata.get("modified_at", ""),
                            "retrieved_at": retrieved_at,
                            "section": section,
                            "chunk_index": index,
                        },
                    }
                )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{topic.title}: {exc}")
        time.sleep(max(0.0, delay))
    return documents, failures


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a sourced materials RAG corpus from Wikimedia's official API.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--max-chunks-per-page", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.75)
    parser.add_argument("--resume", action="store_true", help="Keep existing pages and fetch only missing topics.")
    parser.add_argument("--allow-failures", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    existing_documents: list[dict[str, object]] = []
    existing_page_prefixes: set[str] = set()
    if args.resume and args.output.exists():
        for raw_line in args.output.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            document = json.loads(raw_line)
            existing_documents.append(document)
            document_id = str(document.get("id") or "")
            existing_page_prefixes.add(document_id.rsplit(".chunk", 1)[0])
        topics_by_prefix = {f"wikipedia.en.{_slug(topic.title)}": topic for topic in TOPICS}
        for document in existing_documents:
            document_id = str(document.get("id") or "")
            topic = topics_by_prefix.get(document_id.rsplit(".chunk", 1)[0])
            if topic is None:
                continue
            keywords = list(document.get("keywords") or [])
            document["keywords"] = list(
                dict.fromkeys([*keywords, *topic.keywords, *CHINESE_KEYWORDS.get(topic.title, ())])
            )
    topics = tuple(
        topic
        for topic in TOPICS
        if f"wikipedia.en.{_slug(topic.title)}" not in existing_page_prefixes
    )
    documents, failures = build_documents(
        topics=topics,
        max_chars=max(400, args.max_chars),
        max_chunks_per_page=max(1, args.max_chunks_per_page),
        timeout=max(5.0, args.timeout),
        retries=max(0, args.retries),
        delay=max(0.0, args.delay),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    merged_documents = existing_documents + documents
    args.output.write_text(
        "\n".join(json.dumps(document, ensure_ascii=False, separators=(",", ":")) for document in merged_documents) + "\n",
        encoding="utf-8",
    )
    report = {
        "output": str(args.output),
        "topics_requested": len(TOPICS),
        "topics_fetched_this_run": len(topics),
        "existing_documents": len(existing_documents),
        "new_documents": len(documents),
        "documents_written": len(merged_documents),
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures and not args.allow_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
