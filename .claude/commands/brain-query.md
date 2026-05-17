Query the brAIn knowledge graph to answer the following question.

Use these commands as needed:
- `brain.py find <topic>` — find relevant nodes
- `brain.py causes <node_id>` — what causes X
- `brain.py effects <node_id>` — what does X lead to
- `brain.py paths <a> <b>` — how A relates to B
- `brain.py show <node_id>` — full node detail
- `brain.py query "<cypher>"` — custom traversal

Cite the `evidence` fields from the output. Flag any chain built on edges with confidence below 0.6.

Question: $ARGUMENTS
