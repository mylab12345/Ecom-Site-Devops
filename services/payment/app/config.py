from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    database_url: str = "postgresql://ecom:ecom123@postgres:5432/payment_db"
    service_name: str = "payment-service"
    order_service_url: str = "http://order:8005"
    notification_service_url: str = "http://notification:8008"
    class Config:
        env_file=".env"
settings=Settings()
