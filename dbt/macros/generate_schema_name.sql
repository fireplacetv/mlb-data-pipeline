{% macro generate_schema_name(custom_schema_name, node) -%}
    {#-
        Check the output mode, defaulting to 'dev' if DBT_OUTPUT_MODE is
        empty/not set
    -#}
    {%- set output_mode = env_var('DBT_OUTPUT_MODE', 'dev') -%}
    {%- set base_schema = custom_schema_name if custom_schema_name is not none else target.schema -%}

    {%- if output_mode == 'production' -%}
        {#- Production mode: use the custom schema directly (e.g., 'staging', 'marts') -#}
        {{ base_schema }}
    {%- else -%}
        {#- Dev mode: prefix with a sanitized per-developer identifier -#}
        {%- set current_user = dbt_utils.slugify(env_var('USER', 'dev')) -%}
        dbt_{{ current_user }}_{{ base_schema }}
    {%- endif -%}
{%- endmacro %}
