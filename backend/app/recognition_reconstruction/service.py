from __future__ import annotations

from app.recognition_reconstruction.curve_fit import fit_reconstruction_geometry
from app.recognition_reconstruction.renderer import render_reconstruction_html
from app.recognition_reconstruction.schema import ReconstructionGeometry, ReconstructionSchema
from app.recognition_reconstruction.validator import build_reconstruction_schema
from app.state import RecognitionResult, ResultProfile


class RecognitionReconstructionService:
    def build_schema(
        self,
        recognition_result: RecognitionResult,
        *,
        request_message: str,
        source_image_data_url: str | None = None,
    ) -> ReconstructionSchema:
        return build_reconstruction_schema(
            recognition_result,
            request_message=request_message,
            source_image_data_url=source_image_data_url,
        )

    def fit_geometry(self, schema: ReconstructionSchema) -> ReconstructionGeometry:
        return fit_reconstruction_geometry(schema)

    def fit_geometry_from_image(
        self,
        schema: ReconstructionSchema,
        *,
        source_image_data_url: str | None = None,
    ) -> ReconstructionGeometry:
        return fit_reconstruction_geometry(schema, source_image_data_url=source_image_data_url)

    def render_html(
        self,
        schema: ReconstructionSchema,
        geometry: ReconstructionGeometry,
        result_profile: ResultProfile,
        *,
        source_image_data_url: str | None = None,
        source_image_name: str = "",
    ) -> str:
        return render_reconstruction_html(
            schema,
            geometry,
            result_profile,
            source_image_data_url=source_image_data_url,
            source_image_name=source_image_name,
        )
