-- Let the dashboard read and edit the registers it now presents.
--
-- dashboard_ro could already read and write personal.asset and had no access
-- at all to personal.person, so the family, provider and contact pages failed
-- outright with "permission denied for table person". The asset pages failed
-- too, for a subtler reason: they name the provider on each row, which reads
-- person through a sub-select.
--
-- Granted deliberately narrowly:
--
--   SELECT, INSERT, UPDATE   what the pages do. Adding a contact, correcting
--                            a phone number, retiring an entry.
--   no DELETE                nothing on these pages deletes a row. Events,
--                            invoices and findings point at people and
--                            assets, so an entry is retired instead and the
--                            history that explains it stays readable. The
--                            role cannot delete even if a future page tried.
--   the sequence             an INSERT needs nextval on person_id_seq, and
--                            without it adding a contact fails at the point
--                            of saving, which is the worst place to find out.
GRANT SELECT, INSERT, UPDATE ON personal.person TO dashboard_ro;
GRANT USAGE, SELECT ON SEQUENCE personal.person_id_seq TO dashboard_ro;
GRANT USAGE, SELECT ON SEQUENCE personal.asset_id_seq TO dashboard_ro;

-- The budget totals come from the view, which does the normalising.
GRANT SELECT ON personal.asset_cashflow TO dashboard_ro;
