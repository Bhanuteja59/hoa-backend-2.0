from __future__ import annotations
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    tenant_id: str
    roles: List[str]


# Default ACLs for users with permissions
def allowed_acls(ctx: AuthContext) -> List[str]:
    """
    Decide which document ACLs the user may see.
    Adjust this logic later when roles expand.
    """
    # Platform admin sees everything
    if "*" in ctx.roles:
        return ["RESIDENT_VISIBLE", "BOARD_ONLY"]

    # Board users see all
    if "BOARD" in ctx.roles or "ADMIN" in ctx.roles or "BOARD_MEMBER" in ctx.roles:
        return ["RESIDENT_VISIBLE", "BOARD_ONLY"]

    # Normal users only see resident visible
    return ["RESIDENT_VISIBLE"]


def require_perm(ctx: AuthContext, perm: str):
    """
    Tiny permission checker - expand later.
    """
    if "*" in ctx.roles:
        return True

    if perm not in ctx.roles:
        raise PermissionError(f"Missing permission: {perm}")

    return True
