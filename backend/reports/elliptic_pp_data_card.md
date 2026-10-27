# Elliptic++ data card

Generated 2026-10-26 13:07 UTC from `data\raw\elliptic_pp` by training/inspect_elliptic.py.

Source: Elmougy and Liu (2023), KDD '23, https://github.com/git-disl/EllipticPlusPlus.
Licence: none stated in the repository. Confirm permitted use with your supervisor; do not commit the CSV files.
Label meaning: class 1 illicit, 2 licit, 3 unknown. Unknown is not licit and must not be used as a negative.

## Wallets (addresses)

- Feature rows: 1,268,260; distinct IDs: 822,942; feature columns: 55
- Time steps: 1 to 49
- Labels: 14,266 illicit, 251,088 licit, 557,588 unknown
- Illicit share among known labels: 5.4%
- Columns with missing values: 0
- Constant columns (no information): 0
- Feature rows with no label row: 0

### Known labels per time step

| Time step | Illicit | Licit |
| --- | --- | --- |
| 1 | 96 | 24,547 |
| 2 | 99 | 16,292 |
| 3 | 58 | 4,835 |
| 4 | 266 | 8,647 |
| 5 | 54 | 10,465 |
| 6 | 17 | 1,957 |
| 7 | 463 | 7,902 |
| 8 | 267 | 16,229 |
| 9 | 960 | 3,894 |
| 10 | 120 | 8,454 |
| 11 | 494 | 10,576 |
| 12 | 77 | 2,875 |
| 13 | 918 | 3,288 |
| 14 | 156 | 1,941 |
| 15 | 447 | 6,671 |
| 16 | 427 | 2,315 |
| 17 | 321 | 4,039 |
| 18 | 189 | 1,643 |
| 19 | 320 | 3,220 |
| 20 | 950 | 6,315 |
| 21 | 1,256 | 16,576 |
| 22 | 1,491 | 9,693 |
| 23 | 734 | 7,301 |
| 24 | 1,083 | 3,518 |
| 25 | 2,243 | 8,314 |
| 26 | 1,334 | 2,360 |
| 27 | 89 | 1,000 |
| 28 | 219 | 920 |
| 29 | 979 | 3,811 |
| 30 | 287 | 2,931 |
| 31 | 312 | 5,119 |
| 32 | 1,730 | 4,873 |
| 33 | 561 | 7,675 |
| 34 | 908 | 5,546 |
| 35 | 943 | 6,229 |
| 36 | 1,226 | 11,130 |
| 37 | 804 | 3,455 |
| 38 | 400 | 4,039 |
| 39 | 282 | 4,881 |
| 40 | 801 | 10,233 |
| 41 | 454 | 7,033 |
| 42 | 967 | 11,142 |
| 43 | 156 | 7,302 |
| 44 | 497 | 10,631 |
| 45 | 42 | 11,057 |
| 46 | 626 | 4,723 |
| 47 | 256 | 8,106 |
| 48 | 433 | 8,783 |
| 49 | 789 | 4,385 |

- IDs appearing both in steps 1-34 and later steps: 10,471 (leakage risk: split by ID as well as by time)

### Can each feature be rebuilt from live mempool.space data?

| Feature | Live compatibility |
| --- | --- |
| num_txs_as_sender | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| num_txs_as receiver | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| first_block_appeared_in | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| last_block_appeared_in | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| lifetime_in_blocks | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| total_txs | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| first_sent_block | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| first_received_block | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| num_timesteps_appeared_in | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_transacted_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_transacted_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_transacted_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_transacted_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_transacted_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_sent_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_sent_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_sent_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_sent_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_sent_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_received_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_received_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_received_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_received_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| btc_received_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_as_share_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_as_share_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_as_share_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_as_share_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees_as_share_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_txs_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_txs_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_txs_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_txs_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_txs_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_input_txs_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_input_txs_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_input_txs_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_input_txs_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_input_txs_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_output_txs_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_output_txs_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_output_txs_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_output_txs_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| blocks_btwn_output_txs_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| num_addr_transacted_multiple | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| transacted_w_address_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| transacted_w_address_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| transacted_w_address_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| transacted_w_address_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| transacted_w_address_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |

## Transactions

- Feature rows: 203,769; distinct IDs: 203,769; feature columns: 182
- Time steps: 1 to 49
- Labels: 4,545 illicit, 42,019 licit, 157,205 unknown
- Illicit share among known labels: 9.8%
- Columns with missing values: 17 (worst: in_txs_degree, 965 rows)
- Constant columns (no information): 0
- Feature rows with no label row: 0

### Known labels per time step

| Time step | Illicit | Licit |
| --- | --- | --- |
| 1 | 17 | 2,130 |
| 2 | 18 | 1,099 |
| 3 | 11 | 1,268 |
| 4 | 30 | 1,410 |
| 5 | 8 | 1,874 |
| 6 | 5 | 480 |
| 7 | 102 | 1,101 |
| 8 | 67 | 1,098 |
| 9 | 248 | 530 |
| 10 | 18 | 954 |
| 11 | 131 | 565 |
| 12 | 16 | 490 |
| 13 | 291 | 518 |
| 14 | 43 | 374 |
| 15 | 147 | 471 |
| 16 | 128 | 402 |
| 17 | 99 | 712 |
| 18 | 52 | 337 |
| 19 | 80 | 665 |
| 20 | 260 | 640 |
| 21 | 100 | 541 |
| 22 | 158 | 1,605 |
| 23 | 53 | 1,134 |
| 24 | 137 | 989 |
| 25 | 118 | 476 |
| 26 | 96 | 421 |
| 27 | 24 | 182 |
| 28 | 85 | 199 |
| 29 | 329 | 845 |
| 30 | 83 | 441 |
| 31 | 106 | 604 |
| 32 | 342 | 981 |
| 33 | 23 | 418 |
| 34 | 37 | 478 |
| 35 | 182 | 1,159 |
| 36 | 33 | 1,675 |
| 37 | 40 | 458 |
| 38 | 111 | 645 |
| 39 | 81 | 1,102 |
| 40 | 112 | 1,099 |
| 41 | 116 | 1,016 |
| 42 | 239 | 1,915 |
| 43 | 24 | 1,346 |
| 44 | 24 | 1,567 |
| 45 | 5 | 1,216 |
| 46 | 2 | 710 |
| 47 | 22 | 824 |
| 48 | 36 | 435 |
| 49 | 56 | 420 |

- IDs appearing both in steps 1-34 and later steps: 0

### Can each feature be rebuilt from live mempool.space data?

| Feature | Live compatibility |
| --- | --- |
| Local_feature_1 | No: anonymised Elliptic feature |
| Local_feature_2 | No: anonymised Elliptic feature |
| Local_feature_3 | No: anonymised Elliptic feature |
| Local_feature_4 | No: anonymised Elliptic feature |
| Local_feature_5 | No: anonymised Elliptic feature |
| Local_feature_6 | No: anonymised Elliptic feature |
| Local_feature_7 | No: anonymised Elliptic feature |
| Local_feature_8 | No: anonymised Elliptic feature |
| Local_feature_9 | No: anonymised Elliptic feature |
| Local_feature_10 | No: anonymised Elliptic feature |
| Local_feature_11 | No: anonymised Elliptic feature |
| Local_feature_12 | No: anonymised Elliptic feature |
| Local_feature_13 | No: anonymised Elliptic feature |
| Local_feature_14 | No: anonymised Elliptic feature |
| Local_feature_15 | No: anonymised Elliptic feature |
| Local_feature_16 | No: anonymised Elliptic feature |
| Local_feature_17 | No: anonymised Elliptic feature |
| Local_feature_18 | No: anonymised Elliptic feature |
| Local_feature_19 | No: anonymised Elliptic feature |
| Local_feature_20 | No: anonymised Elliptic feature |
| Local_feature_21 | No: anonymised Elliptic feature |
| Local_feature_22 | No: anonymised Elliptic feature |
| Local_feature_23 | No: anonymised Elliptic feature |
| Local_feature_24 | No: anonymised Elliptic feature |
| Local_feature_25 | No: anonymised Elliptic feature |
| Local_feature_26 | No: anonymised Elliptic feature |
| Local_feature_27 | No: anonymised Elliptic feature |
| Local_feature_28 | No: anonymised Elliptic feature |
| Local_feature_29 | No: anonymised Elliptic feature |
| Local_feature_30 | No: anonymised Elliptic feature |
| Local_feature_31 | No: anonymised Elliptic feature |
| Local_feature_32 | No: anonymised Elliptic feature |
| Local_feature_33 | No: anonymised Elliptic feature |
| Local_feature_34 | No: anonymised Elliptic feature |
| Local_feature_35 | No: anonymised Elliptic feature |
| Local_feature_36 | No: anonymised Elliptic feature |
| Local_feature_37 | No: anonymised Elliptic feature |
| Local_feature_38 | No: anonymised Elliptic feature |
| Local_feature_39 | No: anonymised Elliptic feature |
| Local_feature_40 | No: anonymised Elliptic feature |
| Local_feature_41 | No: anonymised Elliptic feature |
| Local_feature_42 | No: anonymised Elliptic feature |
| Local_feature_43 | No: anonymised Elliptic feature |
| Local_feature_44 | No: anonymised Elliptic feature |
| Local_feature_45 | No: anonymised Elliptic feature |
| Local_feature_46 | No: anonymised Elliptic feature |
| Local_feature_47 | No: anonymised Elliptic feature |
| Local_feature_48 | No: anonymised Elliptic feature |
| Local_feature_49 | No: anonymised Elliptic feature |
| Local_feature_50 | No: anonymised Elliptic feature |
| Local_feature_51 | No: anonymised Elliptic feature |
| Local_feature_52 | No: anonymised Elliptic feature |
| Local_feature_53 | No: anonymised Elliptic feature |
| Local_feature_54 | No: anonymised Elliptic feature |
| Local_feature_55 | No: anonymised Elliptic feature |
| Local_feature_56 | No: anonymised Elliptic feature |
| Local_feature_57 | No: anonymised Elliptic feature |
| Local_feature_58 | No: anonymised Elliptic feature |
| Local_feature_59 | No: anonymised Elliptic feature |
| Local_feature_60 | No: anonymised Elliptic feature |
| Local_feature_61 | No: anonymised Elliptic feature |
| Local_feature_62 | No: anonymised Elliptic feature |
| Local_feature_63 | No: anonymised Elliptic feature |
| Local_feature_64 | No: anonymised Elliptic feature |
| Local_feature_65 | No: anonymised Elliptic feature |
| Local_feature_66 | No: anonymised Elliptic feature |
| Local_feature_67 | No: anonymised Elliptic feature |
| Local_feature_68 | No: anonymised Elliptic feature |
| Local_feature_69 | No: anonymised Elliptic feature |
| Local_feature_70 | No: anonymised Elliptic feature |
| Local_feature_71 | No: anonymised Elliptic feature |
| Local_feature_72 | No: anonymised Elliptic feature |
| Local_feature_73 | No: anonymised Elliptic feature |
| Local_feature_74 | No: anonymised Elliptic feature |
| Local_feature_75 | No: anonymised Elliptic feature |
| Local_feature_76 | No: anonymised Elliptic feature |
| Local_feature_77 | No: anonymised Elliptic feature |
| Local_feature_78 | No: anonymised Elliptic feature |
| Local_feature_79 | No: anonymised Elliptic feature |
| Local_feature_80 | No: anonymised Elliptic feature |
| Local_feature_81 | No: anonymised Elliptic feature |
| Local_feature_82 | No: anonymised Elliptic feature |
| Local_feature_83 | No: anonymised Elliptic feature |
| Local_feature_84 | No: anonymised Elliptic feature |
| Local_feature_85 | No: anonymised Elliptic feature |
| Local_feature_86 | No: anonymised Elliptic feature |
| Local_feature_87 | No: anonymised Elliptic feature |
| Local_feature_88 | No: anonymised Elliptic feature |
| Local_feature_89 | No: anonymised Elliptic feature |
| Local_feature_90 | No: anonymised Elliptic feature |
| Local_feature_91 | No: anonymised Elliptic feature |
| Local_feature_92 | No: anonymised Elliptic feature |
| Local_feature_93 | No: anonymised Elliptic feature |
| Aggregate_feature_1 | No: anonymised Elliptic feature |
| Aggregate_feature_2 | No: anonymised Elliptic feature |
| Aggregate_feature_3 | No: anonymised Elliptic feature |
| Aggregate_feature_4 | No: anonymised Elliptic feature |
| Aggregate_feature_5 | No: anonymised Elliptic feature |
| Aggregate_feature_6 | No: anonymised Elliptic feature |
| Aggregate_feature_7 | No: anonymised Elliptic feature |
| Aggregate_feature_8 | No: anonymised Elliptic feature |
| Aggregate_feature_9 | No: anonymised Elliptic feature |
| Aggregate_feature_10 | No: anonymised Elliptic feature |
| Aggregate_feature_11 | No: anonymised Elliptic feature |
| Aggregate_feature_12 | No: anonymised Elliptic feature |
| Aggregate_feature_13 | No: anonymised Elliptic feature |
| Aggregate_feature_14 | No: anonymised Elliptic feature |
| Aggregate_feature_15 | No: anonymised Elliptic feature |
| Aggregate_feature_16 | No: anonymised Elliptic feature |
| Aggregate_feature_17 | No: anonymised Elliptic feature |
| Aggregate_feature_18 | No: anonymised Elliptic feature |
| Aggregate_feature_19 | No: anonymised Elliptic feature |
| Aggregate_feature_20 | No: anonymised Elliptic feature |
| Aggregate_feature_21 | No: anonymised Elliptic feature |
| Aggregate_feature_22 | No: anonymised Elliptic feature |
| Aggregate_feature_23 | No: anonymised Elliptic feature |
| Aggregate_feature_24 | No: anonymised Elliptic feature |
| Aggregate_feature_25 | No: anonymised Elliptic feature |
| Aggregate_feature_26 | No: anonymised Elliptic feature |
| Aggregate_feature_27 | No: anonymised Elliptic feature |
| Aggregate_feature_28 | No: anonymised Elliptic feature |
| Aggregate_feature_29 | No: anonymised Elliptic feature |
| Aggregate_feature_30 | No: anonymised Elliptic feature |
| Aggregate_feature_31 | No: anonymised Elliptic feature |
| Aggregate_feature_32 | No: anonymised Elliptic feature |
| Aggregate_feature_33 | No: anonymised Elliptic feature |
| Aggregate_feature_34 | No: anonymised Elliptic feature |
| Aggregate_feature_35 | No: anonymised Elliptic feature |
| Aggregate_feature_36 | No: anonymised Elliptic feature |
| Aggregate_feature_37 | No: anonymised Elliptic feature |
| Aggregate_feature_38 | No: anonymised Elliptic feature |
| Aggregate_feature_39 | No: anonymised Elliptic feature |
| Aggregate_feature_40 | No: anonymised Elliptic feature |
| Aggregate_feature_41 | No: anonymised Elliptic feature |
| Aggregate_feature_42 | No: anonymised Elliptic feature |
| Aggregate_feature_43 | No: anonymised Elliptic feature |
| Aggregate_feature_44 | No: anonymised Elliptic feature |
| Aggregate_feature_45 | No: anonymised Elliptic feature |
| Aggregate_feature_46 | No: anonymised Elliptic feature |
| Aggregate_feature_47 | No: anonymised Elliptic feature |
| Aggregate_feature_48 | No: anonymised Elliptic feature |
| Aggregate_feature_49 | No: anonymised Elliptic feature |
| Aggregate_feature_50 | No: anonymised Elliptic feature |
| Aggregate_feature_51 | No: anonymised Elliptic feature |
| Aggregate_feature_52 | No: anonymised Elliptic feature |
| Aggregate_feature_53 | No: anonymised Elliptic feature |
| Aggregate_feature_54 | No: anonymised Elliptic feature |
| Aggregate_feature_55 | No: anonymised Elliptic feature |
| Aggregate_feature_56 | No: anonymised Elliptic feature |
| Aggregate_feature_57 | No: anonymised Elliptic feature |
| Aggregate_feature_58 | No: anonymised Elliptic feature |
| Aggregate_feature_59 | No: anonymised Elliptic feature |
| Aggregate_feature_60 | No: anonymised Elliptic feature |
| Aggregate_feature_61 | No: anonymised Elliptic feature |
| Aggregate_feature_62 | No: anonymised Elliptic feature |
| Aggregate_feature_63 | No: anonymised Elliptic feature |
| Aggregate_feature_64 | No: anonymised Elliptic feature |
| Aggregate_feature_65 | No: anonymised Elliptic feature |
| Aggregate_feature_66 | No: anonymised Elliptic feature |
| Aggregate_feature_67 | No: anonymised Elliptic feature |
| Aggregate_feature_68 | No: anonymised Elliptic feature |
| Aggregate_feature_69 | No: anonymised Elliptic feature |
| Aggregate_feature_70 | No: anonymised Elliptic feature |
| Aggregate_feature_71 | No: anonymised Elliptic feature |
| Aggregate_feature_72 | No: anonymised Elliptic feature |
| in_txs_degree | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| out_txs_degree | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| total_BTC | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| fees | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| size | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| num_input_addresses | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| num_output_addresses | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| in_BTC_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| in_BTC_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| in_BTC_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| in_BTC_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| in_BTC_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| out_BTC_min | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| out_BTC_max | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| out_BTC_mean | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| out_BTC_median | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
| out_BTC_total | Concept yes, values no: Elliptic++ counts only inside its own subgraph and period |
