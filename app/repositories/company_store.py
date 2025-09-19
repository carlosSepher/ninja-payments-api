from __future__ import annotations

from typing import Optional
import hmac

from app.db.client import get_conn
from app.domain.models import Company


class PgCompanyStore:
    """Repository for company authentication data."""

    def get_by_id(self, company_id: int) -> Optional[Company]:
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, contact_email, api_token, active
                      FROM company
                     WHERE id = %s
                     LIMIT 1
                    """,
                    (company_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return Company(
                    id=int(row[0]),
                    name=str(row[1]),
                    contact_email=row[2],
                    api_token=str(row[3]),
                    active=bool(row[4]),
                )

    def validate_credentials(self, company_id: int, token: str) -> Company:
        company = self.get_by_id(company_id)
        if not company or not company.active:
            raise ValueError("Unknown or inactive company")
        if not token:
            raise ValueError("Missing company token")
        if not hmac.compare_digest(company.api_token, token):
            raise ValueError("Invalid company credentials")
        return company

    def list_companies(self) -> list[Company]:
        companies: list[Company] = []
        with get_conn() as conn:
            if conn is None:
                return companies
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, contact_email, api_token, active
                      FROM company
                     ORDER BY id ASC
                    """,
                )
                for row in cur.fetchall() or []:
                    companies.append(
                        Company(
                            id=int(row[0]),
                            name=str(row[1]),
                            contact_email=row[2],
                            api_token=str(row[3]),
                            active=bool(row[4]),
                        )
                    )
        return companies
