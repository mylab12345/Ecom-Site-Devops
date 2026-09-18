from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql://ecom:ecom123@postgres:5432/identity_db"
    secret_key: str = "super-secret-jwt-key-change-in-production-32chars"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    service_name: str = "identity-service"
    jaeger_host: str = "jaeger"
    jaeger_port: int = 6831

    class Config:
        env_file = ".env"

settings = Settings()
