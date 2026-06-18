-- Seed data: starter themes and frameworks for decision_architect
-- Edit to match your actual positioning pillars before first run

INSERT INTO decision_architect.theme (name, description, priority, publish_cadence) VALUES
    ('Property as Decision Architecture',
     'How structured decision frameworks remove emotion from property acquisition and create repeatable outcomes',
     1, 'weekly'),
    ('NDIS Housing & SDA',
     'Specialist Disability Accommodation — investment case, design requirements, and participant impact',
     1, 'weekly'),
    ('Deal Analysis & Feasibility',
     'The numbers behind property deals — yield, growth, cash flow, and how to stress-test assumptions',
     2, 'fortnightly'),
    ('Portfolio Construction',
     'How individual deals compound into a portfolio strategy — sequencing, leverage, and risk management',
     2, 'fortnightly'),
    ('Mindset & Process',
     'The operating system behind consistent deal flow — routines, systems, and avoiding common traps',
     3, 'monthly');

INSERT INTO decision_architect.framework (name, description, theme_id) VALUES
    ('Deal Scoring Matrix',
     'Weighted criteria for evaluating any opportunity against portfolio objectives',
     (SELECT id FROM decision_architect.theme WHERE name = 'Deal Analysis & Feasibility')),
    ('SDA Feasibility Checklist',
     'Step-by-step feasibility for Specialist Disability Accommodation projects',
     (SELECT id FROM decision_architect.theme WHERE name = 'NDIS Housing & SDA')),
    ('3-Filter Acquisition Process',
     'Macro → suburb → deal: the three-layer filter applied before any offer is made',
     (SELECT id FROM decision_architect.theme WHERE name = 'Property as Decision Architecture'));
