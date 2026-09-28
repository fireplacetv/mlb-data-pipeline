"""dlt helpers shared by the pipelines: lake destination, watermark, and run logging."""

import logging
from datetime import date
from pathlib import Path

import dlt

logger = logging.getLogger(__name__)

WATERMARK_KEY = "loaded_through"


def build_pipeline(
    pipeline_name: str, dataset_name: str, data_dir: Path, schema_dir: Path
) -> dlt.Pipeline:
    """Create a dlt pipeline writing Parquet under data_dir/lake (§6.2)."""
    return dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=dlt.destinations.filesystem(bucket_url=str(data_dir / "lake")),
        dataset_name=dataset_name,
        pipelines_dir=str(data_dir / "dlt_pipelines"),
        export_schema_path=str(schema_dir),
    )


def source_state(pipeline: dlt.Pipeline, source_name: str) -> dict:
    """Return the pipeline's local state for one source (empty if none yet)."""
    return pipeline.state.get("sources", {}).get(source_name, {})


def stored_watermark(pipeline: dlt.Pipeline, source_name: str) -> date | None:
    """Return the watermark from the pipeline's local state."""
    value = source_state(pipeline, source_name).get(WATERMARK_KEY)
    return date.fromisoformat(value) if value else None


def restore_watermark(pipeline: dlt.Pipeline, source_name: str) -> date | None:
    """Sync state from the lake (restores a deleted pipelines dir), then read the watermark."""
    pipeline.sync_destination()
    return stored_watermark(pipeline, source_name)


def table_columns(pipeline: dlt.Pipeline) -> dict[str, set[str]]:
    """Map each data table in the pipeline's schema to its column names."""
    if not pipeline.default_schema_name:
        return {}
    tables = pipeline.default_schema.data_tables()
    return {t["name"]: set(t.get("columns", {})) for t in tables}


def log_schema_changes(before: dict[str, set[str]], after: dict[str, set[str]]) -> None:
    """Log new tables, new columns, and new variant columns between two snapshots."""
    for table, columns in sorted(after.items()):
        if table not in before:
            logger.info("Schema change: new table %s (%d columns)", table, len(columns))
            continue
        added = sorted(columns - before[table])
        variants = [c for c in added if "__v_" in c]
        if added:
            logger.info("Schema change: %s new columns: %s", table, ", ".join(added))
        if variants:
            logger.warning(
                "Schema change: %s variant columns (type drift): %s", table, ", ".join(variants)
            )


def last_row_counts(pipeline: dlt.Pipeline) -> dict[str, int]:
    """Rows per data table normalized in the pipeline's last run (dlt tables excluded)."""
    info = pipeline.last_trace.last_normalize_info
    counts = info.row_counts if info else {}
    return {table: n for table, n in counts.items() if not table.startswith("_dlt")}


def log_row_counts(counts: dict[str, int]) -> None:
    """Log rows loaded per table."""
    for table, count in sorted(counts.items()):
        logger.info("Rows loaded: %s = %d", table, count)


def log_step_durations(pipeline: dlt.Pipeline, label: str = "") -> None:
    """Log the duration of each dlt step in the pipeline's last run."""
    prefix = f"{label}: " if label else ""
    for step in pipeline.last_trace.steps:
        seconds = (step.finished_at - step.started_at).total_seconds()
        logger.info("%sStep %s took %.1fs", prefix, step.step, seconds)
