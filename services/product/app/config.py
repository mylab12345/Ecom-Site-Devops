from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    database_url: str = "postgresql://ecom:ecom123@postgres:5432/product_db"
    service_name: str = "product-service"
    inventory_service_url: str = "http://inventory:8003"
    class Config:
        env_file = ".env"
settings = Settings()
