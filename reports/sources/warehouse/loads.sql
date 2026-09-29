select dataset, schema_name, load_id, loaded_at
from staging.stg_dlt__completed_loads
order by loaded_at
