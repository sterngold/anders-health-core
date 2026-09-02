INSERT INTO analysis_policy(policy_id,policy_version,domain,parameters_json,active_from)
VALUES
('food-trend','food-v1','nutrition','{"short_window_days":7,"provisional_min_days":3,"normal_min_days":6,"historical_window_days":30,"historical_min_weeks":3,"current_gap_limit_days":7}','2026-01-01'),
('sleep-trend','sleep-v1','sleep','{"window_days":7,"baseline_days":30}','2026-01-01'),
('weight-trend','weight-v1','weight','{"window_days":7,"body_composition_cadence_days":30}','2026-01-01'),
('training-cadence','training-v1','training','{"cadence_days":7,"targets":"runtime_configuration"}','2026-01-01'),
('food-sleep','association-v1','association','{"minimum_pairs":7,"lag_days":1}','2026-01-01'),
('food-weight','association-v1','association','{"minimum_food_days":7,"minimum_weight_measurements":3}','2026-01-01'),
('food-body-composition','association-v1','association','{"minimum_food_days":14,"minimum_weeks":3,"maximum_gap_days":7}','2026-01-01'),
('history-windows','history-v1','shared','{"supported_days":[7,30,60,90],"default_history_days":60,"default_90_days":false}','2026-01-01');
