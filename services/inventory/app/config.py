from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    database_url: str = "postgresql://ecom:ecom123@postgres:5432/inventory_db"
    service_name: str = "inventory-service"
    product_service_url: str = "http://product:8002"
    class Config:
        env_file=".env"
settings=Settings()
