"""PropertyDealsQueryTool — researcher pulls live deal data to ground content in real examples."""
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from src.db import fetch_all
import json


class PropertyDealsInput(BaseModel):
    stage: str = Field(
        default="",
        description="Filter by deal stage: lead, due_diligence, offer, under_contract, settled. Empty = all active."
    )
    limit: int = Field(default=5)


class PropertyDealsQueryTool(BaseTool):
    name: str = "property_deals_query"
    description: str = (
        "Fetch recent property deals from the pipeline. "
        "Use to ground PR content in real deal examples (anonymised as needed by the writer)."
    )
    args_schema: type[BaseModel] = PropertyDealsInput

    def _run(self, stage: str = "", limit: int = 5) -> str:
        stage_clause = "AND d.stage = %s" if stage else "AND d.stage != 'dead'"
        params = [stage, limit] if stage else [limit]

        rows = fetch_all(
            f"""
            SELECT
                p.suburb, p.state, p.property_type,
                p.bedrooms, p.bathrooms, p.listing_price,
                d.stage, d.purchase_price, d.rental_yield,
                d.projected_growth, d.settlement_date, d.tags
            FROM property_deals.deal d
            JOIN property_deals.property p ON p.id = d.property_id
            WHERE 1=1 {stage_clause}
            ORDER BY d.updated_at DESC
            LIMIT %s
            """,
            params,
        )

        if not rows:
            return "No deals found matching criteria."

        # Anonymise suburb to general area for PR purposes
        result = []
        for r in rows:
            result.append({
                "area": f"{r['suburb']}, {r['state']}",
                "type": r["property_type"],
                "beds": r["bedrooms"],
                "stage": r["stage"],
                "purchase_price": float(r["purchase_price"]) if r["purchase_price"] else None,
                "rental_yield": f"{float(r['rental_yield'])*100:.2f}%" if r["rental_yield"] else None,
                "projected_growth": f"{float(r['projected_growth'])*100:.1f}%" if r["projected_growth"] else None,
                "settlement": str(r["settlement_date"]) if r["settlement_date"] else None,
                "tags": r["tags"] or [],
            })
        return json.dumps(result, indent=2)
