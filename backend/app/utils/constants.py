DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_FRONTEND_HOST = "127.0.0.1"
DEFAULT_FRONTEND_PORT = 5174

CONVERSATION_ROUTE = "conversation.answer"
PHASE_DIAGRAM_ROUTE = "phase_diagram.generate"

TOOL_NAME_CHAT = "chat_responder"
TOOL_NAME_LOOKUP = "thermo_database_lookup"
TOOL_NAME_CODEGEN = "phase_diagram_codegen"
TOOL_NAME_EXECUTE = "python_execute"
TOOL_NAME_REVIEW = "phase_diagram_result_review"

COMMON_BINARY_SYSTEM_HINTS = {
    "alcu": (
        "Al-Cu is not an isomorphous system. Keep the Al-rich side near pure Al around 933 K, the Cu-rich side near pure Cu around 1358 K, "
        "and include an eutectic-like feature plus a theta/CuAl2-style intermetallic region instead of drawing a generic lens."
    ),
    "cual": (
        "Al-Cu is not an isomorphous system. Keep the Al-rich side near pure Al around 933 K, the Cu-rich side near pure Cu around 1358 K, "
        "and include an eutectic-like feature plus a theta/CuAl2-style intermetallic region instead of drawing a generic lens."
    ),
    "cuni": (
        "Cu-Ni is a classic isomorphous binary with complete solid solubility. Use pure Cu near 1358 K on the Cu end, pure Ni near 1728 K on the Ni end, "
        "and do not invent eutectic points, miscibility gaps, or intermetallic compounds."
    ),
    "nicu": (
        "Cu-Ni is a classic isomorphous binary with complete solid solubility. Use pure Cu near 1358 K on the Cu end, pure Ni near 1728 K on the Ni end, "
        "and do not invent eutectic points, miscibility gaps, or intermetallic compounds."
    ),
    "fecu": (
        "Fe-Cu should be treated as a limited-solid-solubility binary rather than a steel diagram. Use pure Fe near 1811 K on the Fe end and pure Cu near 1358 K on the Cu end. "
        "Do not use A3, Acm, carbide, pearlite, or Al-Cu terminology."
    ),
    "cufe": (
        "Fe-Cu should be treated as a limited-solid-solubility binary rather than a steel diagram. Use pure Fe near 1811 K on the Fe end and pure Cu near 1358 K on the Cu end. "
        "Do not use A3, Acm, carbide, pearlite, or Al-Cu terminology."
    ),
    "pbsn": (
        "Pb-Sn is a eutectic binary. Keep pure Pb near 600.6 K, pure Sn near 505 K, and include a eutectic feature near 456 K rather than a smooth complete-solid-solubility lens."
    ),
    "snpb": (
        "Pb-Sn is a eutectic binary. Keep pure Pb near 600.6 K, pure Sn near 505 K, and include a eutectic feature near 456 K rather than a smooth complete-solid-solubility lens."
    ),
    "tial": (
        "Ti-Al should not collapse into a generic binary lens. Keep pure Ti near 1941 K, pure Al near 933 K, "
        "show an intermetallic-rich topology, and explicitly encode labeled intermediate features near about 25 at.% Al (Ti3Al), 50 at.% Al (TiAl), "
        "and the Al-rich side near about 75 at.% Al (Al3Ti/TiAl3-like region) instead of claiming complete solid solubility. "
        "Do not draw these three intermetallic regions as simple axis-aligned rectangles; at least part of their upper or lower boundaries should slope or curve with composition/temperature."
    ),
    "alti": (
        "Ti-Al should not collapse into a generic binary lens. Keep pure Ti near 1941 K, pure Al near 933 K, "
        "show an intermetallic-rich topology, and explicitly encode labeled intermediate features near about 25 at.% Al (Ti3Al), 50 at.% Al (TiAl), "
        "and the Al-rich side near about 75 at.% Al (Al3Ti/TiAl3-like region) instead of claiming complete solid solubility. "
        "Do not draw these three intermetallic regions as simple axis-aligned rectangles; at least part of their upper or lower boundaries should slope or curve with composition/temperature."
    ),
}


def normalize_system_key(system_name: str) -> str:
    return "".join(character for character in system_name.lower() if character.isalpha())
