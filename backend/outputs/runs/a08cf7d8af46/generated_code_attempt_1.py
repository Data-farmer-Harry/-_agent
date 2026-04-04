from app.thermo.engine import build_calculated_phase_diagram_report

report = build_calculated_phase_diagram_report(
    system_name="Fe-Co",
    temperature_min=300.0,
    temperature_max=2300.0,
    pressure=101325.0,
    step_size=50.0,
    notes="verification family=tdb_calculated_binary database=mc_fecocrnbti.tdb",
    output_path="result.html",
)

print(f"system={report['system_name']}")
print(f"family={report['family']}")
print(f"method={report['method']}")
print(f"database={report['database_name']}")
print(f"output={report['output_path']}")