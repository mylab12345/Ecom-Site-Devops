from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    database_url: str = "postgresql://ecom:ecom123@postgres:5432/order_db"
    service_name: str = "order-service"
    product_service_url: str = "http://product:8002"
    inventory_service_url: str = "http://inventory:8003"
    payment_service_url: str = "http://payment:8006"
    cart_service_url: str = "http://cart:8004"
    shipping_service_url: str = "http://shipping:8007"
    notification_service_url: str = "http://notification:8008"
    class Config:
        env_file=".env"
settings=Settings()
