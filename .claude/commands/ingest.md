User explicitly invoked /ingest — this is the user-initiated exception to
the "do not ingest" rule. Proceed with the full extraction protocol from
docs/SKILL.md:

1. Read the document in full
2. Section inventory — list every ##-level heading as your coverage checklist
3. First pass — extract nodes (label, type, description, importance). Use
   `brain_find` to check for existing equivalents before minting new ids.
4. Second pass — extract relations. Prefer causal types (causes / prevents /
   enables / contradicts). Each rel needs evidence explaining the mechanism.
5. Save the JSON payload to `./projects/<project>/<doc_id>.json`
   (relative to the brAIn repo root)
6. Run `brain check <path>` — fix any errors it reports, re-run until clean
7. Run `brain ingest <path>` — confirm post-ingest health summary looks OK
8. Step 7 of the workflow: gap verification. Query the doc_id nodes, re-read
   the source section by section, decide whether any section is unrepresented

Document to ingest: $ARGUMENTS
