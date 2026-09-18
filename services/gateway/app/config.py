from pydantic_settings import BaseSettings
from typing import Dict

class Settings(BaseSettings):
    service_name: str = "api-gateway"
    secret_key: str = "super-secret-jwt-key-change-in-production-32chars"
    algorithm: str = "HS256"
    # Internal DNS service map (K8s + docker-compose)
    services: Dict[str, str] = {
        "identity": "http://identity:8001",
        "product": "http://product:8002",
        "inventory": "http://inventory:8003",
        "cart": "http://cart:8004",
        "order": "http://order:8005",
        "payment": "http://payment:8006",
        "shipping": "http://shipping:8007",
        "notification": "http://notification:8008",
        "review": "http://review:8009",
    }
    # Optional: enable auth enforcement for certain routes
    enforce_auth: bool = False
    class Config:
        env_file=".env"
settings=Settings()
