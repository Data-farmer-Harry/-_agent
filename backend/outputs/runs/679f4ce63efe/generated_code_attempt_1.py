from app.thermo.engine import build_calculated_phase_diagram_report

report = build_calculated_phase_diagram_report(
    system_name="Al-Zn",
    temperature_min=300.0,
    temperature_max=1000.0,
    pressure=101325.0,
    step_size=10.0,
    notes="关注液相线和FCC_A1区域，温度范围调整至合理区间，步长细化以提高精度",
    output_path="result.html",
)

print(f"system={report['system_name']}")
print(f"family={report['family']}")
print(f"method={report['method']}")
print(f"database={report['database_name']}")
print(f"output={report['output_path']}")