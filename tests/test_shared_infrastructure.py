import pytest
from datetime import date, datetime, timedelta
from decimal import Decimal
from sqlalchemy import Column, Integer, String
from app.core.base import Base
from app.shared.repository import BaseRepository
from app.shared.sequence import next_sequence_number
from app.domains.credit.strategies.weekly_credit_terms import WeeklyCreditTerms
from app.domains.credit.strategies.monthly_credit_terms import MonthlyCreditTerms
from app.domains.credit.strategies.net_days_credit_terms import NetDaysCreditTerms
from app.domains.credit.strategies.factory import get_payment_term_strategy
from app.domains.reports.strategies.gst_tax import GstTax
from app.shared.models import Bill, Shop

class ThrowawayTestModel(Base):
    __tablename__ = "throwaway_test_model"
    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String, unique=True)
    name = Column(String)

class SequenceTestModel(Base):
    __tablename__ = "sequence_test_model"
    id = Column(Integer, primary_key=True, autoincrement=True)
    seq_num = Column(String)
    shop_id = Column(Integer)

class ThrowawayRepository(BaseRepository[ThrowawayTestModel]):
    def __init__(self, db):
        super().__init__(db, ThrowawayTestModel)

@pytest.mark.asyncio
async def test_base_repository_crud(db_session):
    async with db_session.bind.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    repo = ThrowawayRepository(db_session)
    
    entity = ThrowawayTestModel(slug="test-slug", name="Test")
    added = await repo.add(entity)
    added_id = added.id
    assert added_id is not None
    await db_session.commit()
    
    fetched = await repo.get_by_id(added_id)
    assert fetched.name == "Test"
    
    fetched_slug = await repo.get_by_slug("test-slug")
    assert fetched_slug.id == added.id
    
    results = await repo.list(name="Test")
    assert len(results) == 1

@pytest.mark.asyncio
async def test_next_sequence_number(db_session):
    async with db_session.bind.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    seq1 = await next_sequence_number(
        db_session,
        model=SequenceTestModel,
        sequence_column=SequenceTestModel.seq_num,
        scope_filters={"shop_id": 1},
        prefix="STMT",
        date_key="202607",
        padding=4
    )
    assert seq1 == "STMT-202607-0001"
    
    db_session.add(SequenceTestModel(seq_num=seq1, shop_id=1))
    await db_session.commit()
    
    seq2 = await next_sequence_number(
        db_session,
        model=SequenceTestModel,
        sequence_column=SequenceTestModel.seq_num,
        scope_filters={"shop_id": 1},
        prefix="STMT",
        date_key="202607",
        padding=4
    )
    assert seq2 == "STMT-202607-0002"
    
    seq3 = await next_sequence_number(
        db_session,
        model=SequenceTestModel,
        sequence_column=SequenceTestModel.seq_num,
        scope_filters={"shop_id": 2},
        prefix="STMT",
        date_key="202607",
        padding=4
    )
    assert seq3 == "STMT-202607-0001"

def test_payment_term_strategy():
    strategy = get_payment_term_strategy("weekly", 0) # 0=Monday
    assert isinstance(strategy, WeeklyCreditTerms)
    
    # 2026-07-01 is a Wednesday
    due = strategy.compute_due_date(date(2026, 7, 1)) 
    assert due.weekday() == 0
    assert due == date(2026, 7, 6) # Next Monday
    assert strategy.compute_next_statement_date(date(2026, 7, 1)) == date(2026, 7, 6)

    # Test false branch for weekly logic (days_ahead > 0)
    strategy_fri = get_payment_term_strategy("weekly", 4) # Friday
    due_fri = strategy_fri.compute_due_date(date(2026, 7, 6)) # Monday
    assert due_fri == date(2026, 7, 10)

    with pytest.raises(ValueError):
        get_payment_term_strategy("unknown", 0)
        
    strategy = get_payment_term_strategy("net_days", 15)
    assert strategy.compute_due_date(date(2026, 7, 1)) == date(2026, 7, 16)
    assert strategy.compute_next_statement_date(date(2026, 7, 1)) == date(2026, 7, 16)
    
    strategy = get_payment_term_strategy("monthly", 31)
    due = strategy.compute_due_date(date(2026, 6, 15)) 
    assert due.day == 31
    assert due.month == 7 
    assert strategy.compute_next_statement_date(date(2026, 6, 15)) == due
    
    # Test December rollover
    due_dec = strategy.compute_due_date(date(2026, 12, 15))
    assert due_dec.year == 2027
    assert due_dec.month == 1
    assert due_dec.day == 31
    assert strategy.compute_next_statement_date(date(2026, 12, 15)) == due_dec

def test_abstract_credit_terms():
    from app.domains.credit.strategies.credit_terms import CreditTerms
    class DummyTerms(CreditTerms):
        def compute_due_date(self, from_date: date) -> date:
            super().compute_due_date(from_date)
            return from_date
        def compute_next_statement_date(self, period_end: date) -> date:
            super().compute_next_statement_date(period_end)
            return period_end
    dummy = DummyTerms()
    dummy.compute_due_date(date(2026, 1, 1))
    dummy.compute_next_statement_date(date(2026, 1, 1))

@pytest.mark.asyncio
async def test_gst_tax_strategy(db_session):
    strategy = GstTax()
    
    # This will return 0.00 since there are no completed bills
    output = await strategy.calculate_tax_collected(db_session, shop_id=1, period_start=datetime(2026, 7, 1), period_end=datetime(2026, 7, 31))
    assert output == Decimal("0.00")
    
    # This will return 0.00 since Expense model isn't created yet (hits ImportError logic)
    input_tax = await strategy.calculate_tax_paid(db_session, shop_id=1, period_start=datetime(2026, 7, 1), period_end=datetime(2026, 7, 31))
    assert input_tax == Decimal("0.00")
    
    # Calculate net payable
    net = strategy.calculate_net_payable(Decimal("100.00"), Decimal("40.00"))
    assert net == Decimal("60.00")

@pytest.mark.asyncio
async def test_abstract_tax_calculation(db_session):
    from app.domains.reports.strategies.tax_calculation import TaxCalculation
    class DummyTax(TaxCalculation):
        async def calculate_tax_collected(self, db, shop_id, start, end):
            await super().calculate_tax_collected(db, shop_id, start, end)
            return Decimal("0.00")
        async def calculate_tax_paid(self, db, shop_id, start, end):
            await super().calculate_tax_paid(db, shop_id, start, end)
            return Decimal("0.00")
            
    dummy = DummyTax()
    await dummy.calculate_tax_collected(db_session, 1, datetime.now(), datetime.now())
    await dummy.calculate_tax_paid(db_session, 1, datetime.now(), datetime.now())
