-- Backfill confidence on existing edges (created before edge confidence existed).
-- Run once against personal_graph. New edges get confidence at creation time via
-- ON CREATE SET in ingestor/src/graph.py — this only covers the historical backlog.
-- Execute via: psql -U geoff -d openclaw -c "LOAD 'age'; SET search_path = ag_catalog, \"$user\", public; SELECT * FROM cypher('personal_graph', $$ <query> $$) AS (r agtype);"

-- Email-derived edges (low confidence — unverified extraction)
SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:MENTIONS]->() WHERE r.confidence IS NULL
    SET r.confidence = 40
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:LINKED_TO]->() WHERE r.confidence IS NULL
    SET r.confidence = 40
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:ASSERTS]->() WHERE r.confidence IS NULL
    SET r.confidence = 40
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:FROM_FRAMEWORK]->() WHERE r.confidence IS NULL
    SET r.confidence = 40
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:APPLIES_TO]->() WHERE r.confidence IS NULL
    SET r.confidence = 40
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:RELATES_TO]->() WHERE r.confidence IS NULL
    SET r.confidence = 40
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:FROM]->() WHERE r.confidence IS NULL
    SET r.confidence = 40
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:AUTHORED_BY]->() WHERE r.confidence IS NULL
    SET r.confidence = 40
    RETURN count(r)
$$) AS (r agtype);

-- Manual/curated concept relationships
SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:SYNONYM_OF]->() WHERE r.confidence IS NULL
    SET r.confidence = 65
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:ANTONYM_OF]->() WHERE r.confidence IS NULL
    SET r.confidence = 65
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:PART_OF]->() WHERE r.confidence IS NULL
    SET r.confidence = 65
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:RELATED_TO]->() WHERE r.confidence IS NULL
    SET r.confidence = 65
    RETURN count(r)
$$) AS (r agtype);

-- Structural edges (derived from validated relational rows — highest confidence)
SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:TRAVEL_TO]->() WHERE r.confidence IS NULL
    SET r.confidence = 90
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:TRAVEL_FROM]->() WHERE r.confidence IS NULL
    SET r.confidence = 90
    RETURN count(r)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH ()-[r:HAS_ASSET]->() WHERE r.confidence IS NULL
    SET r.confidence = 90
    RETURN count(r)
$$) AS (r agtype);
