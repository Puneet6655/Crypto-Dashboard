# Elliptic2 subgraph models

Generated 2026-10-31 00:02 UTC by training/elliptic2.py.
121,810 labelled subgraphs (2,763 suspicious), random 80/10/10 split as in the paper
(test: 12,054 subgraphs, 248 suspicious). One row per subgraph: size and shape, node
features, subgraph-edge features, and each node's background context (degree and mean edge features).
Threshold picked on the validation split. Paper baselines did not use node/edge features.

| Model | PR-AUC | ROC-AUC | F1 (suspicious) | F1 (micro, as in paper) |
| --- | --- | --- | --- | --- |
| XGBoost | 0.721 | 0.961 | 0.687 | 0.989 |
| Random Forest | 0.650 | 0.948 | 0.596 | 0.984 |
| GLASS (paper, test) | 0.208 | 0.889 | n/a | 0.933 |
| GNN-Seg (paper, test) | 0.026 | 0.537 | n/a | 0.398 |

Top XGBoost features: node_in_mean_feat#12_max (0.050), node_in_mean_feat#60_max (0.022), node_feat#28_max (0.022), node_in_mean_feat#32_max (0.019), node_in_mean_feat#6_mean (0.017), node_feat#29_max (0.017), node_in_mean_feat#15_mean (0.012), node_in_mean_feat#26_max (0.009), edge_feat#70_max (0.008), node_in_mean_feat#37_max (0.007)

Elliptic2 nodes are Elliptic's private address clusters with binned, anonymised features, so this model
cannot score live mempool.space lookups. It answers a different question: is this money-flow path suspicious?
