from abc import ABC, abstractmethod
from decimal import Decimal
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

class TaxCalculation(ABC):
    """
    Base Strategy for calculating the net tax payable by the shop to the government.
    This abstraction allows supporting different regional tax regimes (e.g., GST, VAT)
    while keeping the core reports service decoupled from specific formulas.
    """

    @abstractmethod
    async def calculate_tax_collected(self, db: AsyncSession, shop_id: int, period_start: datetime, period_end: datetime) -> Decimal:
        """
        Tax collected by the shop from customers on sales (Bills).
        This is a liability: money the shop has collected on behalf of, and owes to, the government.
        """
        pass

    @abstractmethod
    async def calculate_tax_paid(self, db: AsyncSession, shop_id: int, period_start: datetime, period_end: datetime) -> Decimal:
        """
        Tax paid by the shop on its own business expenses (Expenses).
        This acts as a credit: the shop can deduct this from what it owes the government.
        """
        pass

    def calculate_net_payable(self, tax_collected: Decimal, tax_paid: Decimal) -> Decimal:
        """
        Net Payable is the final amount the shop must remit to the tax authority.
        Formula: (Tax collected on sales) - (Tax paid on expenses)
        """
        return tax_collected - tax_paid
