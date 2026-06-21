from __future__ import annotations

from app.core.artifacts import ArtifactService
from app.recognition_reconstruction.service import RecognitionReconstructionService
from app.recognition_simulator.models import (
    RecognitionSimulationBundle,
    RecognitionSimulatorControlSpec,
    RecognitionSimulationReport,
)
from app.state import ArtifactRef, RecognitionResult, ResultProfile
from app.utils.path_utils import write_json_file, write_text_file


class RecognitionSimulationService:
    def __init__(self, artifact_service: ArtifactService) -> None:
        self.artifact_service = artifact_service
        self.reconstruction_service = RecognitionReconstructionService()

    @staticmethod
    def _result_profile(report: RecognitionSimulationReport, recognition_result: RecognitionResult) -> ResultProfile:
        confidence = recognition_result.confidence if recognition_result.confidence > 0 else None
        trust_level = "medium" if (confidence or 0) >= 0.6 else "low"
        return ResultProfile(
            category="Recognized Simulation",
            source_label="recognized phase-diagram image",
            mode_label="interactive simulator",
            trust_level=trust_level,
            confidence=confidence,
            trust_statement=(
                "This reconstructed panel rebuilds the uploaded phase-diagram view into HTML from recognized facts and "
                "deterministic image processing. The LLM only extracts structured diagram facts."
            ),
            assumptions=[
                "Unrecognized boundaries are interpolated from the validated schema rather than solved thermodynamically.",
                "Pressure acts as a qualitative perturbation factor instead of an experimentally fitted state variable.",
            ],
            warnings=report.warnings,
            evidence=[
                f"Recognized system: {report.system}",
                f"Recognized phases: {', '.join(report.phases[:6])}",
                f"Recognized critical points: {len(report.critical_points)}",
                f"Overlay confidence: {report.reconstruction_schema.get('overlay_confidence', report.reconstruction_schema.get('confidence'))}",
                "Rendering pipeline: schema -> validator -> curve fitting -> renderer",
                "Final render: self-generated HTML/canvas vector reconstruction (no embedded source image)",
            ],
        )

    def build_bundle(
        self,
        *,
        run_id: str,
        recognition_result: RecognitionResult,
        request_message: str,
        source_image_data_url: str | None = None,
        source_image_name: str = "",
    ) -> RecognitionSimulationBundle:
        schema = self.reconstruction_service.build_schema(
            recognition_result,
            request_message=request_message,
            source_image_data_url=source_image_data_url,
        )
        geometry = self.reconstruction_service.fit_geometry_from_image(
            schema,
            source_image_data_url=source_image_data_url,
        )
        report = RecognitionSimulationReport(
            system=schema.system,
            diagram_type=schema.diagram_type,
            x_axis=schema.x_axis,
            y_axis=schema.y_axis,
            phases=schema.phases,
            critical_points=schema.critical_points,
            controls=RecognitionSimulatorControlSpec(
                temperature_min=schema.controls.temperature_min,
                temperature_max=schema.controls.temperature_max,
                temperature_default=schema.controls.temperature_default,
                pressure_min=schema.controls.pressure_min,
                pressure_max=schema.controls.pressure_max,
                pressure_default=schema.controls.pressure_default,
            ),
            warnings=schema.warnings,
            notes=schema.notes,
            raw_summary=schema.raw_summary,
            request_message=schema.request_message,
            reconstruction_schema=schema.model_dump(mode="json"),
            geometry_model=geometry.model_dump(mode="json"),
        )
        result_profile = self._result_profile(report, recognition_result)
        simulation_render_mode = (
            "generated_canvas_vector_reconstruction" if source_image_data_url else "generated_canvas_schema_reconstruction"
        )
        html_content = self.reconstruction_service.render_html(
            schema,
            geometry,
            result_profile,
            source_image_data_url=source_image_data_url,
            source_image_name=source_image_name,
        )
        result_path = self.artifact_service.get_result_path(run_id)
        write_text_file(result_path, html_content)
        json_path = self.artifact_service.get_artifact_path(run_id, "recognition_simulator.json")
        write_json_file(
            json_path,
            {
                "report": report.model_dump(mode="json"),
                "result_profile": result_profile.model_dump(mode="json"),
            },
        )
        artifacts = [
            self.artifact_service.build_artifact_ref(
                "html",
                "result.html",
                result_path,
                url=self.artifact_service.build_artifact_url(run_id, "result.html"),
                metadata={
                    "source": "RecognitionAgent",
                    "mode": "interactive_recognition_simulator",
                    "simulation_render_mode": simulation_render_mode,
                },
            ),
            self.artifact_service.build_artifact_ref(
                "json",
                "recognition_simulator.json",
                json_path,
                url=self.artifact_service.build_artifact_url(run_id, "recognition_simulator.json"),
                metadata={"source": "RecognitionAgent", "mode": "interactive_recognition_simulator"},
            ),
        ]
        return RecognitionSimulationBundle(
            html_content=html_content,
            html_path=str(result_path),
            artifacts=artifacts,
            summary={
                "recognized_system": report.system,
                "recognized_phases": report.phases,
                "recognized_critical_points": [point.model_dump(mode="json") for point in report.critical_points],
                "simulator_controls": report.controls.model_dump(mode="json"),
                "plot_region": schema.plot_region.model_dump(mode="json"),
                "overlay_confidence": schema.overlay_confidence,
                "source_image_present": bool(source_image_data_url),
                "source_image_name": source_image_name,
                "simulation_render_mode": simulation_render_mode,
                "reconstruction_schema": report.reconstruction_schema,
                "geometry_model": report.geometry_model,
                "result_profile": result_profile.model_dump(mode="json"),
            },
            metadata={
                "simulation_mode": "interactive_recognition_simulator",
                "simulation_render_mode": simulation_render_mode,
                "source_image_present": bool(source_image_data_url),
                "source_image_name": source_image_name,
                "simulation_warnings": report.warnings,
                "recognition_simulator": {
                    "system": report.system,
                    "slider_axes": ["temperature", "pressure_factor"],
                    "critical_point_count": len(report.critical_points),
                    "renderer_pipeline": [
                        "recognition_schema",
                        "recognition_validator",
                        "recognition_curve_fit",
                        "recognition_html_renderer",
                    ],
                },
            },
            result_profile=result_profile,
            report=report,
        )
