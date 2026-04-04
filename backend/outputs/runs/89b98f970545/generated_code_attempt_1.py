from app.thermo.engine import build_calculated_phase_diagram_report

report = build_calculated_phase_diagram_report(
    system_name="Al-Zn",
    temperature_min=300.0,
    temperature_max=1000.0,
    pressure=101325.0,
    step_size=10.0,
    notes="Focus on liquidus line and major solid phases FCC_A1 and HCP_A3",
    output_path="result.html",
)

print(f"system={report['system_name']}")
print(f"family={report['family']}")
print(f"method={report['method']}")
print(f"database={report['database_name']}")
print(f"output={report['output_path']}")