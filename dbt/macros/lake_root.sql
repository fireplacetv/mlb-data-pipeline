{% macro lake_root() %}
    {#- Where dlt wrote the lake (src/mlb/config.py resolve_lake): the R2 bucket in S3_BUCKET_URL,
        https://<account id>.r2.cloudflarestorage.com/<bucket>[/<folder>] -> s3://<bucket>[/<folder>],
        or <MLB_DATA_DIR>/lake when it's empty. The source YAML inlines the same expression. -#}
    {{- 's3://' ~ env_var('S3_BUCKET_URL').split('/', 3)[3].strip('/') if env_var('S3_BUCKET_URL', '') | trim else env_var('MLB_DATA_DIR', '../data') ~ '/lake' -}}
{% endmacro %}
