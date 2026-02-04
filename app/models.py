from sqlalchemy import Column, Integer, String, DateTime, Float, Text, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base

class Ticket(Base):
    __tablename__ = "tickets"
    
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String(50), unique=True, index=True, nullable=False)
    transaction_id = Column(String(100))
    
    customer_name = Column(String(200), nullable=False)
    customer_email = Column(String(200))
    customer_phone = Column(String(50))
    
    ticket_type = Column(String(100), default="Standard")
    event_date = Column(String(20))
    event_name = Column(String(200))
    price = Column(Float, default=0)
    subtotal = Column(Float, default=0)  # Оригинальная цена ДО скидки
    discount = Column(Float, default=0)
    payment_amount = Column(Float, default=0)
    promocode = Column(String(50))
    
    qr_token = Column(String(100), unique=True, index=True)
    qr_signature = Column(String(100))
    
    # IMPREZA: Новые поля для мультитенантности
    country_code = Column(String(10), nullable=True, index=True)
    city_name = Column(String(100), nullable=True, index=True)
    club_id = Column(Integer, nullable=True, index=True)
    
    # БАГ FIX #2: Видимость для менеджеров
    visible_to_managers = Column(Boolean, default=True, index=True)
    
    # QUANTITY + EXPIRY: Билеты на несколько человек
    quantity = Column(Integer, default=1)  # Количество человек на билет (1 = обычный билет)
    
    status = Column(String(20), default="valid", index=True)
    scan_count = Column(Integer, default=0)
    first_scan_at = Column(DateTime)
    last_scan_at = Column(DateTime)
    scanned_by = Column(String(100))
    
    telegram_message_id = Column(Integer)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    scan_history = relationship("ScanHistory", back_populates="ticket")


class ScanHistory(Base):
    __tablename__ = "scan_history"
    
    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"))
    order_id = Column(String(50), index=True)
    
    # IMPREZA: Добавлено для multitenancy
    club_id = Column(Integer, nullable=True, index=True)
    
    # IMPREZA: Скрытие от менеджеров
    hidden_for_manager = Column(Boolean, default=False, index=True)
    
    scan_time = Column(DateTime, server_default=func.now())
    scan_result = Column(String(20))
    scanner_id = Column(String(100))
    notes = Column(Text)
    
    ticket = relationship("Ticket", back_populates="scan_history")


class Club(Base):
    """Модель клуба/города для IMPREZA"""
    __tablename__ = "clubs"
    
    club_id = Column(Integer, primary_key=True, index=True)
    country_id = Column(Integer, nullable=False)
    city_name = Column(String(100), nullable=False)  # Русское название
    city_english = Column(String(100), nullable=False)  # Английское название
    login = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    plain_password = Column(String(255), nullable=True)  # Plain-text пароль для показа в админке
    is_active = Column(Boolean, default=True)


class DeletedTicket(Base):
    """Архив удалённых билетов — для восстановления и аудита"""
    __tablename__ = "deleted_tickets"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Оригинальные поля из tickets
    original_id = Column(Integer, nullable=False)  # ID билета до удаления
    order_id = Column(String(50), nullable=False, index=True)
    transaction_id = Column(String(100))
    
    customer_name = Column(String(200), nullable=False)
    customer_email = Column(String(200), index=True)
    customer_phone = Column(String(50))
    
    ticket_type = Column(String(100), default="Standard")
    event_date = Column(String(20))
    event_name = Column(String(200), index=True)
    price = Column(Float, default=0)
    subtotal = Column(Float, default=0)
    discount = Column(Float, default=0)
    payment_amount = Column(Float, default=0)
    promocode = Column(String(50))
    
    qr_token = Column(String(100))
    qr_signature = Column(String(100))
    
    country_code = Column(String(10))
    city_name = Column(String(100), index=True)
    club_id = Column(Integer)
    
    visible_to_managers = Column(Boolean, default=True)
    quantity = Column(Integer, default=1)
    
    status = Column(String(20), default="valid")
    scan_count = Column(Integer, default=0)
    first_scan_at = Column(DateTime)
    last_scan_at = Column(DateTime)
    scanned_by = Column(String(100))
    
    telegram_message_id = Column(Integer)
    
    original_created_at = Column(DateTime)  # Когда билет был создан
    original_updated_at = Column(DateTime)  # Когда билет был обновлён
    
    # Поля архива
    deleted_at = Column(DateTime, server_default=func.now(), index=True)
    deleted_by = Column(String(100))  # Кто удалил
    delete_reason = Column(String(500))  # Причина удаления
