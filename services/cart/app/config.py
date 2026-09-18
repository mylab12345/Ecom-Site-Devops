from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    redis_url: str = "redis://redis:6379/0"
    service_name: str = "cart-service"
    product_service_url: str = "http://product:8002"
    class Config:
        env_file=".env"
settings=Settings()
