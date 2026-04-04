from app.thermo.engine import build_calculated_phase_diagram_report

report = build_calculated_phase_diagram_report(
    system_name='Pb-Sn',
    temperature_min=300.0,
    temperature_max=1800.0,
    pressure=101325.0,
    step_size=50.0,
    notes='stub llm codegen',
    output_path='result.html',
)

print(f"system={report['system_name']}")
print(f"database={report['database_name']}")
print(f"output={report['output_path']}")