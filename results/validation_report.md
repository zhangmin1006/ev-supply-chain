# EV Supply-Chain Model Validation Report

Fresh simulations run: 10 scenarios, 260 weeks, seed 42.

## Summary

- PASS: 130
- WARN: 0
- FAIL: 0

## Scenario Metrics

| scenario                 | mean_oem_production_k_wk | min_oem_production_k_wk | peak_loss_pct_vs_baseline_mean | cumulative_loss_k_veh_vs_baseline | recovery_week_below_90pct | max_total_backlog_k | max_price_signal | min_harness_stock_wk | min_cobalt_stock_wk | min_graphite_stock_wk | min_ree_stock_wk | min_sic_stock_wk |
| ------------------------ | ------------------------ | ----------------------- | ------------------------------ | --------------------------------- | ------------------------- | ------------------- | ---------------- | -------------------- | ------------------- | --------------------- | ---------------- | ---------------- |
| baseline                 | 8.829                    | 3.554                   | 59.746                         | 0.0                               | 130                       | 124.745             | 1.318            | 1.988                | 6.976               | 4.976                 | 8.986            | 12.986           |
| china_catl_disruption    | 8.829                    | 3.554                   | 59.746                         | 0.0                               | 130                       | 124.745             | 1.318            | 1.988                | 6.976               | 4.976                 | 8.986            | 12.986           |
| china_graphite           | 8.829                    | 3.554                   | 59.746                         | 0.0                               | 130                       | 124.745             | 1.318            | 1.988                | 6.976               | 4.976                 | 8.986            | 12.986           |
| china_ree_restriction    | 8.829                    | 3.554                   | 59.746                         | 0.0                               | 130                       | 124.745             | 1.318            | 1.988                | 6.976               | 4.976                 | 8.986            | 12.986           |
| compound_shock           | 8.829                    | 3.554                   | 59.746                         | 0.0                               | 130                       | 124.745             | 1.318            | 1.988                | 6.976               | 4.976                 | 8.986            | 12.986           |
| drc_cobalt               | 8.829                    | 3.554                   | 59.746                         | 0.0                               | 130                       | 124.745             | 1.318            | 1.988                | 6.976               | 4.976                 | 8.986            | 12.986           |
| sic_bottleneck           | 8.829                    | 3.554                   | 59.746                         | 0.0                               | 130                       | 124.745             | 1.318            | 1.988                | 6.976               | 4.976                 | 8.986            | 12.986           |
| uk_supply_chain_friction | 8.683                    | 3.352                   | 62.035                         | 37.835                            | 130                       | 139.091             | 1.299            | 2.001                | 6.976               | 4.976                 | 8.986            | 12.986           |
| ukraine_harness          | 8.829                    | 3.554                   | 59.746                         | 0.0                               | 130                       | 124.745             | 1.318            | 1.988                | 6.976               | 4.976                 | 8.986            | 12.986           |
| us_china_tariff          | 8.829                    | 3.554                   | 59.746                         | 0.0                               | 130                       | 124.745             | 1.318            | 1.988                | 6.976               | 4.976                 | 8.986            | 12.986           |

## Warnings And Failures

No warnings or failures.

## Files

- Checks: `results\validation_checks.csv`
- Scenario metrics: `results\validation_scenario_metrics.csv`