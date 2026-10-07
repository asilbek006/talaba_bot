"""Qo'lda to'lov tizimi.

Foydalanuvchi "Sotib olish"ni bosadi → bot karta raqami va summani ko'rsatadi → foydalanuvchi
pul o'tkazadi va chek (screenshot) yuboradi → admin tekshiradi → "Tasdiqlash" bosilganda Pro
paket beriladi.

Karta raqami FAQAT `.env` da (`PAYMENT_CARD`, `PAYMENT_HOLDER`, `PAYMENT_AMOUNT`).
Hech qachon kodga, README ga yoki GitHub ga tushmaydi (`.gitignore` orqali himoyalangan).
"""
import config


def is_ready() -> bool:
    return bool(config.PAYMENT_CARD)


def card_info() -> dict:
    return {
        "card": config.PAYMENT_CARD,
        "holder": config.PAYMENT_HOLDER,
        "amount": config.PAYMENT_AMOUNT,
    }