from app.thermo.engine import build_calculated_phase_diagram_report

report = build_calculated_phase_diagram_report(
    system_name="Al-Ni",
    temperature_min=300.0,
    temperature_max=2000.0,
    pressure=101325.0,
    step_size=20.0,
    notes="calculated from tdb file, limited to stable phases in Al-Ni system, experimental verification recommended",
    output_path="result.html",
)

print(f"system={report['system_name']}")
print(f"family={report['family']}")
print(f"method={report['method']}")
print(f"database={report['database_name']}")
print(f"output={report['output_path']}")