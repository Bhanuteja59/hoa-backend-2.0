from datetime import datetime, timedelta
from jose import jwt, JWTError

from app.core.config import settings
from app.core.errors import AppError
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------
# Password Hashing + Verify
# ---------------------------
def hash_password(password: str) -> str:
    return pwd_context.hash(password[:72])

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# ---------------------------
# Access Token Create / Decode
# ---------------------------
def create_access_token(*, user_id: str, tenant_id: str, roles: list[str] | None = None, claims: dict | None = None):
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,          # user id
        "tid": tenant_id,        # tenant id
        "roles": roles or [],    # role list
        "exp": expire,           # expiry time
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "pv": claims.get("pv") if claims else None, # password verification snippet
    }
    if claims:
        payload.update(claims)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str):
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        return payload
    except JWTError:
        raise AppError(code="AUTH_INVALID", message="Invalid token", status_code=401)
