Ingest the following document into the brAIn knowledge graph.

Follow the ingestion workflow from the skill:
1. Read the document in full
2. Extract 10–50 nodes with comprehensive descriptions
3. Extract causal relations with rich evidence
4. Run `brain.py find` for anti-duplicate checks before creating nodes
5. Save the JSON payload to `examples/$ARGUMENTS.json` and run `brain.py ingest`
6. Verify with `brain.py stats` and `brain.py audit`

Document to ingest: $ARGUMENTS
