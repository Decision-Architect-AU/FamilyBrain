FROM joefagan/incubator-age-viewer:latest

# Replace slow Cypher-traversal meta queries with fast pg_catalog lookups.
# The original MATCH ()-[V]-() scans every edge bidirectionally and times out
# on large graphs. reltuples from pg_class is instant and accurate enough.
# Note: getNodes/getEdges call util.format(sql, graph, graph) — two args for one %s.
# util.format appends extra args with a space, which would land after the semicolon
# as invalid SQL. The trailing -- %s comment consumes the second arg safely.
RUN node -e " \
const fs = require('fs'); \
const nodes = \"SELECT l.name as label, GREATEST(0, pc.reltuples::INTEGER) as cnt\nFROM ag_catalog.ag_label l\nJOIN pg_class pc ON pc.oid = l.relation\nWHERE l.graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = '%s')\nAND l.kind = 'v'\nAND l.name != '_ag_label_vertex';\n-- %s\n\"; \
const edges = \"SELECT l.name as label, GREATEST(0, pc.reltuples::INTEGER) as cnt\nFROM ag_catalog.ag_label l\nJOIN pg_class pc ON pc.oid = l.relation\nWHERE l.graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = '%s')\nAND l.kind = 'e'\nAND l.name != '_ag_label_edge';\n-- %s\n\"; \
fs.writeFileSync('/app/backend/sql/meta_nodes/AGE.sql', nodes); \
fs.writeFileSync('/app/backend/sql/meta_edges/AGE.sql', edges); \
console.log('SQL patches applied'); \
"
