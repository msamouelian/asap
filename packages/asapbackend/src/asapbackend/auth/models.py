"""Auth-related Pydantic models."""

from pydantic import BaseModel


class AuthUser(BaseModel):
    id: str          # asap_user.id UUID — FK in conversations, prompts, etc.
    keycloak_id: str  # Keycloak sub claim
    email: str
    full_name: str
    role: str        # 'admin' | 'user'
    status: str      # 'active' | 'inactive'

    def has_role(self, role: str) -> bool:
        """Admin implicitly satisfies the 'user' role requirement."""
        if role == "user":
            return self.role in ("user", "admin")
        return self.role == role
