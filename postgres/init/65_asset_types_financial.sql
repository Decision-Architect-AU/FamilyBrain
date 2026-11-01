-- Three kinds of thing the asset table could not hold.
--
-- asset_type was a closed list of physical and service things: vehicles,
-- properties, medications, subscriptions, companies, trusts. Income, loans and
-- investments had nowhere to live, so a mortgage survived as four facts on a
-- property and income did not exist at all — which is why no budget could be
-- produced from any of it.
--
--   income      what comes in: salary, rent received where it is not tied to
--               a property row, dividends, government support payments.
--   loan        what is owed and what it costs to service, with the property
--               or asset it is secured against.
--   investment  what is held for its value or yield: shares, managed funds,
--               anything that is neither a house nor a car.
--
-- Added to the existing list rather than replacing it: every current row keeps
-- its type, and the check still refuses a typo.
ALTER TABLE personal.asset DROP CONSTRAINT IF EXISTS asset_asset_type_check;
ALTER TABLE personal.asset
    ADD CONSTRAINT asset_asset_type_check CHECK (asset_type = ANY (ARRAY[
        'vehicle', 'medication', 'property', 'subscription', 'person',
        'device', 'pet', 'company', 'trust', 'routine', 'government_support',
        'income', 'loan', 'investment'
    ]));
