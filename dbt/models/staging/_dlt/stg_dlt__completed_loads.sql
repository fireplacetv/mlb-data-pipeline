-- One row per completed dlt load, read from the marker files dlt writes into
-- <lake>/<dataset>/_dlt_loads/<schema>__<load_id>.jsonl when a load finishes (§6.2).
with marker_files as (
    select file
    -- duckdb: glob() lists files (locally, or in R2 through httpfs) and returns no rows
    -- (not an error) when none match
    from glob('{{ lake_root() }}/*/_dlt_loads/*')
),

parsed as (
    select
        -- duckdb: regexp_extract; the dataset is the folder above _dlt_loads
        regexp_extract(file, '([^/]+)/_dlt_loads/[^/]+$', 1) as dataset,
        -- duckdb: regexp_extract; <schema>__<load_id>.<ext>, schema names may contain "__"
        regexp_extract(file, '/_dlt_loads/(.+)__[0-9]+\.[0-9]+\.[^/]+$', 1) as schema_name,
        -- duckdb: regexp_extract; load ids are unix timestamps with a fraction
        regexp_extract(file, '__([0-9]+\.[0-9]+)\.[^/]+$', 1) as load_id
    from marker_files
)

select
    cast(dataset as varchar)                                    as dataset,
    cast(schema_name as varchar)                                as schema_name,
    cast(load_id as varchar)                                    as load_id,
    to_timestamp(cast(load_id as double precision))             as loaded_at
from parsed
