from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    database_url: str = "postgresql://ecom:ecom123@postgres:5432/review_db"
    service_name: str = "review-service"
    product_service_url: str = "http://product:8002"
    order_service_url: str = "http://order:8005"
    class Config:
        env_file=".env"
settings=Settings()
