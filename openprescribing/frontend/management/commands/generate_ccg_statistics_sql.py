"""A command to generate the SQL required to aggregate statistics
stashed in the JSON column for star_pus in the practice_statistics
table.

When the keys in the JSON change, replace
`views_sql/ccgstatistics.sql` with the output of running this command

"""


keys = ['analgesics_cost',
        'antidepressants_adq',
        'antidepressants_cost',
        'antiepileptic_drugs_cost',
        'antiplatelet_drugs_cost',
        'benzodiazepine_caps_and_tabs_cost',
        'bisphosphonates_and_other_drugs_cost',
        'bronchodilators_cost',
        'calcium-channel_blockers_cost',
        'cox-2_inhibitors_cost',
        'drugs_acting_on_benzodiazepine_receptors_cost',
        'drugs_affecting_the_renin_angiotensin_system_cost',
        'drugs_for_dementia_cost',
        'drugs_used_in_parkinsonism_and_related_disorders_cost',
        'hypnotics_adq',
        'inhaled_corticosteroids_cost',
        'laxatives_cost',
        'lipid-regulating_drugs_cost',
        'omega-3_fatty_acid_compounds_adq',
        'oral_antibacterials_cost',
        'oral_antibacterials_item',
        'oral_nsaids_cost',
        'proton_pump_inhibitors_cost',
        'statins_cost',
        'ulcer_healing_drugs_cost']

keys_with_hyphens = {}
for orig in keys:
    if '-' in orig:
        keys_with_hyphens[orig.replace('-', '_')] = orig
sql = """
-- This SQL is generated by `generate_ccg_statistics_sql.py`

CREATE TEMPORARY FUNCTION
  jsonify_starpu(%s)
  RETURNS STRING
  LANGUAGE js AS '''
  var obj = {};
  %s
  return JSON.stringify(obj);
  ''';
SELECT
  month AS date,
  pct_id,
  ccgs.name AS name,
  SUM(total_list_size) AS total_list_size,
  SUM(astro_pu_items) AS astro_pu_items,
  SUM(astro_pu_cost) AS astro_pu_cost,
  jsonify_starpu(%s) AS star_pu
FROM
  {hscic}.practice_statistics AS statistics
JOIN {hscic}.ccgs ccgs
ON (statistics.pct_id = ccgs.code AND ccgs.org_type = 'CCG')
WHERE month >= TIMESTAMP(DATE_SUB(DATE "{{this_month}}", INTERVAL 5 YEAR))
GROUP BY
  month,
  pct_id,
  name
"""

temporary_function_sig = []
temporary_function_obj = []
select_starpu = []
for key in keys:
    safe_key = key.replace('-', '_')
    temporary_function_sig.append("%s FLOAT64" % safe_key)
    temporary_function_obj.append("obj['%s'] = %s" % (
        key, safe_key))
    select_starpu.append("SUM(CAST(JSON_EXTRACT_SCALAR(star_pu,"
                         "'$.%s') AS FLOAT64))" % (
                             key))
print sql % (",".join(temporary_function_sig),
             ";".join(temporary_function_obj),
             ",".join(select_starpu))
