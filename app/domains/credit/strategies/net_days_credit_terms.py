from datetime import date, timedelta
from app.domains.credit.strategies.credit_terms import CreditTerms

class NetDaysCreditTerms(CreditTerms):
    def __init__(self, days: int):
        self.days = days
        
    def compute_due_date(self, from_date: date) -> date:
        return from_date + timedelta(days=self.days)
        
    def compute_next_statement_date(self, period_end: date) -> date:
        return self.compute_due_date(period_end)
