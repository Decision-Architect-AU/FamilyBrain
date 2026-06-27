-- Backfill collision_aware on existing event nodes by label.
-- Run once against personal_graph after Session 2.
-- Execute via: psql -U geoff -d openclaw -c "LOAD 'age'; SET search_path = ag_catalog, \"$user\", public; SELECT * FROM cypher('personal_graph', $$ <query> $$) AS (r agtype);"

-- Collision-aware labels (participate in conflict detection)
SELECT * FROM cypher('personal_graph', $$
    MATCH (n:Event) WHERE n.collision_aware IS NULL
    SET n.collision_aware = true
    RETURN count(n)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH (n:Appointment) WHERE n.collision_aware IS NULL
    SET n.collision_aware = true
    RETURN count(n)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH (n:SchoolEvent) WHERE n.collision_aware IS NULL
    SET n.collision_aware = true
    RETURN count(n)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH (n:PropertyEvent) WHERE n.collision_aware IS NULL
    SET n.collision_aware = true
    RETURN count(n)
$$) AS (r agtype);

-- Non-collision-aware labels (informational only)
SELECT * FROM cypher('personal_graph', $$
    MATCH (n:Reminder) WHERE n.collision_aware IS NULL
    SET n.collision_aware = false
    RETURN count(n)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH (n:Medication) WHERE n.collision_aware IS NULL
    SET n.collision_aware = false
    RETURN count(n)
$$) AS (r agtype);

SELECT * FROM cypher('personal_graph', $$
    MATCH (n:PublicHoliday) WHERE n.collision_aware IS NULL
    SET n.collision_aware = false
    RETURN count(n)
$$) AS (r agtype);

-- Set default attendance_mode on nodes missing it
SELECT * FROM cypher('personal_graph', $$
    MATCH (n:Event) WHERE n.attendance_mode IS NULL
    SET n.attendance_mode = "IN_PERSON"
    RETURN count(n)
$$) AS (r agtype);
